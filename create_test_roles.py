"""在服务器端创建测试角色和测试用户"""
import sys
sys.path.insert(0, '/opt/material-equipment-management')

from app import create_app, db
from app.models import SysRole, SysMenu, SysRoleMenu, SysRoleDataScope, SysRoleDept, User, SysDept, Project
from werkzeug.security import generate_password_hash
from datetime import datetime

app = create_app()
with app.app_context():
    # 1. 创建三个测试角色
    test_roles = [
        {
            'code_suffix': 'VIEW_STOCK_IN',
            'name': '测试角色A-只查看入库',
            'remark': '阶段四验收：只给入库管理查看权限',
        },
        {
            'code_suffix': 'NO_EQUIPMENT',
            'name': '测试角色B-无设备权限',
            'remark': '阶段四验收：完全不给设备模块权限',
        },
        {
            'code_suffix': 'DEPT_ONLY',
            'name': '测试角色C-本部门数据',
            'remark': '阶段四验收：本部门数据权限',
        },
    ]

    role_ids = {}
    for tr in test_roles:
        existing = SysRole.query.filter_by(role_name=tr['name']).first()
        if existing:
            role_ids[tr['code_suffix']] = existing.id
            print(f"Role exists: {tr['name']} id={existing.id}")
            continue
        max_id = db.session.query(db.func.max(SysRole.id)).scalar() or 0
        role = SysRole(
            role_code=f"ROLE_TEST_{tr['code_suffix']}",
            role_name=tr['name'],
            data_scope='all',
            sort=max_id + 1,
            remark=tr['remark']
        )
        db.session.add(role)
        db.session.flush()
        role_ids[tr['code_suffix']] = role.id
        print(f"Created role: {tr['name']} id={role.id}")

    db.session.commit()

    # 2. 测试角色A：只给入库管理的查看权限
    role_a_id = role_ids['VIEW_STOCK_IN']
    SysRoleMenu.query.filter_by(role_id=role_a_id).delete()

    # 找入库管理菜单
    stock_in_menus = SysMenu.query.filter(
        db.or_(
            SysMenu.menu_code.like('%stock_in%'),
            SysMenu.path.like('%stock_in%'),
            SysMenu.menu_name == '入库管理',
        )
    ).all()
    print(f"找到入库相关菜单 {len(stock_in_menus)} 个")
    for m in stock_in_menus:
        if m.menu_type in ('menu', 'button'):
            if m.menu_type == 'menu' or (m.menu_type == 'button' and '入库' in m.menu_name):
                rm = SysRoleMenu(role_id=role_a_id, menu_id=m.id, operation='view')
                db.session.add(rm)
                print(f"角色A: {m.menu_name} ({m.menu_type}) - view")

    # 给工作台基础权限
    workspace = SysMenu.query.filter(
        db.or_(SysMenu.menu_code == 'workspace', SysMenu.menu_name == '工作台')
    ).first()
    if workspace:
        rm = SysRoleMenu(role_id=role_a_id, menu_id=workspace.id, operation='view')
        db.session.add(rm)
        print(f"角色A: 工作台 - view")

    # 角色A数据权限: 全部
    cfg = SysRoleDataScope.query.filter_by(role_id=role_a_id).first()
    if cfg:
        cfg.data_scope = 'all'
        cfg.custom_depts = None
    else:
        db.session.add(SysRoleDataScope(role_id=role_a_id, data_scope='all', custom_depts=None))

    # 3. 测试角色B：完全不给设备模块权限（只给工作台）
    role_b_id = role_ids['NO_EQUIPMENT']
    SysRoleMenu.query.filter_by(role_id=role_b_id).delete()
    if workspace:
        rm = SysRoleMenu(role_id=role_b_id, menu_id=workspace.id, operation='view')
        db.session.add(rm)
        print(f"角色B: 仅给工作台 - view")

    cfg = SysRoleDataScope.query.filter_by(role_id=role_b_id).first()
    if cfg:
        cfg.data_scope = 'all'
        cfg.custom_depts = None
    else:
        db.session.add(SysRoleDataScope(role_id=role_b_id, data_scope='all', custom_depts=None))

    # 4. 测试角色C：本部门数据权限，给多个基础菜单
    role_c_id = role_ids['DEPT_ONLY']
    SysRoleMenu.query.filter_by(role_id=role_c_id).delete()
    if workspace:
        rm = SysRoleMenu(role_id=role_c_id, menu_id=workspace.id, operation='view')
        db.session.add(rm)
    for mc in ['stock_in', 'stock_out', 'contract']:
        m = SysMenu.query.filter_by(menu_code=mc).first()
        if m:
            for op in ['view', 'create']:
                rm = SysRoleMenu(role_id=role_c_id, menu_id=m.id, operation=op)
                db.session.add(rm)
    cfg = SysRoleDataScope.query.filter_by(role_id=role_c_id).first()
    if cfg:
        cfg.data_scope = 'dept'
        cfg.custom_depts = None
    else:
        db.session.add(SysRoleDataScope(role_id=role_c_id, data_scope='dept', custom_depts=None))

    db.session.commit()
    print("\n所有测试角色权限配置完成")

    # 5. 创建测试用户
    depts = SysDept.query.filter(SysDept.status == True).limit(2).all()
    if len(depts) < 2:
        depts = SysDept.query.limit(2).all()
    if not depts:
        print("ERROR: 部门表为空，无法创建测试用户")
        sys.exit(1)

    dept_a, dept_b = depts[0], depts[1]
    print(f"\n使用部门: A={dept_a.dept_name}(id={dept_a.id}), B={dept_b.dept_name}(id={dept_b.id})")

    test_users = [
        ('test_user_a1', '测试用户A1', role_c_id, dept_a.id, 'dept'),
        ('test_user_b1', '测试用户B1', role_c_id, dept_b.id, 'dept'),
        ('test_user_view', '测试用户A-只查看入库', role_a_id, dept_a.id, 'all'),
        ('test_user_noeq', '测试用户B-无设备权限', role_b_id, dept_a.id, 'all'),
    ]
    for uname, name, rid, did, ds in test_users:
        if not User.query.filter_by(username=uname).first():
            u = User(
                username=uname,
                password_hash=generate_password_hash('Test@123456'),
                name=name,
                role_id=rid,
                dept_id=did,
                data_scope=ds,
            )
            db.session.add(u)
            print(f"Created user: {uname}")

    db.session.commit()
    print("\n=== 阶段四测试账号信息 ===")
    print("test_user_view  /Test@123456  角色A: 只查看入库")
    print("test_user_noeq  /Test@123456  角色B: 无设备权限")
    print("test_user_a1    /Test@123456  角色C: 部门A")
    print("test_user_b1    /Test@123456  角色C: 部门B")
