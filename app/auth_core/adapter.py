# app/auth_core/adapter.py
"""LegacyAuthAdapter：旧权限代码 → auth_core 的单向桥接。

这是唯一允许 import 旧权限模型（app.models）的新模块文件。
新模块其他文件绝不引用旧模型，桥接逻辑全部收敛于此，避免旧代码污染新逻辑。
"""
import json
from app import db
from app.auth_core.models import (
    AuthUser, AuthRole, AuthOrgUnit, AuthPermission,
    AuthRolePermission, AuthUserRole, AuthDataScope, AuthUserDataScope,
)

# 仅此文件引用旧模型（桥接需要）
from app.models import (  # noqa: E402
    User as LegacyUser, SysRole as LegacyRole, SysDept as LegacyDept,
    SysMenu as LegacyMenu, SysRoleMenu as LegacyRoleMenu,
    SysUserProject as LegacyUserProject,
)

# 旧 data_scope 值 → 新 scope_type
SCOPE_MAP = {
    'all': 'all',
    'dept': 'project',
    'dept_and_sub': 'project',
    'custom': 'custom',
    'self': 'self',
}

# 旧 dept_code → 已有 auth_core org_code 归一映射。
# 旧系统的顶级部门"中国铁建"(HQ) 与 seed 根节点"中国铁建股份有限公司"(CRC)
# 是同一法人实体，不做归一会在组织树上出现两个根。
ORG_CODE_ALIAS = {
    'HQ': 'CRC',
}

# 模块中文名（覆盖全部 auth_core_permission.module 取值，供权限矩阵分组标题展示）
MODULE_CN = {
    'admin': '后台管理', 'ai': 'AI智能助手', 'basic': '基础数据',
    'contract': '采购合同', 'cost': '成本核算', 'equipment': '设备管理',
    'help': '帮助中心', 'master': '主数据管理', 'material': '物资管理',
    'module_equipment': '设备管理', 'module_industry_tools': '行业工具',
    'module_turnover': '周转材管理', 'org_sync': '组织同步', 'report': '统计报表',
    'stock': '库存管理', 'subcontract': '分包商管理', 'system': '系统与权限',
    'tools': '行业工具', 'turnover': '周转材管理', 'workbench': '工作台',
}

# 资源中文名兜底（仅用于无对应 SysMenu.permission 的权限点；多数资源名由 SysMenu.menu_name 派生）
RESOURCE_CN = {
    ('cost', 'budget'): '责任成本预算', ('equipment', 'maintenance'): '设备维保',
    ('material', 'stock_in'): '入库管理', ('material', 'stock_out'): '出库管理',
    ('subcontract', 'supplier'): '供应商管理', ('system', 'org'): '组织架构',
    ('module_equipment', 'equipment'): '设备台账',
    ('module_industry_tools', 'industry_tools'): '行业工具',
    ('module_turnover', 'turnover_material'): '周转材管理',
}


def _resolve_names(module, resource, perm_key=None):
    """解析权限点的中文模块名/资源名：优先 SysMenu.menu_name（数据驱动），其次兜底字典。"""
    module_name = MODULE_CN.get(module, module)
    resource_name = RESOURCE_CN.get((module, resource))
    if not resource_name and perm_key:
        m = LegacyMenu.query.filter(LegacyMenu.permission == perm_key).first()
        if m:
            resource_name = m.menu_name
    if not resource_name:
        # 退而求其次：用 module:resource: 前缀匹配菜单（覆盖 menu_type=menu 的权限点）
        m = LegacyMenu.query.filter(LegacyMenu.permission.like('%s:%s:%%' % (module, resource))).first()
        if m:
            resource_name = m.menu_name
    if not resource_name:
        resource_name = resource
    return module_name, resource_name


def _ensure_permission(module, resource, action):
    """确保 auth_core_permission 中存在 (module, resource, action) 权限点，返回实例（幂等）。

    权限点 perm_key 统一为 'module:resource:action'，与 SysMenu.permission
    （由 _generate_permission 生成 module:func:view）同命名空间，可作为菜单可见性与
    按钮权限判定的单一数据源（SSOT）。同时写入中文模块名/资源名供权限矩阵友好展示。
    """
    perm_key = '%s:%s:%s' % (module, resource, action)
    p = AuthPermission.query.filter_by(perm_key=perm_key).first()
    if p:
        return p
    module_name, resource_name = _resolve_names(module, resource, perm_key)
    p = AuthPermission(module=module, resource=resource, action=action, perm_key=perm_key,
                        module_name=module_name, resource_name=resource_name)
    db.session.add(p)
    db.session.flush()
    return p


def _org_code_for(dept_code):
    """旧部门编码 → 新组织编码（优先走归一别名，否则加 LEGACY_ 前缀）"""
    return ORG_CODE_ALIAS.get(dept_code) or ('LEGACY_%s' % dept_code)


def sync_org_from_legacy(legacy_dept_id=None):
    """把旧 sys_dept 树迁为 auth_core_org_unit（含法人标识、三级四层层级）

    关键：旧 sys_dept.id 与新 auth_core_org_unit.id 属于不同 id 空间，
    必须先建组织再统一修正 parent_id 指向新的 auth_core org id。
    """
    query = LegacyDept.query
    if legacy_dept_id:
        query = query.filter_by(id=legacy_dept_id)
    # 1) 创建缺失组织（parent_id 暂置 0）
    for d in query.all():
        if AuthOrgUnit.query.filter_by(org_code=_org_code_for(d.dept_code)).first():
            continue
        level = {'company': 2, 'branch': 2, 'project': 4, 'dept': 4, 'team': 4}.get(d.dept_type, 4)
        legal = d.dept_type in ('company', 'branch')
        ou = AuthOrgUnit(
            org_code=_org_code_for(d.dept_code),
            org_name=d.dept_name,
            parent_id=0,
            org_level=level,
            legal_entity=legal,
            dept_type=d.dept_type,
            project_id=d.project_id,
            leader=d.leader,
            sort=d.sort or 0,
            status=d.status if d.status is not None else True,
        )
        db.session.add(ou)
        db.session.flush()
    db.session.commit()
    # 2) 构建 旧 dept_id -> 新 org_id 映射
    id_map = {}
    for d in LegacyDept.query.all():
        ou = AuthOrgUnit.query.filter_by(org_code=_org_code_for(d.dept_code)).first()
        if ou:
            id_map[d.id] = ou.id
    # 3) 修正所有 parent_id（旧 sys_dept.id -> 新 auth_core org id）
    for d in LegacyDept.query.all():
        ou_id = id_map.get(d.id)
        if ou_id is None:
            continue
        ou = AuthOrgUnit.query.get(ou_id)
        if ou is None:
            continue
        new_parent = id_map.get(d.parent_id, 0) if d.parent_id else 0
        if ou.parent_id != new_parent:
            ou.parent_id = new_parent
            db.session.add(ou)
    db.session.commit()
    return id_map


def sync_role_from_legacy():
    """旧 sys_role → auth_core_role + 抽取 sys_menu.permission 为权限点 + 角色权限关联"""
    # 1) 权限点（从旧菜单的 permission 标识抽取，menu_type 仅渲染用，不进权限判定）
    perm_map = {}
    for m in LegacyMenu.query.filter(LegacyMenu.permission.isnot(None)).all():
        key = m.permission
        p = AuthPermission.query.filter_by(perm_key=key).first()
        if not p:
            parts = key.split(':')
            module = parts[0] if parts else 'misc'
            action = parts[-1] if parts else 'view'
            resource = parts[1] if len(parts) > 2 else (m.menu_code or 'page')
            module_name, resource_name = _resolve_names(module, resource, key)
            p = AuthPermission(module=module, resource=resource, action=action, perm_key=key,
                               module_name=module_name, resource_name=resource_name)
            db.session.add(p)
            db.session.flush()
        perm_map[m.id] = p.id

    # 2) 角色 + 角色权限
    role_map = {}
    for r in LegacyRole.query.all():
        role = AuthRole.query.filter_by(role_code=r.role_code).first()
        if not role:
            role = AuthRole(role_code=r.role_code, role_name=r.role_name,
                            is_system=True, sort=r.sort or 0,
                            status=r.status if r.status is not None else True)
            db.session.add(role)
            db.session.flush()
        role_map[r.id] = role.id
        new_scope = SCOPE_MAP.get(r.data_scope, 'project')
        if not AuthDataScope.query.filter_by(role_id=role.id).first():
            db.session.add(AuthDataScope(role_id=role.id, scope_type=new_scope))
        # 旧 角色-菜单(含 operation) → 新 角色-权限（SSOT）
        #   - view 权限点：perm_key 与 SysMenu.permission 完全一致，承载「菜单可见性」
        #   - operation 权限点(module:resource:<op>)：承载「按钮粒度」create/edit/delete/export...
        for rm in LegacyRoleMenu.query.filter_by(role_id=r.id).all():
            menu = LegacyMenu.query.get(rm.menu_id)
            if not menu or not menu.permission:
                continue
            parts = menu.permission.split(':')
            module = parts[0] if parts else 'misc'
            resource = parts[1] if len(parts) > 2 else (menu.menu_code or 'page')
            operation = rm.operation or 'view'
            view_perm = _ensure_permission(module, resource, 'view')
            op_perm = _ensure_permission(module, resource, operation)
            for perm in (view_perm, op_perm):
                if not AuthRolePermission.query.filter_by(role_id=role.id, permission_id=perm.id).first():
                    db.session.add(AuthRolePermission(role_id=role.id, permission_id=perm.id))
    db.session.commit()
    sync_permission_names()
    return role_map


def sync_permission_names():
    """回填/修正 auth_core_permission 的中文模块名与资源名（幂等，可重复执行）。

    优先用 SysMenu.menu_name 派生资源名，其次 MODULE_CN/RESOURCE_CN 兜底字典；
    保证权限矩阵展示始终为中文，不再暴露内部编码。
    """
    updated = 0
    for p in AuthPermission.query.all():
        mn, rn = _resolve_names(p.module, p.resource, p.perm_key)
        if (p.module_name or '') != mn or (p.resource_name or '') != rn:
            p.module_name = mn
            p.resource_name = rn
            db.session.add(p)
            updated += 1
    if updated:
        db.session.commit()
    return updated


def sync_user_from_legacy(legacy_user_id):
    """旧 users 单行 → auth_core_user（密码直搬，同算法零中断）

    关键：org_id 必须指向新的 auth_core org id（经 org_code 反查），
    dept_id 保留旧 sys_dept.id 以兼容 Project.dept_id 查询；旧管理员赠 super_admin。
    """
    legacy = LegacyUser.query.get(legacy_user_id)
    if not legacy:
        return None
    # 反查新 org id（旧 sys_dept.id -> 新 auth_core org id 通过 org_code 桥接）
    dept = LegacyDept.query.get(legacy.dept_id) if legacy.dept_id else None
    ou = AuthOrgUnit.query.filter_by(org_code=_org_code_for(dept.dept_code)).first() if dept else None
    new_org_id = ou.id if ou else None

    existing = AuthUser.query.filter_by(username=legacy.username).first()
    if existing:
        # 幂等修正（防止旧次错误映射）：组织归属 + 角色 + 数据范围 全量对齐
        existing.org_id = new_org_id
        existing.dept_id = legacy.dept_id
        existing.name = legacy.name or existing.name
        existing.password_hash = legacy.password_hash or existing.password_hash
        existing.email = legacy.email or existing.email
        existing.phone = legacy.phone or existing.phone
        # 强制改密标志必须以旧系统为准，否则 seed 建的 admin 会被反复拦在改密页
        existing.must_change_password = bool(legacy.must_change_password)
        if legacy.status is not None:
            existing.status = bool(legacy.status)
        _sync_user_roles(existing, legacy)
        _sync_user_scope(existing, legacy)
        db.session.commit()
        return existing.id

    user = AuthUser(
        username=legacy.username,
        password_hash=legacy.password_hash,  # werkzeug pbkdf2:sha256，直搬可验证
        name=legacy.name,
        email=legacy.email,
        phone=legacy.phone,
        org_id=new_org_id,
        dept_id=legacy.dept_id,  # 旧 sys_dept.id，供 Project.dept_id 查询兼容
        post=(legacy.role_obj.role_code if legacy.role_obj else None),
        source='etl',
        must_change_password=bool(legacy.must_change_password),
    )
    db.session.add(user)
    db.session.flush()

    _sync_user_roles(user, legacy)
    _sync_user_scope(user, legacy)

    db.session.commit()
    return user.id


def _sync_user_roles(user, legacy):
    """旧用户角色 → auth_core_user_role（幂等；旧管理员额外赠 super_admin）"""
    if legacy.role_obj:
        role = AuthRole.query.filter_by(role_code=legacy.role_obj.role_code).first()
        if role and not AuthUserRole.query.filter_by(user_id=user.id, role_id=role.id).first():
            db.session.add(AuthUserRole(user_id=user.id, role_id=role.id))
    if legacy.is_admin():
        sa = AuthRole.query.filter_by(role_code='super_admin').first()
        if sa and not AuthUserRole.query.filter_by(user_id=user.id, role_id=sa.id).first():
            db.session.add(AuthUserRole(user_id=user.id, role_id=sa.id))
    db.session.flush()


def _sync_user_scope(user, legacy):
    """旧 allowed_projects / sys_user_project → auth_core_user_data_scope（幂等覆盖）

    超管不写用户级白名单：其角色档位已是 all，写白名单只会造成语义歧义。
    """
    sa = AuthRole.query.filter_by(role_code='super_admin').first()
    if sa and AuthUserRole.query.filter_by(user_id=user.id, role_id=sa.id).first():
        stale = AuthUserDataScope.query.filter_by(user_id=user.id).first()
        if stale:
            db.session.delete(stale)
            db.session.flush()
        return

    proj_ids = []
    try:
        if legacy.allowed_projects:
            proj_ids = json.loads(legacy.allowed_projects)
    except Exception:
        proj_ids = []
    if not proj_ids:
        for up in LegacyUserProject.query.filter_by(user_id=legacy.id).all():
            proj_ids.append(up.project_id)
    if not proj_ids:
        return
    row = AuthUserDataScope.query.filter_by(user_id=user.id).first()
    payload = json.dumps(proj_ids, ensure_ascii=False)
    if row:
        row.scope_type = 'project'
        row.project_ids = payload
        db.session.add(row)
    else:
        db.session.add(AuthUserDataScope(user_id=user.id, scope_type='project',
                                         project_ids=payload))
    db.session.flush()


def sync_all_from_legacy():
    """全量单向同步：组织 → 角色/权限 → 用户"""
    report = {'orgs': 0, 'roles': 0, 'users': 0}
    sync_org_from_legacy()
    report['orgs'] = AuthOrgUnit.query.count()
    sync_role_from_legacy()
    report['roles'] = AuthRole.query.count()
    for u in LegacyUser.query.all():
        sync_user_from_legacy(u.id)
    report['users'] = AuthUser.query.count()
    return report
