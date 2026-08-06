# app/auth_core/data_scope.py
# -*- coding: utf-8 -*-
"""集中式行级数据隔离（Row-Level Data Scope）

设计目标
--------
业务模块的项目级隔离必须由**一层**统一裁决，而不是在 ~168 个列表路由里各写各的过滤条件。
本模块在 SQLAlchemy ORM 执行层挂 `do_orm_execute` 钩子，对所有带 `project_id` 的业务模型
自动注入 `project_id IN (<当前用户可见项目>)`，做到：

  - 新增业务表/新增路由 **零改动** 自动纳入隔离（可扩展）
  - 漏写过滤条件不再等于越权（默认安全）
  - 与 AuthGateway 单一数据源一致（scope 由权限中台裁决，这里只负责执行）

两条执行路径
------------
  A. 实体加载（.all() / .first() / .get() / paginate().items / 关系预加载）
     -> with_loader_criteria，由 SQLAlchemy 负责放到正确位置（含 JOIN 的 ON 子句）
  B. 聚合查询（.count() / paginate().total / select(func.count())）
     -> SQLAlchemy 生成 `SELECT count(*) FROM (子查询)`，该语句 all_mappers 为空，
        loader criteria 无处可挂。必须改写语句本身，否则会出现
        「共 988 条、列表却是空的」这种错乱，且泄漏他项目数据量。
     -> _scope_plain_select() 处理：只对 FROM 的**最左主表**加 WHERE，
        绝不动 JOIN 右侧（避免把 LEFT JOIN 语义改成 INNER JOIN）。

边界（明确不参与隔离的表）
------------------------
  1. auth_core_*        权限中台自身（否则裁决时递归）
  2. sys_*              旧 RBAC 组织/角色/用户-项目映射（scope 计算依赖它）
  3. users              身份表（过滤它 = 登录直接挂）
  4. projects           特殊处理：按主键 id 过滤，而非 project_id

NULL 语义
---------
  project_id 列 **可空** 的表：NULL 视为「不属于任何项目 = 全局共享」，放行。
  project_id 列 **非空** 的表：严格过滤。
  规则由 schema 自身决定，不维护硬编码白名单。

逃生舱
------
  1. 灰度开关：环境变量 DATA_SCOPE_ENFORCE=false 全局关闭（回滚安全）
  2. 单查询豁免：Model.query.execution_options(skip_data_scope=True)
  3. 代码块豁免：with bypass_data_scope(): ...   （跨项目统计、对账、大屏汇总）
  4. 无请求上下文（CLI / ETL / 调度器）天然不拦截

版本：v0.2.0 / 2026-08-06
"""
import os
from contextlib import contextmanager

from flask import g, has_request_context
from sqlalchemy import Table, event, or_, select as sa_select
from sqlalchemy.orm import Session as SessionBase, with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.selectable import Alias, AliasedReturnsRows, Join, Select

# ---------------------------------------------------------------- 开关


def enforcing_enabled():
    """全局灰度开关。默认关闭，翻转前可用脚本充分验证。"""
    return os.environ.get('DATA_SCOPE_ENFORCE', 'false').lower() == 'true'


# ---------------------------------------------------------------- 排除名单

EXCLUDE_TABLES = {
    'users',            # 身份表
    'alembic_version',
}
EXCLUDE_PREFIXES = ('auth_core_', 'sys_')

PROJECT_TABLE = 'projects'

_REGISTRY = None        # {模型类: 'strict' | 'null_ok'}
_TABLE_RULES = None     # {表名: ('project_id', 'strict'|'null_ok') | ('id', 'pk')}
_PROJECT_CLS = None


def _build_registry(db):
    """扫描 ORM 元数据，登记所有需要隔离的业务模型/表。仅构建一次。"""
    global _REGISTRY, _TABLE_RULES, _PROJECT_CLS
    if _REGISTRY is not None:
        return _REGISTRY

    reg = {}
    rules = {}
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        table = mapper.local_table
        if table is None:
            continue
        name = table.name
        if name == PROJECT_TABLE:
            _PROJECT_CLS = cls
            rules[name] = ('id', 'pk')
            continue
        if name in EXCLUDE_TABLES or name.startswith(EXCLUDE_PREFIXES):
            continue
        col = table.columns.get('project_id')
        if col is None:
            continue
        mode = 'null_ok' if col.nullable else 'strict'
        reg[cls] = mode
        rules[name] = ('project_id', mode)

    _REGISTRY = reg
    _TABLE_RULES = rules
    return _REGISTRY


def scoped_model_names():
    """供审计/测试脚本读取当前纳管的模型名单"""
    if _REGISTRY is None:
        return []
    return sorted(c.__name__ for c in _REGISTRY)


# ---------------------------------------------------------------- 可见项目解析


def _resolve_allowed_projects():
    """返回 None 表示全量放行；返回 list 表示仅这些项目可见。

    结果按「当前用户 id」缓存在 g 内，一次请求只算一次。
    注意 g 挂在 **应用上下文** 上：脚本里若在同一个 app_context 内切换用户，
    必须按 user_id 判定缓存是否失效，否则会串号（生产每请求独立上下文不受影响）。
    """
    from flask_login import current_user

    try:
        uid = getattr(current_user, 'id', None)
    except Exception:
        uid = None

    cached = getattr(g, '_ds_cache', None)
    if cached is not None and cached[0] == uid:
        return cached[1]

    g._ds_resolving = True
    try:
        if not current_user or not getattr(current_user, 'is_authenticated', False):
            # 匿名：不拦截（公开路由由 login_required 自身把关，此处不改变既有行为）
            allowed = None
        else:
            try:
                is_admin = bool(current_user.is_admin())
            except Exception:
                is_admin = False
            if is_admin:
                allowed = None
            else:
                from app.services.permission_service import permission_service
                allowed = permission_service.get_user_allowed_projects(current_user)
                if allowed is not None:
                    allowed = [int(x) for x in allowed if x is not None]
    except Exception as exc:
        # 解析失败一律放行，绝不能因为隔离层把系统打挂；但必须留痕，否则等于静默失效
        try:
            from flask import current_app
            current_app.logger.error('[data_scope] 可见项目解析失败，本次放行: %s', exc, exc_info=True)
        except Exception:
            pass
        allowed = None
    finally:
        g._ds_resolving = False

    g._ds_cache = (uid, allowed)
    return allowed


@contextmanager
def bypass_data_scope():
    """代码块级豁免：跨项目统计、对账、大屏汇总等场景显式声明"""
    if not has_request_context():
        yield
        return
    prev = getattr(g, '_ds_bypass', False)
    g._ds_bypass = True
    try:
        yield
    finally:
        g._ds_bypass = prev


def current_allowed_projects():
    """对外只读接口：当前请求可见项目（None = 全部）"""
    if not has_request_context():
        return None
    return _resolve_allowed_projects()


# ---------------------------------------------------------------- 语句改写（聚合路径）


def _criteria_for(from_clause, allowed):
    """按表规则给 FromClause 生成过滤条件；不适用返回 None"""
    table = from_clause
    while isinstance(table, Alias) and isinstance(getattr(table, 'element', None), (Alias, Table)):
        table = table.element
    if not isinstance(table, Table):
        return None
    rule = (_TABLE_RULES or {}).get(table.name)
    if rule is None:
        return None
    colname, mode = rule
    col = from_clause.c.get(colname)
    if col is None:
        return None
    if mode == 'pk':
        return col.in_(allowed)
    if mode == 'null_ok':
        return or_(col.in_(allowed), col.is_(None))
    return col.in_(allowed)


def _leftmost_from(f):
    """取 FROM 的最左主表：JOIN 右侧一律不动，避免把外连接语义改成内连接"""
    while isinstance(f, Join):
        f = f.left
    return f


def _scope_plain_select(stmt, allowed):
    """给一个不含 ORM 实体的 Select 加上范围条件；无改动返回 None"""
    if not isinstance(stmt, Select):
        return None
    crits = []
    try:
        froms = stmt.get_final_froms()
    except Exception:
        return None
    for f in froms:
        c = _criteria_for(_leftmost_from(f), allowed)
        if c is not None:
            crits.append(c)
    if not crits:
        return None
    return stmt.where(*crits)


def _outer_refs_subquery(stmt, sub):
    """外层选择列是否引用了子查询的列（引用了就不能重建，放弃改写）"""
    subcols = set(sub.c)
    for col in stmt.selected_columns:
        for el in visitors.iterate(col):
            if el in subcols:
                return True
    return False


def _scope_aggregate(stmt, allowed):
    """处理 `SELECT count(*) FROM (SELECT ... FROM 业务表) AS anon_1`

    这是 Query.count() / Pagination.total 的固定形态，all_mappers 为空，
    loader criteria 挂不上，必须把 WHERE 打进内层子查询再重建外层。
    """
    if not isinstance(stmt, Select):
        return None
    try:
        froms = stmt.get_final_froms()
    except Exception:
        return None
    if len(froms) != 1:
        return None
    sub = froms[0]
    # Query.count() 走 from_self，实际形态是 Alias(Subquery(Select))，
    # 包装层数与类型都不稳定，统一按 AliasedReturnsRows 逐层拆到最内层 Select。
    if not isinstance(sub, AliasedReturnsRows):
        return None
    inner = sub
    for _ in range(5):
        if not isinstance(inner, AliasedReturnsRows):
            break
        inner = getattr(inner, 'element', None)
    if not isinstance(inner, Select):
        return None
    if _outer_refs_subquery(stmt, sub):
        return None
    new_inner = _scope_plain_select(inner, allowed)
    if new_inner is None:
        return None
    return sa_select(*stmt.selected_columns).select_from(new_inner.subquery())


# ---------------------------------------------------------------- 钩子

_INSTALLED = False


def install_data_scope(app, db):
    """在 create_app 中调用一次，挂载 ORM 层隔离钩子"""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    @event.listens_for(SessionBase, 'do_orm_execute')
    def _apply_data_scope(state):
        # 只管 ORM SELECT；列加载/关系懒加载由 with_loader_criteria 的传播机制覆盖
        if not state.is_select or state.is_column_load or state.is_relationship_load:
            return
        if state.execution_options.get('skip_data_scope'):
            return
        if not enforcing_enabled() or not has_request_context():
            return
        if getattr(g, '_ds_resolving', False) or getattr(g, '_ds_bypass', False):
            return

        allowed = _resolve_allowed_projects()
        if allowed is None:
            return

        registry = _build_registry(db)

        mappers = list(state.all_mappers or ())
        if mappers:
            # 路径 A：实体加载
            for mapper in mappers:
                cls = mapper.class_
                if _PROJECT_CLS is not None and cls is _PROJECT_CLS:
                    crit = cls.id.in_(allowed)
                else:
                    mode = registry.get(cls)
                    if mode is None:
                        continue
                    if mode == 'null_ok':
                        crit = or_(cls.project_id.in_(allowed), cls.project_id.is_(None))
                    else:
                        crit = cls.project_id.in_(allowed)
                state.statement = state.statement.options(
                    with_loader_criteria(cls, crit, include_aliases=True)
                )
            return

        # 路径 B：聚合/无实体查询（count、sum 等）
        try:
            rewritten = _scope_aggregate(state.statement, allowed)
            if rewritten is None:
                rewritten = _scope_plain_select(state.statement, allowed)
            if rewritten is not None:
                state.statement = rewritten
        except Exception as exc:
            try:
                from flask import current_app
                current_app.logger.error('[data_scope] 聚合语句改写失败，本次放行: %s', exc, exc_info=True)
            except Exception:
                pass

    with app.app_context():
        reg = _build_registry(db)
    app.logger.info('[data_scope] 已挂载行级隔离，纳管业务模型 %d 个，开关=%s',
                    len(reg), enforcing_enabled())
