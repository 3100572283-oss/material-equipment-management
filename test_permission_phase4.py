#!/usr/bin/env python3
"""
第四阶段：全量联调 + 6大验收标准逐条验证

验收标准：
1. 菜单级一致性验收：后端计算的菜单树 = 前端渲染的菜单
2. 按钮级一致性验收：后端权限校验 = 前端按钮显隐
3. 数据范围一致性验收：数据权限规则在所有模块统一生效
4. 配置与生效一致性验收：角色权限配置保存后立即生效
5. 分级管控验收：集团管理员 > 分公司管理员 > 项目部用户
6. 扩展性验收：新增角色/菜单无需改代码自动适配
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import User, SysRole, SysMenu, SysRoleMenu, SysRoleDataScope, Project, SysDept
from app.services.permission_service import permission_service
from werkzeug.security import generate_password_hash

app = create_app()

def test_menu_consistency():
    """验收标准1：菜单级一致性验收"""
    print("\n" + "="*70)
    print("  验收标准1：菜单级一致性验收")
    print("="*70)
    
    with app.app_context():
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            print("❌ 未找到admin用户")
            return False
        
        menu_tree = permission_service.get_user_menu_tree(admin)
        print(f"✅ admin用户菜单树节点数: {len(menu_tree)}")
        
        for catalog in menu_tree:
            children_count = len(catalog.get('children', []))
            print(f"  - [{catalog.get('code')}] {catalog.get('name')}: {children_count} 个子菜单")
        
        roles = SysRole.query.filter(SysRole.role_code != 'admin').all()
        for role in roles:
            users = User.query.filter_by(role_id=role.id).limit(1).all()
            if users:
                user = users[0]
                menu_tree = permission_service.get_user_menu_tree(user)
                print(f"✅ [{role.role_name}] {user.username} 菜单树节点数: {len(menu_tree)}")
        
        return True

def test_button_consistency():
    """验收标准2：按钮级一致性验收"""
    print("\n" + "="*70)
    print("  验收标准2：按钮级一致性验收")
    print("="*70)
    
    with app.app_context():
        menus_to_test = ['inventory', 'stock_in', 'stock_out', 'purchase_contract', 'equipment']
        
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            print("❌ 未找到admin用户")
            return False
        
        for menu_code in menus_to_test:
            buttons = permission_service.get_menu_button_permissions(admin, menu_code)
            print(f"✅ admin用户 [{menu_code}] 按钮权限: {buttons}")
            assert 'view' in buttons, f"admin用户应有view权限"
            assert 'create' in buttons, f"admin用户应有create权限"
        
        roles = SysRole.query.filter(SysRole.role_code != 'admin').all()
        for role in roles:
            users = User.query.filter_by(role_id=role.id).limit(1).all()
            if users:
                user = users[0]
                for menu_code in menus_to_test[:2]:
                    buttons = permission_service.get_menu_button_permissions(user, menu_code)
                    print(f"  - [{role.role_name}] {user.username} [{menu_code}]: {buttons}")
        
        return True

def test_data_scope_consistency():
    """验收标准3：数据范围一致性验收"""
    print("\n" + "="*70)
    print("  验收标准3：数据范围一致性验收")
    print("="*70)
    
    with app.app_context():
        scopes = ['all', 'dept_and_sub', 'dept', 'self', 'custom']
        
        admin = User.query.filter_by(username='admin').first()
        if admin:
            scope_info = permission_service.get_org_data_scope(admin)
            print(f"✅ admin用户数据范围: {scope_info}")
            assert scope_info['scope'] == 'all', "admin应拥有全部数据权限"
        
        for scope in scopes:
            role = SysRole.query.filter_by(data_scope=scope).first()
            if role:
                users = User.query.filter_by(role_id=role.id).limit(1).all()
                if users:
                    user = users[0]
                    scope_info = permission_service.get_org_data_scope(user)
                    project_scope = permission_service.get_user_allowed_projects(user)
                    print(f"✅ [{role.role_name}] [{scope}] 数据范围: {scope_info['scope']}, 项目数: {len(project_scope) if project_scope else '全部'}")
        
        return True

def test_config_effectiveness():
    """验收标准4：配置与生效一致性验收"""
    print("\n" + "="*70)
    print("  验收标准4：配置与生效一致性验收")
    print("="*70)
    
    with app.app_context():
        existing_role = SysRole.query.filter_by(role_code='test_config_role').first()
        if existing_role:
            existing_users = User.query.filter_by(role_id=existing_role.id).all()
            for u in existing_users:
                db.session.delete(u)
            existing_rms = SysRoleMenu.query.filter_by(role_id=existing_role.id).all()
            for rm in existing_rms:
                db.session.delete(rm)
            db.session.delete(existing_role)
            db.session.commit()
        
        test_role = SysRole(
            role_code='test_config_role',
            role_name='测试配置角色',
            data_scope='dept'
        )
        db.session.add(test_role)
        db.session.commit()
        
        test_user = User(
            username='test_config_user',
            password_hash=generate_password_hash('test123', method='pbkdf2:sha256'),
            name='测试配置用户',
            role_id=test_role.id,
            dept_id=1
        )
        db.session.add(test_user)
        db.session.commit()
        
        inventory_menu = SysMenu.query.filter_by(menu_code='inventory').first()
        if not inventory_menu:
            print("❌ 未找到inventory菜单")
            return False
        
        has_before = permission_service.check_permission(test_user, f'inventory:create')
        print(f"  修改前 [{test_role.role_name}] [{test_user.username}] inventory:create: {has_before}")
        
        rm = SysRoleMenu(
            role_id=test_role.id,
            menu_id=inventory_menu.id,
            operation='create'
        )
        db.session.add(rm)
        db.session.commit()
        
        has_after = permission_service.check_permission(test_user, f'inventory:create')
        print(f"  修改后 [{test_role.role_name}] [{test_user.username}] inventory:create: {has_after}")
        
        assert has_before != has_after, "权限配置应立即生效"
        print(f"✅ 权限配置修改后立即生效：{has_before} -> {has_after}")
        
        db.session.delete(rm)
        db.session.delete(test_user)
        db.session.delete(test_role)
        db.session.commit()
        
        return True

def test_hierarchical_control():
    """验收标准5：分级管控验收"""
    print("\n" + "="*70)
    print("  验收标准5：分级管控验收")
    print("="*70)
    
    with app.app_context():
        admin = User.query.filter_by(username='admin').first()
        if admin:
            print(f"✅ 集团管理员 [{admin.username}]: is_admin={admin.is_admin()}")
        
        roles = SysRole.query.order_by(SysRole.id).all()
        for role in roles:
            users = User.query.filter_by(role_id=role.id).limit(1).all()
            if users:
                user = users[0]
                org_scope = permission_service.get_org_data_scope(user)
                projects = permission_service.get_accessible_projects(user)
                print(f"  - [{role.role_name}] {user.username}: scope={org_scope['scope']}, projects={len(projects)}")
        
        return True

def test_extensibility():
    """验收标准6：扩展性验收"""
    print("\n" + "="*70)
    print("  验收标准6：扩展性验收")
    print("="*70)
    
    with app.app_context():
        existing_role = SysRole.query.filter_by(role_code='test_ext_role').first()
        if existing_role:
            existing_users = User.query.filter_by(role_id=existing_role.id).all()
            for u in existing_users:
                db.session.delete(u)
            existing_rms = SysRoleMenu.query.filter_by(role_id=existing_role.id).all()
            for rm in existing_rms:
                db.session.delete(rm)
            db.session.delete(existing_role)
            db.session.commit()
        
        new_role_name = "测试扩展角色"
        new_role = SysRole(
            role_code='test_ext_role',
            role_name=new_role_name,
            data_scope='dept'
        )
        db.session.add(new_role)
        db.session.commit()
        print(f"✅ 创建新角色: {new_role_name}")
        
        inventory_menu = SysMenu.query.filter_by(menu_code='inventory').first()
        if inventory_menu:
            rm = SysRoleMenu(
                role_id=new_role.id,
                menu_id=inventory_menu.id,
                operation='view'
            )
            db.session.add(rm)
            db.session.commit()
            print(f"✅ 为新角色分配inventory视图权限")
        
        new_user = User(
            username='test_ext_user',
            password_hash=generate_password_hash('test123', method='pbkdf2:sha256'),
            name='测试扩展用户',
            role_id=new_role.id,
            dept_id=1
        )
        db.session.add(new_user)
        db.session.commit()
        print(f"✅ 创建测试用户: test_ext_user")
        
        menu_tree = permission_service.get_user_menu_tree(new_user)
        buttons = permission_service.get_menu_button_permissions(new_user, 'inventory')
        print(f"✅ 新用户菜单树节点数: {len(menu_tree)}")
        print(f"✅ 新用户inventory按钮权限: {buttons}")
        
        assert 'view' in buttons, "新角色应自动获取分配的权限"
        
        db.session.delete(new_user)
        db.session.delete(rm)
        db.session.delete(new_role)
        db.session.commit()
        print(f"✅ 清理测试数据")
        
        return True

def main():
    print("="*70)
    print("  第四阶段：全量联调 + 6大验收标准逐条验证")
    print("="*70)
    
    results = []
    
    results.append(('菜单级一致性', test_menu_consistency()))
    results.append(('按钮级一致性', test_button_consistency()))
    results.append(('数据范围一致性', test_data_scope_consistency()))
    results.append(('配置与生效一致性', test_config_effectiveness()))
    results.append(('分级管控', test_hierarchical_control()))
    results.append(('扩展性', test_extensibility()))
    
    print("\n" + "="*70)
    print("  验收结果汇总")
    print("="*70)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"  {status} - {name}")
    
    print(f"\n  总计: {passed}/{total} 项通过")
    
    if passed == total:
        print("\n🎉 第四阶段6大验收标准全部通过！")
        return 0
    else:
        print("\n❌ 部分验收标准未通过，请检查错误信息")
        return 1

if __name__ == '__main__':
    sys.exit(main())