#!/usr/bin/env python3
"""测试父子菜单联动功能"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.services.permission_service import permission_service

app = create_app()
with app.app_context():
    from app.models import SysRole, SysRoleMenu, SysMenu
    
    test_role = SysRole.query.filter_by(role_code='ROLE0011').first()
    if not test_role:
        print('测试角色 ROLE0011 不存在')
        sys.exit(0)
    
    print('=== 测试父子菜单联动 ===')
    print()
    print(f'角色: {test_role.role_code} - {test_role.role_name}')
    
    stock_in_menu = SysMenu.query.filter_by(menu_name='入库管理').first()
    print(f'入库管理菜单 ID: {stock_in_menu.id}')
    
    parent_ids = permission_service.get_parent_menu_ids(stock_in_menu.id)
    print(f'上级父菜单ID: {parent_ids}')
    
    print()
    print('当前权限:')
    current_perms = set((rm.menu_id, rm.operation) for rm in SysRoleMenu.query.filter_by(role_id=test_role.id).all())
    print(f'  count: {len(current_perms)}')
    
    print()
    print('模拟勾选入库管理查看权限:')
    new_ops = {(stock_in_menu.id, 'view')}
    filled = permission_service.apply_parent_menu_permissions(test_role.id, new_ops)
    print(f'  原始权限: {new_ops}')
    print(f'  补全后权限: {filled}')
    print(f'  新增父菜单权限: {filled - new_ops}')
    
    print()
    print('验证父菜单名称:')
    for menu_id, op in (filled - new_ops):
        menu = SysMenu.query.get(menu_id)
        if menu:
            print(f'  [{menu_id}] {menu.menu_name} ({op})')
    
    print()
    print('=== 父子菜单联动测试完成 ===')
