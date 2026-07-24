#!/usr/bin/env python3
"""第二阶段验证：项目可见范围控制测试"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import User, Project, SysDept, SysRole, SysRoleDataScope
from flask import session

def test_project_visibility():
    """测试不同数据权限用户的可见项目列表"""
    app = create_app()
    with app.app_context():
        print("=" * 80)
        print("【第二阶段】项目可见范围控制验证")
        print("=" * 80)

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
                print(f"\n✗ 用户 {username} 不存在，跳过测试")
                continue

            print(f"\n{'=' * 80}")
            print(f"用户: {username} ({desc})")
            print(f"用户ID: {user.id}, 部门ID: {user.dept_id}")
            print(f"数据权限: {user.get_data_scope()}")

            # 获取部门信息
            if user.dept_id:
                dept = SysDept.query.get(user.dept_id)
                if dept:
                    print(f"部门名称: {dept.dept_name}, 部门类型: {dept.dept_type}")
                    if dept.project_id:
                        print(f"关联项目ID: {dept.project_id}")

            # 获取可见项目列表
            allowed = user.get_allowed_projects()
            visible_projects = user.get_visible_projects()

            if allowed is None:
                print("\n✓ 可见项目: 全部项目")
                can_view_all = True
            else:
                print(f"\n✓ 可见项目列表 ({len(visible_projects)}个):")
                can_view_all = False
                for p in visible_projects:
                    dept = SysDept.query.filter_by(project_id=p.id).first()
                    dept_name = dept.dept_name if dept else '无'
                    print(f"    - 项目ID={p.id} {p.name} (状态: {p.status}, 部门: {dept_name})")

            # 验证项目切换器逻辑
            print(f"\n项目切换器状态:")
            print(f"  - 是否可查看全部项目: {can_view_all}")
            print(f"  - 可切换项目数: {len([p for p in visible_projects if p.status == 'active' and not p.is_archived])}")

            # 测试can_access_project方法
            print(f"\n项目访问权限验证:")
            test_project_ids = [1, 2, 3, 4, 5]
            for pid in test_project_ids:
                project = Project.query.get(pid)
                if project:
                    can_access = user.can_access_project(pid)
                    status = "✓" if can_access else "✗"
                    print(f"  {status} 项目 {project.name} (ID={pid}): {'可访问' if can_access else '不可访问'}")

        print(f"\n{'=' * 80}")
        print("【验证完成】")
        print("=" * 80)


def test_all_projects_user():
    """测试全部数据权限用户的项目切换器"""
    app = create_app()
    with app.app_context():
        print("\n" + "=" * 80)
        print("【全部数据权限用户测试】")
        print("=" * 80)

        user = User.query.filter_by(username='admin').first()
        if not user:
            print("✗ admin用户不存在")
            return

        print(f"用户: {user.username}")
        print(f"数据权限: {user.get_data_scope()}")
        print(f"是否管理员: {user.is_admin()}")

        # 验证get_allowed_projects返回None（表示全部项目）
        allowed = user.get_allowed_projects()
        if allowed is None:
            print("✓ get_allowed_projects() 返回 None，表示可访问全部项目")
        else:
            print(f"✗ 预期返回 None，实际返回: {allowed}")

        # 验证get_visible_projects返回所有未归档项目
        visible_projects = user.get_visible_projects()
        print(f"✓ 可见项目数: {len(visible_projects)}")

        # 验证can_access_project对所有项目返回True
        all_projects = Project.query.filter_by(is_archived=False).all()
        for p in all_projects[:5]:  # 只测试前5个
            can_access = user.can_access_project(p.id)
            if not can_access:
                print(f"✗ 项目 {p.name} (ID={p.id}) 应该可访问但返回False")
            else:
                print(f"✓ 项目 {p.name} (ID={p.id}): 可访问")


def test_dept_scope_user():
    """测试本部门数据权限用户的项目可见性"""
    app = create_app()
    with app.app_context():
        print("\n" + "=" * 80)
        print("【本部门数据权限用户测试】")
        print("=" * 80)

        # 查找测试用户
        user = User.query.filter_by(username='test_user_a1').first()
        if not user:
            print("✗ test_user_a1用户不存在")
            return

        print(f"用户: {user.username}")
        print(f"部门ID: {user.dept_id}")

        dept = SysDept.query.get(user.dept_id)
        if dept:
            print(f"部门名称: {dept.dept_name}")
            print(f"部门类型: {dept.dept_type}")

            if dept.dept_type == 'project' and dept.project_id:
                project = Project.query.get(dept.project_id)
                print(f"关联项目: {project.name if project else '无'}")

        # 获取可见项目
        visible_projects = user.get_visible_projects()
        print(f"\n可见项目列表 ({len(visible_projects)}个):")
        for p in visible_projects:
            print(f"  - {p.name} (ID={p.id})")

        # 验证是否只能看到本部门关联的项目
        if dept and dept.dept_type == 'project' and dept.project_id:
            expected_project_id = dept.project_id
            for p in visible_projects:
                if p.id != expected_project_id:
                    print(f"✗ 意外发现项目 {p.name} (ID={p.id})，预期只能看到项目ID={expected_project_id}")

        # 测试项目访问权限
        all_projects = Project.query.filter_by(is_archived=False).limit(5).all()
        print(f"\n项目访问权限验证:")
        for p in all_projects:
            can_access = user.can_access_project(p.id)
            status = "✓" if can_access else "✗"
            print(f"  {status} {p.name} (ID={p.id}): {'可访问' if can_access else '不可访问'}")


if __name__ == '__main__':
    test_project_visibility()
    test_all_projects_user()
    test_dept_scope_user()