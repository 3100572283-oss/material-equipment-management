#!/usr/bin/env python3
"""
权限体系历史数据校准脚本

功能：
1. 遍历所有已有角色，自动补全缺失的父菜单查看权限
2. 遍历所有已有用户，按统一规则重新计算并校准可访问项目范围

注意：
- 已手动配置的自定义项目权限保留，不受自动规则覆盖
- 只有角色数据权限为全部/本部门及下级/本部门的，才走自动计算规则
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import SysRole, SysRoleMenu, SysMenu, User, SysUserProject, SysRoleDataScope, Project
from app.services.permission_service import permission_service


def calibrate_role_parent_permissions():
    """校准角色权限：补全缺失的父菜单查看权限"""
    print("\n=== 校准角色父菜单权限 ===")
    
    roles = SysRole.query.filter_by(status=True).all()
    total_roles = len(roles)
    total_added = 0
    
    for i, role in enumerate(roles, 1):
        print(f"\n[{i}/{total_roles}] 角色: {role.role_code} - {role.role_name}")
        
        existing = set((rm.menu_id, rm.operation) for rm in SysRoleMenu.query.filter_by(role_id=role.id).all())
        original_count = len(existing)
        
        filled = permission_service.apply_parent_menu_permissions(role.id, existing)
        filled_count = len(filled)
        
        added = filled - existing
        
        if added:
            print(f"  发现 {len(added)} 个缺失的父菜单权限，正在补全...")
            for menu_id, operation in added:
                menu = SysMenu.query.get(menu_id)
                if menu:
                    rm = SysRoleMenu(role_id=role.id, menu_id=menu_id, operation=operation)
                    db.session.add(rm)
                    print(f"    + [{menu_id}] {menu.menu_name} ({operation})")
            total_added += len(added)
        else:
            print(f"  权限完整，无需补全")
    
    db.session.commit()
    print(f"\n=== 角色权限校准完成 ===")
    print(f"共处理 {total_roles} 个角色")
    print(f"共补全 {total_added} 个父菜单权限")


def calibrate_user_project_permissions():
    """校准用户项目权限：按统一规则重新计算可访问项目范围"""
    print("\n\n=== 校准用户项目权限 ===")
    
    users = User.query.filter_by(status='active').all()
    total_users = len(users)
    total_updated = 0
    total_unchanged = 0
    
    for i, user in enumerate(users, 1):
        print(f"\n[{i}/{total_users}] 用户: {user.username} - {user.name or '-'}")
        
        if not user.role_id:
            print(f"  无角色，跳过")
            continue
        
        scope_info = permission_service.get_user_data_scope(user)
        data_scope = scope_info['scope']
        
        if data_scope in ('custom', 'self'):
            print(f"  数据权限为 '{data_scope}'，保留手动配置，跳过")
            total_unchanged += 1
            continue
        
        auto_project_ids = permission_service.get_user_allowed_projects(user)
        if auto_project_ids is None:
            auto_project_ids = [p.id for p in Project.query.filter_by(is_archived=False).all()]
        
        current_project_ids = [up.project_id for up in user.user_projects]
        
        if set(auto_project_ids) == set(current_project_ids):
            print(f"  项目权限已正确，无需更新")
            total_unchanged += 1
            continue
        
        print(f"  当前项目: {current_project_ids}")
        print(f"  应有权限: {auto_project_ids}")
        
        SysUserProject.query.filter_by(user_id=user.id).delete()
        
        if auto_project_ids:
            main_pid = auto_project_ids[0]
            for pid in auto_project_ids:
                up = SysUserProject(
                    user_id=user.id,
                    project_id=pid,
                    is_main=(pid == main_pid)
                )
                db.session.add(up)
        
        user.project_id = auto_project_ids[0] if auto_project_ids else None
        
        print(f"  ✓ 已更新")
        total_updated += 1
    
    db.session.commit()
    print(f"\n=== 用户项目权限校准完成 ===")
    print(f"共处理 {total_users} 个用户")
    print(f"更新项目权限: {total_updated}")
    print(f"保持不变: {total_unchanged}")


def main():
    print("=" * 60)
    print("权限体系历史数据校准脚本")
    print("=" * 60)
    
    app = create_app()
    with app.app_context():
        calibrate_role_parent_permissions()
        calibrate_user_project_permissions()
    
    print("\n" + "=" * 60)
    print("校准完成！")
    print("=" * 60)


if __name__ == '__main__':
    main()
