#!/usr/bin/env python3
"""第五阶段验证：模块开关与权限体系联动测试"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import SysModule, SysMenu, User, SysRoleMenu
from flask import json

def test_module_switch_effect():
    """测试模块开关对权限的影响"""
    app = create_app()
    with app.app_context():
        print("=" * 80)
        print("【第五阶段】模块开关与权限体系联动测试")
        print("=" * 80)

        # 查看当前模块状态
        print("\n--- 当前模块状态 ---")
        modules = SysModule.query.order_by(SysModule.sort).all()
        for m in modules:
            status = "✅ 启用" if m.status else "❌ 关闭"
            required = "(必需)" if m.is_required else ""
            print(f"  [{m.module_key}] {m.module_name} {required}: {status}")

        # 测试：关闭某个模块后，权限是否被过滤
        print("\n--- 测试模块关闭对权限的影响 ---")
        
        test_module = SysModule.query.filter_by(module_key='module_equipment').first()
        if not test_module:
            test_module = SysModule.query.filter_by(is_required=False).first()
        
        if not test_module:
            print("  ✗ 没有可测试的模块")
            return

        print(f"\n  测试模块: {test_module.module_key} ({test_module.module_name})")
        
        # 获取该模块关联的菜单和权限
        module_menus = SysMenu.query.filter_by(module_key=test_module.module_key).all()
        print(f"  关联菜单数: {len(module_menus)}")
        for menu in module_menus[:5]:
            print(f"    - {menu.menu_name} ({menu.permission})")
        
        # 获取该模块关联的权限点
        perm_count = 0
        for menu in module_menus:
            if menu.permission:
                base = menu.permission.rsplit(':', 1)[0]
                perm_count += 8
        print(f"  关联权限点总数: {perm_count}")

        # 模拟管理员用户的权限列表
        print("\n--- 模拟管理员权限计算 ---")
        admin = User.query.filter_by(username='admin').first()
        if admin:
            # 计算未关闭模块时的权限数
            enabled_modules = {m.module_key: m.status for m in SysModule.query.all()}
            all_perms = []
            
            all_menus = SysMenu.query.filter_by(menu_type='menu').all()
            for menu in all_menus:
                if menu.permission:
                    if menu.module_key and not enabled_modules.get(menu.module_key, False):
                        continue
                    base_perm = menu.permission.rsplit(':', 1)[0]
                    for op in ['view', 'create', 'edit', 'delete', 'import', 'export', 'approve', 'print']:
                        all_perms.append(f"{base_perm}:{op}")
            
            print(f"  当前管理员权限总数: {len(all_perms)}")
            
            # 测试关闭模块后的权限数
            enabled_modules[test_module.module_key] = False
            filtered_perms = []
            for menu in all_menus:
                if menu.permission:
                    if menu.module_key and not enabled_modules.get(menu.module_key, False):
                        continue
                    base_perm = menu.permission.rsplit(':', 1)[0]
                    for op in ['view', 'create', 'edit', 'delete', 'import', 'export', 'approve', 'print']:
                        filtered_perms.append(f"{base_perm}:{op}")
            
            diff = len(all_perms) - len(filtered_perms)
            print(f"  关闭模块后权限总数: {len(filtered_perms)}")
            print(f"  过滤掉的权限数: {diff}")
            
            if diff > 0:
                print("  ✓ 模块关闭后权限被正确过滤")
            else:
                print("  ✗ 模块关闭后权限未被过滤")

        # 测试菜单过滤
        print("\n--- 模拟菜单树过滤 ---")
        enabled_modules = {m.module_key: m.status for m in SysModule.query.all()}
        
        def count_menus(parent_id=0, indent=0):
            menus = SysMenu.query.filter_by(parent_id=parent_id, status=True).order_by(SysMenu.sort).all()
            total = 0
            for menu in menus:
                if menu.module_key and not enabled_modules.get(menu.module_key, False):
                    continue
                total += 1
                children = count_menus(menu.id, indent + 1)
                total += children
            return total
        
        total_menus = count_menus()
        print(f"  当前启用模块的菜单总数: {total_menus}")
        
        # 测试关闭设备模块后的菜单数
        enabled_modules[test_module.module_key] = False
        filtered_menus = count_menus()
        print(f"  关闭设备模块后的菜单数: {filtered_menus}")
        
        if filtered_menus < total_menus:
            print("  ✓ 模块关闭后菜单被正确过滤")
        else:
            print("  ✗ 模块关闭后菜单未被过滤")


def test_project_module_config():
    """测试项目级模块配置"""
    app = create_app()
    with app.app_context():
        print("\n" + "=" * 80)
        print("项目级模块配置测试")
        print("=" * 80)
        
        from app.models import Project
        
        projects = Project.query.limit(3).all()
        for project in projects:
            config = project.get_module_config()
            print(f"\n  项目: {project.name}")
            print(f"    模块配置: {json.dumps(config, ensure_ascii=False)[:100]}...")
            
            # 检查是否有自定义配置（非默认值）
            if project.module_config:
                custom_config = json.loads(project.module_config) if project.module_config else {}
                if custom_config:
                    print(f"    自定义配置: {json.dumps(custom_config, ensure_ascii=False)}")


def test_decorator_effect():
    """测试模块装饰器效果"""
    app = create_app()
    with app.app_context():
        print("\n" + "=" * 80)
        print("模块装饰器效果测试")
        print("=" * 80)
        
        # 检查哪些路由文件使用了module_required装饰器
        print("\n--- 使用module_required装饰器的路由文件 ---")
        
        import inspect
        import os
        
        route_files = [
            'equipment/routes.py',
            'material/routes.py', 
            'turnover_material/routes.py',
            'scrap/routes.py',
            'approval/routes.py',
        ]
        
        for rf in route_files:
            full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app', rf)
            if os.path.exists(full_path):
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'module_required' in content:
                        print(f"    ✓ {rf}")
                    else:
                        print(f"    ✗ {rf}")


if __name__ == '__main__':
    test_module_switch_effect()
    test_project_module_config()
    test_decorator_effect()

    print("\n" + "=" * 80)
    print("【第五阶段验证完成】")
    print("=" * 80)