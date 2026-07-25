"""为测试角色C重新配置权限（修正menu_code匹配）"""
import sys
sys.path.insert(0, '/opt/material-equipment-management')

from app import create_app, db
from app.models import SysRole, SysMenu, SysRoleMenu, SysRoleDataScope

app = create_app()
with app.app_context():
    role_c = SysRole.query.filter_by(role_code='ROLE_TEST_DEPT_ONLY').first()
    if not role_c:
        print("ERROR: 角色C不存在")
        sys.exit(1)
    print(f"角色C: {role_c.role_name} (id={role_c.id})")

    # 清除旧权限
    SysRoleMenu.query.filter_by(role_id=role_c.id).delete()

    # 工作台
    workspace = SysMenu.query.filter_by(menu_name='工作台').first()
    if workspace:
        db.session.add(SysRoleMenu(role_id=role_c.id, menu_id=workspace.id, operation='view'))
        print(f"  + 工作台 view")

    # 入库管理、出库管理、合同台账 - 给view+create权限
    target_menus = ['入库管理', '出库管理', '合同台账']
    for mname in target_menus:
        m = SysMenu.query.filter_by(menu_name=mname).first()
        if m:
            for op in ['view', 'create']:
                db.session.add(SysRoleMenu(role_id=role_c.id, menu_id=m.id, operation=op))
            print(f"  + {mname} view+create (id={m.id})")
        else:
            print(f"  ! 未找到菜单: {mname}")

    db.session.commit()
    print("\n完成。")
