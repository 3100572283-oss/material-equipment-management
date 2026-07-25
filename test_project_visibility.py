"""第二阶段验收：项目可见范围控制验证

验证规则：
1. 全部数据权限用户 → 能看到所有项目 + "全部项目"选项
2. 本部门权限用户 → 只能看到本部门及下级的项目部
3. 项目部用户 → 只能看到自己归属的项目
"""

import sys
sys.path.insert(0, '/opt/material-equipment-management')

from app import create_app, db
from app.models import User, SysDept, Project, SysRoleDataScope
from flask import Flask

app = create_app()
with app.app_context():
    print("=" * 60)
    print("第二阶段验收：项目可见范围控制")
    print("=" * 60)

    # 检查测试账号
    test_accounts = [
        ('admin', '系统管理员，全部权限'),
        ('test_user_view', '角色A，全部数据权限'),
        ('test_user_a1', '角色C，本部门数据权限'),
        ('test_user_b1', '角色C，本部门数据权限'),
    ]

    for username, desc in test_accounts:
        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"\n{username}: 用户不存在")
            continue

        print(f"\n--- {username} ({desc}) ---")
        print(f"  用户ID: {user.id}, 部门ID: {user.dept_id}")
        print(f"  数据权限: {user.get_data_scope()}")

        # 获取可见项目
        allowed = user.get_allowed_projects()
        if allowed is None:
            print("  可见项目: 全部项目")
            visible_projects = Project.query.filter_by(is_archived=False).all()
        else:
            visible_projects = user.get_visible_projects()

        print(f"  可见项目列表 ({len(visible_projects)}个):")
        for p in visible_projects:
            # 查找关联的部门
            dept = SysDept.query.filter_by(project_id=p.id).first()
            dept_name = dept.dept_name if dept else '无'
            print(f"    - 项目ID={p.id} {p.name} (部门: {dept_name})")

    print("\n" + "=" * 60)
    print("验收完成")
    print("=" * 60)