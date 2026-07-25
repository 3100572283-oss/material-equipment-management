"""
双树分离 - 权限点迁移脚本
功能：
1. 更新组织架构权限点：从 system:depts:* 改为 system:dept:*
2. 新增项目管理菜单及其权限点
3. 将旧的组织架构权限关联迁移到新标识
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import SysMenu, SysRoleMenu


def migrate_menus():
    app = create_app()
    with app.app_context():
        print("=" * 60)
        print(" 权限点迁移：双树分离")
        print("=" * 60)

        try:
            sys_catalog = SysMenu.query.filter_by(menu_code='system').first()
            if not sys_catalog:
                print("  未找到系统管理目录，跳过迁移")
                return True

            # ===== 1. 更新组织架构菜单权限标识 =====
            print("\n[1/3] 更新组织架构菜单权限标识...")
            dept_menu = SysMenu.query.filter_by(menu_code='system.depts').first()
            if dept_menu:
                old_perm = dept_menu.permission
                new_perm = 'system:dept:view'
                dept_menu.permission = new_perm
                dept_menu.menu_name = '组织架构'
                print(f"  组织架构菜单: {old_perm} → {new_perm}")

                # 更新按钮权限
                dept_buttons = SysMenu.query.filter_by(
                    parent_id=dept_menu.id, menu_type='button'
                ).all()
                op_map = {
                    'view': 'list',
                    'create': 'add',
                    'edit': 'edit',
                    'delete': 'delete',
                }
                for btn in dept_buttons:
                    old_btn_perm = btn.permission
                    parts = old_btn_perm.split(':') if old_btn_perm else []
                    if len(parts) == 3 and parts[1] == 'depts':
                        new_op = op_map.get(parts[2], parts[2])
                        new_btn_perm = f'system:dept:{new_op}'
                        btn.permission = new_btn_perm
                        print(f"  按钮 {btn.menu_name}: {old_btn_perm} → {new_btn_perm}")

                        # 迁移角色权限关联
                        old_links = SysRoleMenu.query.filter_by(
                            menu_id=btn.id
                        ).all()
                        for link in old_links:
                            if link.operation == parts[2]:
                                link.operation = new_op

            # ===== 2. 新增项目管理菜单 =====
            print("\n[2/3] 新增项目管理菜单及权限点...")

            # 检查是否已存在
            project_menu = SysMenu.query.filter_by(menu_code='system.project').first()
            if not project_menu:
                # 找组织架构菜单的排序，放在它后面
                dept_sort = dept_menu.sort if dept_menu else 20
                project_menu = SysMenu(
                    parent_id=sys_catalog.id,
                    menu_name='项目管理',
                    menu_code='system.project',
                    menu_type='menu',
                    path='/system/projects',
                    permission='system:project:view',
                    icon='bi-houses',
                    sort=dept_sort + 1,
                    status=True,
                )
                db.session.add(project_menu)
                db.session.flush()
                print(f"  新增项目管理菜单: id={project_menu.id}")

                # 新增按钮权限点
                buttons = [
                    ('查看', 'list', 'system:project:list'),
                    ('新增', 'add', 'system:project:add'),
                    ('编辑', 'edit', 'system:project:edit'),
                    ('删除', 'delete', 'system:project:delete'),
                    ('配置', 'config', 'system:project:config'),
                ]
                for i, (name, op, perm) in enumerate(buttons):
                    btn = SysMenu(
                        parent_id=project_menu.id,
                        menu_name=name,
                        menu_code=f'system.project.{op}',
                        menu_type='button',
                        permission=perm,
                        sort=i + 1,
                        status=True,
                    )
                    db.session.add(btn)
                    print(f"    新增按钮: {name} - {perm}")

                # 给超级管理员分配项目管理权限
                from app.models import SysRole
                super_admin = SysRole.query.filter_by(role_code='super_admin').first()
                if super_admin:
                    all_operations = ['list', 'add', 'edit', 'delete', 'config']
                    for op in all_operations:
                        existing = SysRoleMenu.query.filter_by(
                            role_id=super_admin.id,
                            menu_id=project_menu.id,
                            operation=op
                        ).first()
                        if not existing:
                            link = SysRoleMenu(
                                role_id=super_admin.id,
                                menu_id=project_menu.id,
                                operation=op
                            )
                            db.session.add(link)
                    print(f"  已给超级管理员分配项目管理全部权限")
            else:
                print(f"  项目管理菜单已存在: id={project_menu.id}，跳过")

            # ===== 3. 重新排序系统管理子菜单 =====
            print("\n[3/3] 重新排序系统管理子菜单...")
            children = SysMenu.query.filter_by(
                parent_id=sys_catalog.id, menu_type='menu'
            ).order_by(SysMenu.sort).all()
            for i, m in enumerate(children):
                m.sort = i + 1
            print(f"  已重排 {len(children)} 个菜单项")

            db.session.commit()
            print("\n" + "=" * 60)
            print(" 权限点迁移完成！")
            print("=" * 60)

            # 验证
            print("\n验证结果:")
            dept_m = SysMenu.query.filter_by(menu_code='system.depts').first()
            proj_m = SysMenu.query.filter_by(menu_code='system.project').first()
            if dept_m:
                print(f"  组织架构: {dept_m.permission}")
                btns = SysMenu.query.filter_by(parent_id=dept_m.id, menu_type='button').all()
                for b in btns:
                    print(f"    {b.menu_name}: {b.permission}")
            if proj_m:
                print(f"  项目管理: {proj_m.permission}")
                btns = SysMenu.query.filter_by(parent_id=proj_m.id, menu_type='button').all()
                for b in btns:
                    print(f"    {b.menu_name}: {b.permission}")

            return True

        except Exception as e:
            db.session.rollback()
            print(f"\n❌ 迁移失败，已回滚: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == '__main__':
    success = migrate_menus()
    sys.exit(0 if success else 1)
