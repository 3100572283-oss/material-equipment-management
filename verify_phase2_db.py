#!/usr/bin/env python3
"""第二阶段数据库验证脚本"""
import sqlite3

def verify_users():
    conn = sqlite3.connect('material_mgmt.db')
    cursor = conn.cursor()

    print("=" * 80)
    print("【用户数据权限配置验证】")
    print("=" * 80)
    cursor.execute("""
        SELECT u.id, u.username, u.dept_id, d.dept_name, d.dept_type,
               r.role_code, r.role_name, r.data_scope
        FROM users u
        LEFT JOIN sys_dept d ON u.dept_id = d.id
        LEFT JOIN sys_role r ON u.role_id = r.id
        WHERE u.username IN ('admin', 'test_user_a1', 'test_user_b1')
    """)
    for row in cursor.fetchall():
        print(f"  用户: {row[1]}, 部门: {row[3]}, 部门类型: {row[4]}, 角色: {row[5]}, 数据权限: {row[7]}")

    print("\n" + "=" * 80)
    print("【项目部类型部门关联验证】")
    print("=" * 80)
    cursor.execute("""
        SELECT d.id, d.dept_name, d.dept_type, d.project_id, p.name as project_name
        FROM sys_dept d
        LEFT JOIN projects p ON d.project_id = p.id
        WHERE d.dept_type = 'project'
        ORDER BY d.id
    """)
    rows = cursor.fetchall()
    print(f"  项目部类型部门数量: {len(rows)}")
    for row in rows:
        print(f"    部门ID={row[0]} {row[1]} -> 项目ID={row[3]} {row[4]}")

    print("\n" + "=" * 80)
    print("【用户-项目直接分配验证】")
    print("=" * 80)
    cursor.execute("""
        SELECT u.username, p.name as project_name, up.is_main
        FROM sys_user_project up
        JOIN users u ON up.user_id = u.id
        JOIN projects p ON up.project_id = p.id
        ORDER BY u.username, up.is_main DESC
    """)
    for row in cursor.fetchall():
        main_tag = " [主项目]" if row[2] else ""
        print(f"  {row[0]} -> {row[1]}{main_tag}")

    print("\n" + "=" * 80)
    print("【在建项目统计】")
    print("=" * 80)
    cursor.execute("SELECT COUNT(*) FROM projects WHERE status = 'active' AND is_archived = 0")
    count = cursor.fetchone()[0]
    print(f"  在建项目数量: {count}")

    cursor.execute("SELECT id, name, status FROM projects WHERE is_archived = 0")
    for row in cursor.fetchall():
        print(f"    项目ID={row[0]} {row[1]} (状态: {row[2]})")

    conn.close()
    print("\n" + "=" * 80)
    print("【数据库验证完成】")
    print("=" * 80)

if __name__ == '__main__':
    verify_users()
