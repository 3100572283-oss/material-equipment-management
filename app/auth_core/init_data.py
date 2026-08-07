# app/auth_core/init_data.py
"""auth_core 种子数据初始化：组织树骨架 + 铁建岗位角色模板 + 权限点 + 超级管理员。

create_app() 的 app_context 内调用。幂等：已存在则跳过。
注意：现有 users 均为测试内容，此处不直接迁移旧用户，仅建骨架 + 超级管理员种子。
真实用户迁移走 migrate_auth_to_core.py（按需运行）。
"""
import json
from flask import current_app
from werkzeug.security import generate_password_hash

from app import db
from app.auth_core.models import (
    AuthOrgUnit, AuthUser, AuthRole, AuthPermission,
    AuthRolePermission, AuthUserRole, AuthDataScope,
)


# 组织树骨架（parent_code 解析，保证父子顺序）
# 注意：此处只播种集团根节点。二级集团/三级公司/项目部一律由 ETL 从旧
# sys_dept 迁入（org_code = LEGACY_*），或由管理员在权限中台页面维护，
# 不再塞任何"（示例）"占位数据，避免生产组织树出现脏节点。
ORG_SEED = [
    {'code': 'CRC', 'name': '中国铁建股份有限公司', 'parent_code': None,
     'level': 1, 'legal': True, 'type': 'company'},
]

# 铁建岗位角色模板：(code, name, data_scope, sort)
ROLE_SEED = [
    ('super_admin', '超级管理员', 'all', 0),
    ('project_manager', '项目经理', 'project', 1),
    ('chief_engineer', '总工', 'project', 2),
    ('material_manager', '物资部长', 'legal_entity', 3),
    ('material_staff', '物资员', 'project', 4),
    ('equipment_manager', '设备管理员', 'project', 5),
    ('cost_accountant', '成本会计', 'legal_entity', 6),
    ('contract_admin', '合同管理员', 'legal_entity', 7),
    ('safety_director', '安全总监', 'project', 8),
    ('viewer', '查看员', 'project', 9),
]

# 初始权限点：(module, resource, action, perm_key)
PERM_SEED = [
    ('material', 'stock_in', 'view', 'material:stock_in:view'),
    ('material', 'stock_in', 'create', 'material:stock_in:create'),
    ('material', 'stock_out', 'view', 'material:stock_out:view'),
    ('material', 'stock_out', 'create', 'material:stock_out:create'),
    ('equipment', 'maintenance', 'view', 'equipment:maintenance:view'),
    ('equipment', 'maintenance', 'create', 'equipment:maintenance:create'),
    ('cost', 'budget', 'view', 'cost:budget:view'),
    ('cost', 'budget', 'edit', 'cost:budget:edit'),
    ('cost', 'profit', 'view', 'cost:profit:view'),
    ('cost', 'control', 'view', 'cost:control:view'),
    ('cost', 'adjust', 'review', 'cost:adjust:review'),
    ('subcontract', 'supplier', 'view', 'subcontract:supplier:view'),
    ('subcontract', 'supplier', 'review', 'subcontract:supplier:review'),
    # —— M6 分包商核验 ——
    ('subcontractor', 'profile', 'view', 'subcontractor:profile:view'),
    ('subcontractor', 'profile', 'edit', 'subcontractor:profile:edit'),
    ('subcontractor', 'profile', 'review', 'subcontractor:profile:review'),
    ('subcontractor', 'blacklist', 'view', 'subcontractor:blacklist:view'),
    ('subcontractor', 'blacklist', 'edit', 'subcontractor:blacklist:edit'),
    # —— M5 票据三流合一 ——
    ('invoice', 'threeflow', 'view', 'invoice:threeflow:view'),
    ('invoice', 'threeflow', 'book', 'invoice:threeflow:book'),
    # —— 独立数据可视化大屏（第三终端）——
    ('bigscreen', 'view', 'view', 'bigscreen:view:view'),
    # —— 权限中台自身管理权限（三页 UI 按钮级控制）——
    ('system', 'org', 'view', 'system:org:view'),
    ('system', 'org', 'create', 'system:org:create'),
    ('system', 'org', 'edit', 'system:org:edit'),
    ('system', 'org', 'delete', 'system:org:delete'),
    ('system', 'role', 'view', 'system:role:view'),
    ('system', 'role', 'create', 'system:role:create'),
    ('system', 'role', 'edit', 'system:role:edit'),
    ('system', 'role', 'delete', 'system:role:delete'),
    ('system', 'role', 'grant', 'system:role:grant'),
    ('system', 'user', 'view', 'system:user:view'),
    ('system', 'user', 'create', 'system:user:create'),
    ('system', 'user', 'edit', 'system:user:edit'),
    ('system', 'user', 'delete', 'system:user:delete'),
    ('system', 'user', 'reset_pwd', 'system:user:reset_pwd'),
]

# 岗位选项（新增用户时「选组织+选岗位自动带权」的下拉源）
# 与 ROLE_SEED 的 role_code 一一对应，保证选岗位即自动挂载对应角色模板
POST_OPTIONS = [
    {'code': code, 'name': name}
    for code, name, _scope, _sort in ROLE_SEED if code != 'super_admin'
]

# 数据范围类型（角色配置下拉源）
SCOPE_OPTIONS = [
    {'code': 'self', 'name': '仅本人数据'},
    {'code': 'project', 'name': '本项目部数据'},
    {'code': 'legal_entity', 'name': '本法人及下属单位'},
    {'code': 'all', 'name': '全部数据'},
    {'code': 'custom', 'name': '自定义组织集'},
]


def init_auth_core_data():
    # 1) 组织树
    org_ids = {}
    for d in ORG_SEED:
        if AuthOrgUnit.query.filter_by(org_code=d['code']).first():
            continue
        parent_id = 0
        if d['parent_code']:
            parent = AuthOrgUnit.query.filter_by(org_code=d['parent_code']).first()
            parent_id = parent.id if parent else 0
        ou = AuthOrgUnit(
            org_code=d['code'], org_name=d['name'], parent_id=parent_id,
            org_level=d['level'], legal_entity=d['legal'], dept_type=d['type'], status=True,
        )
        db.session.add(ou)
        db.session.flush()
        org_ids[d['code']] = ou.id

    # 2) 角色 + 数据范围
    role_ids = {}
    for code, name, scope, sort in ROLE_SEED:
        role = AuthRole.query.filter_by(role_code=code).first()
        if not role:
            role = AuthRole(role_code=code, role_name=name, is_system=True, sort=sort)
            db.session.add(role)
            db.session.flush()
        role_ids[code] = role.id
        if not AuthDataScope.query.filter_by(role_id=role.id).first():
            db.session.add(AuthDataScope(role_id=role.id, scope_type=scope))

    # 3) 权限点
    perm_ids = {}
    for module, resource, action, key in PERM_SEED:
        p = AuthPermission.query.filter_by(perm_key=key).first()
        if not p:
            p = AuthPermission(module=module, resource=resource, action=action, perm_key=key)
            db.session.add(p)
            db.session.flush()
        perm_ids[key] = p.id

    # 4) super_admin 拥有全部权限
    sa = role_ids.get('super_admin')
    if sa:
        existing = set(rp.permission_id for rp in AuthRolePermission.query.filter_by(role_id=sa).all())
        for p in AuthPermission.query.filter_by(status=True).all():
            if p.id not in existing:
                db.session.add(AuthRolePermission(role_id=sa, permission_id=p.id))

    # 5) 超级管理员种子用户（密码与新系统一致；旧 admin 同算法可验证）
    if not AuthUser.query.filter_by(username='admin').first():
        pwd = current_app.config.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')
        admin = AuthUser(
            username='admin',
            password_hash=generate_password_hash(pwd, method='pbkdf2:sha256'),
            name='系统管理员',
            org_id=org_ids.get('CRC'),
            post='super_admin',
            must_change_password=True,
        )
        db.session.add(admin)
        db.session.flush()
        if not AuthUserRole.query.filter_by(user_id=admin.id, role_id=role_ids['super_admin']).first():
            db.session.add(AuthUserRole(user_id=admin.id, role_id=role_ids['super_admin']))

    db.session.commit()
    # 6) 铁建岗位角色默认授权（启动自愈）：即便未跑 legacy ETL，岗位角色也获得与岗位匹配的
    #    合理功能权限，避免 perm_count=0 → 菜单为空。幂等，与 ETL 授权取并集。
    try:
        from app.auth_core import adapter
        n = adapter.grant_default_role_permissions()
        if n:
            print("[auth_core] 岗位默认授权 +%d 项" % n)
    except Exception as e:  # 启动期不阻断主流程
        print("[auth_core] 岗位默认授权跳过：%s" % e)

    # 6.5) 阶段 A 影子菜单目录：幂等 ETL 旧 sys_menu → auth_core_menu（非破坏，旧路径仍读 sys_menu）
    try:
        from app.auth_core import adapter
        adapter.sync_menu_from_legacy()
    except Exception as e:  # 启动期不阻断主流程
        print("[auth_core] 菜单目录 ETL 跳过：%s" % e)

    print("[auth_core] seed data initialized")
