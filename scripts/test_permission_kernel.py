#!/usr/bin/env python3
"""权限内核测试脚本"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.permission_service import permission_service

app = create_app()
with app.app_context():
    from app.models import User, SysRole
    
    admin = User.query.filter_by(username='admin').first()
    print('=== 测试统一权限服务 ===')
    print()
    
    print('1. 用户数据权限范围：')
    scope_info = permission_service.get_user_data_scope(admin)
    print('   scope:', scope_info['scope'])
    
    print()
    print('2. 用户可访问项目ID列表：')
    projects = permission_service.get_user_allowed_projects(admin)
    print('   projects:', projects)
    
    print()
    print('3. 用户按钮权限列表（前10个）：')
    perms = permission_service.get_user_permissions_list(admin)
    print('   count:', len(perms))
    print('   sample:', perms[:10])
    
    print()
    print('4. 按钮权限校验：')
    has_stock_view = permission_service.check_permission(admin, 'stock:in:view')
    has_stock_create = permission_service.check_permission(admin, 'stock:in:create')
    print('   stock:in:view:', has_stock_view)
    print('   stock:in:create:', has_stock_create)
    
    print()
    print('5. 菜单树（前5个节点）：')
    menu_tree = permission_service.get_user_menu_tree(admin)
    def print_tree(nodes, indent=0):
        for node in nodes[:5]:
            print('  ' * indent + f'{node["name"]} (id={node["id"]})')
            if 'children' in node and node['children']:
                print_tree(node['children'], indent + 1)
    print_tree(menu_tree)
    
    print()
    print('6. 测试新增角色（ROLE0011）权限计算：')
    test_role = SysRole.query.filter_by(role_code='ROLE0011').first()
    if test_role:
        print('   role_id:', test_role.id)
        from app.models import SysRoleMenu
        role_perms = SysRoleMenu.query.filter_by(role_id=test_role.id).all()
        print('   permission count:', len(role_perms))
    else:
        print('   ROLE0011 not found')
    
    print()
    print('=== 权限内核测试完成 ===')
