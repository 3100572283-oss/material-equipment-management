#!/usr/bin/env python3
"""第四阶段验证：数据权限落地测试"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import User, SysRole, SysRoleDataScope, SysDept, StockIn, Contract, Supplier
from app.utils import apply_data_scope
from flask import session

def test_data_scope_filter():
    """测试数据权限过滤逻辑"""
    app = create_app()
    with app.app_context():
        with app.test_request_context():
            print("=" * 80)
            print("【第四阶段】数据权限落地测试")
            print("=" * 80)

            test_users = [
                ('admin', '全部数据权限'),
                ('test_user_a1', '本部门数据权限'),
                ('test_user_b1', '本部门数据权限'),
            ]

            for username, desc in test_users:
                user = User.query.filter_by(username=username).first()
                if not user:
                    print(f"\n✗ 用户 {username} 不存在")
                    continue

                print(f"\n{'=' * 80}")
                print(f"用户: {username} ({desc})")
                print(f"用户ID: {user.id}, 部门ID: {user.dept_id}")
                print(f"数据权限: {user.get_data_scope()}")

                if user.dept_id:
                    dept = db.session.get(SysDept, user.dept_id)
                    if dept:
                        print(f"部门名称: {dept.dept_name}, 部门类型: {dept.dept_type}")

                # 测试入库单数据权限过滤
                print("\n--- 入库单数据权限测试 ---")
                session['current_project_id'] = 1  # 设置当前项目
                
                # 无数据权限过滤的总数
                total_count = StockIn.query.filter(StockIn.status != 'voided').count()
                print(f"入库单总数(无权限过滤): {total_count}")

                # 应用数据权限后的数量
                query = StockIn.query.filter(StockIn.status != 'voided')
                query = apply_data_scope(query, StockIn, user)
                filtered_count = query.count()
                print(f"入库单数量(数据权限过滤后): {filtered_count}")

                if user.get_data_scope() == 'all' and filtered_count != total_count:
                    print(f"✗ 全部权限用户应该看到所有入库单")
                elif user.get_data_scope() == 'dept' and filtered_count == total_count:
                    print(f"! 本部门权限用户看到了全部入库单（可能当前项目数据较少）")
                else:
                    print(f"✓ 数据权限过滤正常")

                # 测试合同数据权限过滤
                print("\n--- 合同数据权限测试 ---")
                total_contracts = Contract.query.count()
                print(f"合同总数(无权限过滤): {total_contracts}")

                query = Contract.query
                query = apply_data_scope(query, Contract, user)
                filtered_contracts = query.count()
                print(f"合同数量(数据权限过滤后): {filtered_contracts}")

                # 测试供应商数据权限过滤
                print("\n--- 供应商数据权限测试 ---")
                total_suppliers = Supplier.query.filter(Supplier.status == 'qualified').count()
                print(f"供应商总数(无权限过滤): {total_suppliers}")

                query = Supplier.query.filter(Supplier.status == 'qualified')
                query = apply_data_scope(query, Supplier, user)
                filtered_suppliers = query.count()
                print(f"供应商数量(数据权限过滤后): {filtered_suppliers}")


def test_dept_hierarchy():
    """测试部门层级关系"""
    app = create_app()
    with app.app_context():
        print("\n" + "=" * 80)
        print("部门层级关系测试")
        print("=" * 80)

        def print_dept_tree(dept_id, indent=0):
            depts = SysDept.query.filter_by(parent_id=dept_id).order_by(SysDept.sort).all()
            for dept in depts:
                prefix = "├─" if indent > 0 else ""
                dept_type = DEPT_TYPE_LABELS.get(dept.dept_type, dept.dept_type)
                project_info = f" (项目ID:{dept.project_id})" if dept.project_id else ""
                print(f"{'  ' * indent}{prefix}{dept.dept_name} [{dept_type}]{project_info}")
                print_dept_tree(dept.id, indent + 1)

        DEPT_TYPE_LABELS = {
            'company': '公司',
            'branch': '分公司',
            'project': '项目部',
            'dept': '部门',
            'team': '班组',
        }

        print_dept_tree(0)


def test_role_data_scope_config():
    """测试角色数据权限配置"""
    app = create_app()
    with app.app_context():
        print("\n" + "=" * 80)
        print("角色数据权限配置测试")
        print("=" * 80)

        roles = SysRole.query.all()
        for role in roles:
            scope_cfg = SysRoleDataScope.query.filter_by(role_id=role.id).first()
            scope = scope_cfg.data_scope if scope_cfg else role.data_scope
            print(f"角色 [{role.role_code}] {role.role_name}: 数据权限={scope}")


if __name__ == '__main__':
    test_data_scope_filter()
    test_dept_hierarchy()
    test_role_data_scope_config()

    print("\n" + "=" * 80)
    print("【第四阶段验证完成】")
    print("=" * 80)