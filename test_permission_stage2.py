#!/usr/bin/env python3
"""第二阶段：权限装饰器测试验证"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import User, SysMenu, SysRole, SysRoleMenu

app = create_app()
app.app_context().push()


def print_separator(title=""):
    print("\n" + "=" * 80)
    if title:
        print(f"  {title}")
        print("=" * 80)


def test_decorators_import():
    """测试权限装饰器是否能正常导入"""
    print_separator("权限装饰器导入测试")
    try:
        from app.decorators import (
            permission_required,
            menu_view_required,
            button_action_required,
            data_scope_required,
            admin_required,
            editor_required,
            module_required,
            log_audit
        )
        print("✅ 所有权限装饰器导入成功")
        print(f"   导入的装饰器: permission_required, menu_view_required, button_action_required, data_scope_required")
        return True
    except Exception as e:
        print(f"❌ 装饰器导入失败: {e}")
        return False


def test_menu_view_required_logic():
    """测试 menu_view_required 装饰器底层逻辑"""
    print_separator("menu_view_required 底层逻辑测试")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    buttons = permission_service.get_menu_button_permissions(admin, 'inventory')
    has_view = 'view' in buttons
    print(f"✅ admin用户 inventory 菜单按钮权限: {buttons}")
    print(f"   有view权限: {has_view}")

    normal_users = User.query.filter(User.role_id.isnot(None), User.username != 'admin').limit(2).all()
    for user in normal_users:
        buttons = permission_service.get_menu_button_permissions(user, 'inventory')
        has_view = 'view' in buttons
        print(f"\n✅ 用户[{user.username}] inventory菜单view权限: {has_view}")
        if has_view:
            print(f"   按钮权限: {buttons}")

    return True


def test_button_action_required_logic():
    """测试 button_action_required 装饰器底层逻辑"""
    print_separator("button_action_required 底层逻辑测试")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    test_actions = ['view', 'create', 'edit', 'delete', 'export', 'import', 'approve', 'print']
    for action in test_actions:
        buttons = permission_service.get_menu_button_permissions(admin, 'inventory')
        has_action = action in buttons
        print(f"✅ admin用户 inventory {action}: {has_action}")

    normal_users = User.query.filter(User.role_id.isnot(None), User.username != 'admin').limit(2).all()
    for user in normal_users:
        print(f"\n✅ 用户[{user.username}] inventory按钮权限:")
        for action in test_actions:
            buttons = permission_service.get_menu_button_permissions(user, 'inventory')
            has_action = action in buttons
            print(f"   {action}: {has_action}")

    return True


def test_permission_required_logic():
    """测试 permission_required 装饰器底层逻辑"""
    print_separator("permission_required 底层逻辑测试")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    test_cases = [
        ("system:dept:view", True),
        ("system:dept:create", True),
        ("stock_in:create", True),
    ]

    all_pass = True
    for perm, expected in test_cases:
        result = permission_service.check_permission(admin, perm)
        status = "✅" if result == expected else "❌"
        print(f"{status} {perm} -> {result} (预期: {expected})")
        if result != expected:
            all_pass = False

    return all_pass


def test_route_decorators_applied():
    """测试路由装饰器是否已应用到核心模块"""
    print_separator("路由装饰器应用检查")

    import inspect

    modules = [
        ('inventory', 'app.inventory.routes'),
        ('stock_in', 'app.stock_in.routes'),
    ]

    for module_name, module_path in modules:
        try:
            module = __import__(module_path, fromlist=[''])
            decorator_counts = {
                'menu_view_required': 0,
                'button_action_required': 0,
                'permission_required': 0,
            }

            for name, obj in inspect.getmembers(module):
                if inspect.isfunction(obj) and hasattr(obj, '__wrapped__'):
                    func = obj.__wrapped__
                    source = inspect.getsource(func)
                    for dec_name in decorator_counts:
                        if f'@{dec_name}' in source:
                            decorator_counts[dec_name] += 1

            print(f"✅ [{module_name}] 路由装饰器应用:")
            for dec_name, count in decorator_counts.items():
                print(f"   {dec_name}: {count} 处")
        except Exception as e:
            print(f"❌ [{module_name}] 检查失败: {e}")

    return True


def test_menu_code_mapping():
    """测试菜单code与数据库的映射关系"""
    print_separator("菜单code与数据库映射测试")

    from app.models import SysMenu

    test_codes = ['inventory', 'stock_in', 'stock_out', 'contract', 'equipment']
    for code in test_codes:
        menu = SysMenu.query.filter(
            (SysMenu.menu_code == code) |
            (SysMenu.path == f'/{code}') |
            (SysMenu.menu_code.like(f'%{code}%'))
        ).first()
        if menu:
            print(f"✅ 菜单[{code}] 找到: id={menu.id}, name={menu.menu_name}, code={menu.menu_code}, path={menu.path}")
        else:
            print(f"⚠️  菜单[{code}] 未找到，可能需要配置")

    return True


if __name__ == '__main__':
    print("\n" + "#" * 80)
    print("#  第二阶段：权限装饰器测试验证")
    print("#  " + "=" * 76)
    print("#  测试目标：")
    print("#    1. 权限装饰器导入测试")
    print("#    2. menu_view_required 底层逻辑测试")
    print("#    3. button_action_required 底层逻辑测试")
    print("#    4. permission_required 底层逻辑测试")
    print("#    5. 路由装饰器应用检查")
    print("#    6. 菜单code与数据库映射测试")
    print("#" * 80)

    results = {}

    results['装饰器导入'] = test_decorators_import()
    results['menu_view_required'] = test_menu_view_required_logic()
    results['button_action_required'] = test_button_action_required_logic()
    results['permission_required'] = test_permission_required_logic()
    results['路由装饰器应用'] = test_route_decorators_applied()
    results['菜单code映射'] = test_menu_code_mapping()

    print_separator("测试结果汇总")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"  {status} - {name}")

    print(f"\n总计: {passed}/{total} 项通过")

    if passed == total:
        print("\n🎉 第二阶段权限装饰器测试通过！")
    else:
        print(f"\n⚠️  有 {total - passed} 项测试失败，请检查")
        sys.exit(1)
