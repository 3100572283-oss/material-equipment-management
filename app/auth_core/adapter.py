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


def sync_org_from_legacy(legacy_dept_id=None):
    """把旧 sys_dept 树迁为 auth_core_org_unit（含法人标识、三级四层层级）"""
    query = LegacyDept.query
    if legacy_dept_id:
        query = query.filter_by(id=legacy_dept_id)
    mapped = {}
    for d in query.all():
        if AuthOrgUnit.query.filter_by(org_code='LEGACY_%s' % d.dept_code).first():
            continue
        level = {'company': 2, 'branch': 2, 'project': 4, 'dept': 4, 'team': 4}.get(d.dept_type, 4)
        legal = d.dept_type in ('company', 'branch')
        ou = AuthOrgUnit(
            org_code='LEGACY_%s' % d.dept_code,
            org_name=d.dept_name,
            parent_id=d.parent_id or 0,
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
        mapped[d.id] = ou.id
    db.session.commit()
    return mapped


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
            p = AuthPermission(module=module, resource=resource, action=action, perm_key=key)
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
        # 旧 角色-菜单 → 新 角色-权限
        for rm in LegacyRoleMenu.query.filter_by(role_id=r.id).all():
            perm_id = perm_map.get(rm.menu_id)
            if not perm_id:
                continue
            if not AuthRolePermission.query.filter_by(role_id=role.id, permission_id=perm_id).first():
                db.session.add(AuthRolePermission(role_id=role.id, permission_id=perm_id))
    db.session.commit()
    return role_map


def sync_user_from_legacy(legacy_user_id):
    """旧 users 单行 → auth_core_user（密码直搬，同算法零中断）"""
    legacy = LegacyUser.query.get(legacy_user_id)
    if not legacy:
        return None
    existing = AuthUser.query.filter_by(username=legacy.username).first()
    if existing:
        return existing.id  # 幂等
    user = AuthUser(
        username=legacy.username,
        password_hash=legacy.password_hash,  # werkzeug pbkdf2:sha256，直搬可验证
        name=legacy.name,
        email=legacy.email,
        phone=legacy.phone,
        org_id=legacy.dept_id,
        post=(legacy.role_obj.role_code if legacy.role_obj else None),
        source='etl',
        must_change_password=bool(legacy.must_change_password),
    )
    db.session.add(user)
    db.session.flush()

    # 角色映射
    if legacy.role_obj:
        role = AuthRole.query.filter_by(role_code=legacy.role_obj.role_code).first()
        if role and not AuthUserRole.query.filter_by(user_id=user.id, role_id=role.id).first():
            db.session.add(AuthUserRole(user_id=user.id, role_id=role.id))

    # 数据范围：allowed_projects → user_data_scope.project_ids
    proj_ids = []
    try:
        if legacy.allowed_projects:
            proj_ids = json.loads(legacy.allowed_projects)
    except Exception:
        proj_ids = []
    if not proj_ids:
        for up in LegacyUserProject.query.filter_by(user_id=legacy.id).all():
            proj_ids.append(up.project_id)
    if proj_ids and not AuthUserDataScope.query.filter_by(user_id=user.id).first():
        db.session.add(AuthUserDataScope(user_id=user.id, scope_type='project',
                                         project_ids=json.dumps(proj_ids, ensure_ascii=False)))
    db.session.commit()
    return user.id


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
