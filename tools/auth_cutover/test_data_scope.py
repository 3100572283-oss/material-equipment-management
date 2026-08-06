# -*- coding: utf-8 -*-
"""集中式行级隔离验证：关闭态取基线 -> 打开态逐用户比对 -> 真值用原生 SQL 校验"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))

import os
from run import app
from app import db
from sqlalchemy import text
from flask_login import login_user, logout_user

from app.auth_core.models import AuthUser
from app.auth_core.gateway import AuthGateway
from app.auth_core.data_scope import scoped_model_names, bypass_data_scope, current_allowed_projects

app.config['WTF_CSRF_ENABLED'] = False
app.login_manager.session_protection = None

PASS = FAIL = 0


def ck(ok, msg, extra=''):
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  \u2705 %s %s' % (msg, extra))
    else:
        FAIL += 1
        print('  \u274c %s %s' % (msg, extra))


def hr(title):
    print('=' * 78)
    print(title)


# 抽样：有真实数据的表 + 关键业务表
# 注意 Category/Supplier/Material 是**公司级主数据**，按设计不参与 project_id 隔离，
# 仍留在抽样里是为了反向验证「它们必须保持全量可见」这条不变量。
SAMPLES = [
    ('Category', 'categories'),
    ('Supplier', 'suppliers'),
    ('StockIn', 'stock_ins'),
    ('Material', 'materials'),
    ('ProjectMaterial', 'project_material'),
    ('Inventory', 'inventory'),
    ('Contract', 'contracts'),
    ('ProjectSupplier', 'project_supplier'),
]

# 用于分页/逃生舱验证的**受隔离**表：必须有数据、且不同用户可见量不同
SCOPED_PROBE = ('ProjectSupplier', 'project_supplier')

USERS = ['admin', 'zhangwu', '19818623768', 'branch_test']


def is_managed(tname):
    """该表是否真的参与 project_id 行级隔离（由隔离层自己说了算，测试不硬编码）"""
    from app.auth_core.data_scope import _TABLE_RULES
    rule = (_TABLE_RULES or {}).get(tname)
    return bool(rule) and rule[0] == 'project_id'


def model_of(name):
    for mapper in db.Model.registry.mappers:
        if mapper.class_.__name__ == name:
            return mapper.class_
    return None


def counts_for(user):
    """在请求上下文内、以该用户身份统计 ORM 可见行数"""
    out = {}
    with app.test_request_context('/'):
        login_user(user)
        for mname, _t in SAMPLES:
            cls = model_of(mname)
            out[mname] = cls.query.count() if cls is not None else None
        from app.models import Project
        out['Project'] = Project.query.count()
        out['_allowed'] = current_allowed_projects()
        logout_user()
    return out


def sql_truth(table, allowed, nullable):
    # 未纳管的表（公司级主数据 / 身份表）期望值恒为全量
    if allowed is None or not is_managed(table):
        return db.session.execute(text('SELECT COUNT(*) FROM `%s`' % table)).scalar()
    if not allowed:
        base = 0 if not nullable else db.session.execute(
            text('SELECT COUNT(*) FROM `%s` WHERE project_id IS NULL' % table)).scalar()
        return base
    ids = ','.join(str(int(x)) for x in allowed)
    q = 'SELECT COUNT(*) FROM `%s` WHERE project_id IN (%s)' % (table, ids)
    if nullable:
        q += ' OR project_id IS NULL'
    return db.session.execute(text(q)).scalar()


with app.app_context():
    hr('0) 纳管范围')
    os.environ['DATA_SCOPE_ENFORCE'] = 'false'
    from app.auth_core.data_scope import _build_registry
    reg = _build_registry(db)
    names = scoped_model_names()
    ck(len(names) >= 35, '纳管业务模型数', '= %d' % len(names))
    ck('User' not in names, '身份表 users 未被纳管')
    ck(not any(n.startswith('Auth') for n in names), 'auth_core_* 未被纳管')
    ck(not any(n in ('SysDept', 'SysUserProject', 'SysRole') for n in names), 'sys_* 未被纳管')

    users = {}
    for un in USERS:
        u = AuthUser.query.filter_by(username=un).first()
        if u:
            users[un] = u
    ck(len(users) == len(USERS), '测试用户齐备', '= %s' % ','.join(users))

    hr('0b) 主数据共享不变量（公司主库不得被项目隔离切碎）')
    from app.auth_core.data_scope import MASTER_DATA_TABLES
    for _t in ('suppliers', 'materials', 'categories'):
        ck(not is_managed(_t), '%-16s 未被纳管（公司级主数据）' % _t,
           '| 在排除名单=%s' % (_t in MASTER_DATA_TABLES))
    ck(is_managed('project_supplier') and is_managed('project_material'),
       '项目关联表仍参与隔离（project_supplier / project_material）')
    ck(is_managed('stock_ins') and is_managed('contracts'),
       '业务单据表仍参与隔离（stock_ins / contracts）')

    hr('1) 关闭态基线（DATA_SCOPE_ENFORCE=false，行为应与切换前完全一致）')
    os.environ['DATA_SCOPE_ENFORCE'] = 'false'
    base = {}
    for un, u in users.items():
        base[un] = counts_for(u)
    ref = base['admin']
    same = all(base[un][m] == ref[m] for un in users for m, _ in SAMPLES)
    ck(same, '关闭态下所有用户看到的行数一致（无过滤）',
       '| Category=%s Supplier=%s StockIn=%s' % (ref['Category'], ref['Supplier'], ref['StockIn']))

    hr('2) 打开态逐用户核对（与原生 SQL 真值比对）')
    os.environ['DATA_SCOPE_ENFORCE'] = 'true'
    on = {}
    for un, u in users.items():
        on[un] = counts_for(u)
        allowed = on[un]['_allowed']
        gw = AuthGateway.get_data_scope(u.id)
        print('  -- %-13s scope=%-8s allowed=%s' % (un, gw['scope_type'], allowed))
        bad = []
        for mname, tname in SAMPLES:
            cls = model_of(mname)
            nullable = cls.__table__.columns['project_id'].nullable
            want = sql_truth(tname, allowed, nullable)
            got = on[un][mname]
            if want != got:
                bad.append('%s want=%s got=%s' % (mname, want, got))
        ck(not bad, '%-13s 全部抽样表与 SQL 真值一致' % un, '| ' + (';'.join(bad) if bad else 'ok'))

    hr('3) 隔离有效性（非管理员不得看到越权数据）')
    admin_on = on['admin']
    for un in ('zhangwu', '19818623768', 'branch_test'):
        if un not in on:
            continue
        allowed = on[un]['_allowed']
        ck(allowed is not None, '%-13s 不是全量放行' % un, '| allowed=%s' % allowed)
        if allowed is None:
            continue
        leaked = []
        for mname, tname in SAMPLES:
            cls = model_of(mname)
            if not is_managed(tname):
                continue        # 公司级主数据本就共享，不算泄漏
            if cls.__table__.columns['project_id'].nullable:
                continue
            ids = ','.join(str(int(x)) for x in allowed) or 'NULL'
            outside = db.session.execute(text(
                'SELECT COUNT(*) FROM `%s` WHERE project_id NOT IN (%s)' % (tname, ids))).scalar()
            if outside and on[un][mname] >= admin_on[mname]:
                leaked.append('%s(可见%d/全量%d，越权%d)'
                              % (mname, on[un][mname], admin_on[mname], outside))
        ck(not leaked, '%-13s 无跨项目泄漏' % un, '| ' + (';'.join(leaked) if leaked else 'ok'))

    hr('3b) 分页一致性（total 与 items 必须同源，否则「共N条列表却空」）')
    os.environ['DATA_SCOPE_ENFORCE'] = 'true'
    _pm, _pt = SCOPED_PROBE
    for un in ('admin', 'zhangwu', '19818623768'):
        u = users[un]
        with app.test_request_context('/'):
            login_user(u)
            cls = model_of(_pm)
            p = cls.query.paginate(page=1, per_page=5, error_out=False)
            cnt = cls.query.count()
            allowed = current_allowed_projects()
            logout_user()
        nullable = model_of(_pm).__table__.columns['project_id'].nullable
        want = sql_truth(_pt, allowed, nullable)
        ck(p.total == want and cnt == want,
           '%-13s paginate.total / count() 与真值一致' % un,
           '| %s total=%s count=%s want=%s items=%s' % (_pm, p.total, cnt, want, len(p.items)))
        ck(len(p.items) == min(5, want),
           '%-13s items 数量与 total 自洽' % un, '| items=%s' % len(p.items))

    hr('4) 管理员不受影响（scope=all 全量放行）')
    for mname, tname in SAMPLES:
        pass
    diffs = [m for m, _ in SAMPLES if base['admin'][m] != on['admin'][m]]
    ck(not diffs, 'admin 开关前后行数完全一致', '| 差异=%s' % (diffs or '无'))

    hr('5) 逃生舱（用受隔离表验证，否则等于没测）')
    os.environ['DATA_SCOPE_ENFORCE'] = 'true'
    _pm, _pt = SCOPED_PROBE
    _esc_user = '19818623768'       # allowed=[12]，而 project_supplier 数据全在 [4] -> 必被过滤为 0
    u = users[_esc_user]
    with app.test_request_context('/'):
        login_user(u)
        cls = model_of(_pm)
        filtered = cls.query.count()
        skipped = cls.query.execution_options(skip_data_scope=True).count()
        with bypass_data_scope():
            bypassed = cls.query.count()
        logout_user()
    total = db.session.execute(text('SELECT COUNT(*) FROM `%s`' % _pt)).scalar()
    ck(filtered < total, '%s 在 %s 上确实被过滤' % (_esc_user, _pm),
       '= %d / %d' % (filtered, total))
    ck(skipped == total, 'execution_options(skip_data_scope=True) 可豁免', '= %d' % skipped)
    ck(bypassed == total, 'bypass_data_scope() 上下文可豁免', '= %d' % bypassed)

    hr('6) 身份链路不受影响（登录/权限裁决必须绕开隔离）')
    os.environ['DATA_SCOPE_ENFORCE'] = 'true'
    with app.test_request_context('/'):
        login_user(users['zhangwu'])
        ck(AuthUser.query.count() == 7, 'AuthUser 表未被过滤', '= %d' % AuthUser.query.count())
        perms = AuthGateway.get_permissions(users['zhangwu'].id)
        ck(len(perms) > 0, 'zhangwu 权限点仍可解析', '= %d 个' % len(perms))
        logout_user()
    from app import login_manager
    with app.test_request_context('/'):
        loaded = login_manager._user_callback(str(users['admin'].id))
        ck(loaded is not None and loaded.username == 'admin', 'user_loader 正常', '-> %s' % getattr(loaded, 'username', None))

    os.environ['DATA_SCOPE_ENFORCE'] = 'false'
    print('=' * 78)
    print('结果：通过 %d，失败 %d' % (PASS, FAIL))
    print('（脚本结束已把开关复位为 false，生产开关以 .env 为准）')
