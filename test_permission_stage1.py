#!/usr/bin/env python3
"""第一阶段：权限内核四大核心方法测试验证"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import User, SysMenu, SysRole, SysRoleMenu, SysDept, Project

app = create_app()
app.app_context().push()


def print_separator(title=""):
    print("\n" + "=" * 80)
    if title:
        print(f"  {title}")
        print("=" * 80)


def test_method_1_menu_tree():
    """方法一：获取用户菜单权限树"""
    print_separator("方法一：获取用户菜单权限树")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    menu_tree = permission_service.get_user_menu_tree(admin)
    menu_count = 0

    def count_menus(nodes):
        count = 0
        for node in nodes:
            count += 1
            if node.get('children'):
                count += count_menus(node['children'])
        return count

    menu_count = count_menus(menu_tree)
    print(f"✅ admin用户菜单树节点数: {menu_count}")
    print(f"   一级菜单数: {len(menu_tree)}")
    if menu_tree:
        print(f"   第一个一级菜单: {menu_tree[0]['name']}")

    return True


def test_method_2_button_permissions():
    """方法二：获取用户页面按钮权限集"""
    print_separator("方法二：获取用户页面按钮权限集")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    first_menu = SysMenu.query.filter_by(menu_type='menu').first()
    if first_menu:
        buttons = permission_service.get_menu_button_permissions(admin, first_menu.id)
        print(f"✅ admin用户 [{first_menu.menu_name}] 菜单按钮权限: {buttons}")
    else:
        print("⚠️  未找到菜单")

    buttons_by_path = permission_service.get_menu_button_permissions(admin, '/inventory')
    print(f"✅ admin用户 按路径'/inventory' 查询按钮权限: {buttons_by_path}")

    return True


def test_method_3_org_data_scope():
    """方法三：获取用户组织数据范围"""
    print_separator("方法三：获取用户组织数据范围")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    scope = permission_service.get_org_data_scope(admin)
    print(f"✅ admin用户组织数据范围:")
    print(f"   scope: {scope['scope']}")
    print(f"   dept_ids: {scope['dept_ids']}")
    print(f"   dept_names: {scope['dept_names'][:3]}..." if len(scope['dept_names']) > 3 else f"   dept_names: {scope['dept_names']}")

    normal_users = User.query.filter(User.role_id.isnot(None)).limit(2).all()
    for i, user in enumerate(normal_users):
        if user.username == 'admin':
            continue
        scope = permission_service.get_org_data_scope(user)
        print(f"\n✅ 用户[{user.username}] 组织数据范围:")
        print(f"   scope: {scope['scope']}")
        print(f"   dept_ids 数量: {len(scope['dept_ids']) if scope['dept_ids'] else 0}")
        if scope['dept_ids'] and len(scope['dept_ids']) <= 5:
            print(f"   dept_ids: {scope['dept_ids']}")

    return True


def test_method_4_accessible_projects():
    """方法四：获取用户可访问项目列表"""
    print_separator("方法四：获取用户可访问项目列表")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    projects = permission_service.get_accessible_projects(admin)
    print(f"✅ admin用户可访问项目数: {len(projects)}")
    if projects:
        print(f"   前3个项目: {[p['name'] for p in projects[:3]]}")

    normal_users = User.query.filter(User.role_id.isnot(None)).limit(3).all()
    for user in normal_users:
        if user.username == 'admin':
            continue
        projects = permission_service.get_accessible_projects(user)
        print(f"\n✅ 用户[{user.username}] 可访问项目数: {len(projects)}")
        if projects:
            print(f"   前3个: {[p['name'] for p in projects[:3]]}")

    return True


def test_permission_check():
    """check_permission 方法验证"""
    print_separator("check_permission 方法验证")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    test_cases = [
        ("system:dept:view", True, "三段落格式"),
        ("system:dept:create", True, "三段落格式-create"),
        ("26:view", True, "菜单ID:操作"),
    ]

    all_pass = True
    for perm, expected, desc in test_cases:
        result = permission_service.check_permission(admin, perm)
        status = "✅" if result == expected else "❌"
        print(f"{status} {desc}: {perm} -> {result} (预期: {expected})")
        if result != expected:
            all_pass = False

    normal_user = User.query.filter(User.role_id.isnot(None), User.username != 'admin').first()
    if normal_user:
        print(f"\n  普通用户[{normal_user.username}]反向校验:")
        result = permission_service.check_permission(normal_user, 'nonexistent_permission:xxx')
        print(f"    不存在的权限: {result} (预期: False) {'✅' if not result else '❌'}")
        if result:
            all_pass = False

    return all_pass


def test_self_check():
    """权限自检接口验证"""
    print_separator("权限自检 get_permission_detail")

    from app.services.permission_service import permission_service

    admin = User.query.filter_by(username='admin').first()
    if not admin:
        print("❌ admin用户不存在")
        return False

    detail = permission_service.get_permission_detail(admin)
    print(f"✅ 权限自检返回字段: {list(detail.keys())}")
    print(f"   user: {detail['user']['username']}")
    print(f"   role: {detail['role']['role_name'] if detail['role'] else 'None'}")
    print(f"   data_scope: {detail['data_scope']['scope']}")
    print(f"   org_data_scope dept count: {len(detail['org_data_scope']['dept_ids']) if detail['org_data_scope']['dept_ids'] else 'all'}")
    print(f"   allowed_projects count: {len(detail['allowed_projects'])}")
    print(f"   menu_tree root count: {len(detail['menu_tree'])}")
    print(f"   button_permissions count: {len(detail['button_permissions'])}")

    return True


def test_permission_service_singleton():
    """验证单例模式"""
    print_separator("单例模式验证")

    from app.services.permission_service import PermissionService, permission_service

    s1 = PermissionService()
    s2 = PermissionService()
    if s1 is s2 is permission_service:
        print("✅ PermissionService 单例模式验证通过")
        return True
    else:
        print("❌ PermissionService 单例模式验证失败")
        return False


if __name__ == '__main__':
    print("\n" + "#" * 80)
    print("#  第一阶段：权限内核四大核心方法测试验证")
    print("#  " + "=" * 76)
    print("#  测试目标：")
    print("#    1. 获取用户菜单权限树")
    print("#    2. 获取用户页面按钮权限集")
    print("#    3. 获取用户组织数据范围")
    print("#    4. 获取用户可访问项目列表")
    print("#    5. check_permission 权限校验")
    print("#    6. 权限自检接口")
    print("#    7. 单例模式验证")
    print("#" * 80)

    results = {}

    results['方法一_菜单树'] = test_method_1_menu_tree()
    results['方法二_按钮权限集'] = test_method_2_button_permissions()
    results['方法三_组织数据范围'] = test_method_3_org_data_scope()
    results['方法四_可访问项目'] = test_method_4_accessible_projects()
    results['check_permission'] = test_permission_check()
    results['权限自检'] = test_self_check()
    results['单例模式'] = test_permission_service_singleton()

    print_separator("测试结果汇总")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"  {status} - {name}")

    print(f"\n总计: {passed}/{total} 项通过")

    if passed == total:
        print("\n🎉 第一阶段所有测试通过！")
    else:
        print(f"\n⚠️  有 {total - passed} 项测试失败，请检查")
        sys.exit(1)
