"""第一阶段：组织架构数据清理修复

修复内容：
1. 清理重复的项目部记录
2. 确保项目部类型部门与项目表正确关联
3. 修复数据一致性

执行规则：
- 保留最早创建的部门记录（id最小）
- 确保项目表中有对应记录
- 建立正确的双向关联
"""

import sqlite3
from datetime import datetime

DB_PATH = '/opt/material-equipment-management/material_mgmt.db'

def clean_organization_data():
    c = sqlite3.connect(DB_PATH)
    cur = c.cursor()

    print("=" * 60)
    print("组织架构数据清理修复")
    print("=" * 60)

    # 1. 分析重复的项目部（相同dept_name且dept_type='project'）
    print("\n【1. 分析重复项目部】")
    cur.execute("""
        SELECT dept_name, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
        FROM sys_dept
        WHERE dept_type = 'project'
        GROUP BY dept_name
        HAVING COUNT(*) > 1
    """)
    duplicates = cur.fetchall()

    if not duplicates:
        print("  无重复项目部")
    else:
        for name, cnt, ids in duplicates:
            print(f"  重复: {name} ({cnt}条) IDs: {ids}")
            # 保留ID最小的，删除其他的
            id_list = [int(x) for x in ids.split(',')]
            keep_id = min(id_list)
            delete_ids = [x for x in id_list if x != keep_id]

            for del_id in delete_ids:
                # 检查是否有关联用户
                cur.execute("SELECT COUNT(*) FROM users WHERE dept_id = ?", (del_id,))
                user_cnt = cur.fetchone()[0]
                if user_cnt > 0:
                    # 将用户移到保留的部门
                    cur.execute("UPDATE users SET dept_id = ? WHERE dept_id = ?", (keep_id, del_id))
                    print(f"    移动 {user_cnt} 个用户从部门{del_id}到部门{keep_id}")

                # 检查是否有子部门
                cur.execute("SELECT COUNT(*) FROM sys_dept WHERE parent_id = ?", (del_id,))
                child_cnt = cur.fetchone()[0]
                if child_cnt > 0:
                    cur.execute("UPDATE sys_dept SET parent_id = ? WHERE parent_id = ?", (keep_id, del_id))
                    print(f"    移动 {child_cnt} 个子部门从父部门{del_id}到{keep_id}")

                # 删除重复部门
                cur.execute("DELETE FROM sys_dept WHERE id = ?", (del_id,))
                print(f"    删除重复部门 id={del_id}")

    c.commit()

    # 2. 检查项目部是否关联了项目
    print("\n【2. 检查项目部-项目关联】")
    cur.execute("""
        SELECT d.id, d.dept_name, d.project_id, p.id as proj_id, p.name
        FROM sys_dept d
        LEFT JOIN projects p ON d.project_id = p.id
        WHERE d.dept_type = 'project'
        ORDER BY d.id
    """)
    project_depts = cur.fetchall()
    for row in project_depts:
        d_id, d_name, d_proj_id, p_id, p_name = row
        if d_proj_id is None:
            print(f"  部门{id} '{d_name}' 未关联项目")
            # 尝试按名称匹配
            cur.execute("SELECT id, name FROM projects WHERE name = ?", (d_name,))
            match = cur.fetchone()
            if match:
                cur.execute("UPDATE sys_dept SET project_id = ? WHERE id = ?", (match[0], d_id))
                print(f"    已关联到项目 id={match[0]} '{match[1]}'")
            else:
                # 创建新项目
                cur.execute("SELECT MAX(id) FROM projects")
                max_pid = cur.fetchone()[0] or 0
                new_code = f"XM{max_pid + 1:04d}"
                cur.execute("""
                    INSERT INTO projects (name, code, status, created_at)
                    VALUES (?, ?, 'active', ?)
                """, (d_name, new_code, datetime.now().isoformat()))
                new_pid = cur.lastrowid
                cur.execute("UPDATE sys_dept SET project_id = ? WHERE id = ?", (new_pid, d_id))
                print(f"    创建新项目 id={new_pid} '{d_name}' 并关联")
        elif p_id is None:
            print(f"  部门{id} '{d_name}' project_id={d_proj_id} 但项目不存在，需修复")
            # 清空无效关联
            cur.execute("UPDATE sys_dept SET project_id = NULL WHERE id = ?", (d_id,))
            print(f"    已清空无效关联")

    c.commit()

    # 3. 检查项目表是否有对应部门
    print("\n【3. 检查项目是否有对应部门】")
    cur.execute("SELECT id, name FROM projects ORDER BY id")
    projects = cur.fetchall()
    for p_id, p_name in projects:
        cur.execute("SELECT id, dept_name FROM sys_dept WHERE project_id = ? AND dept_type = 'project'", (p_id,))
        depts = cur.fetchall()
        if not depts:
            print(f"  项目{id} '{p_name}' 无对应项目部，需创建")
            # 创建对应部门
            cur.execute("SELECT MAX(id) FROM sys_dept")
            max_did = cur.fetchone()[0] or 0
            new_code = f"PRJ_{max_did + 1}"
            cur.execute("""
                INSERT INTO sys_dept (dept_code, dept_name, parent_id, dept_type, project_id, sort, status, created_at)
                VALUES (?, ?, 0, 'project', ?, 100, 1, ?)
            """, (new_code, p_name, p_id, datetime.now().isoformat()))
            print(f"    创建部门 '{p_name}' 并关联项目{id}")
        elif len(depts) > 1:
            print(f"  项目{id} '{p_name}' 有多个对应部门: {depts}")
        else:
            print(f"  项目{id} '{p_name}' <-> 部门{depts[0][0]} '{depts[0][1]}' ✓")

    c.commit()

    # 4. 最终统计
    print("\n【4. 最终统计】")
    cur.execute("SELECT COUNT(*) FROM sys_dept WHERE dept_type = 'project'")
    print(f"  项目部数量: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM projects")
    print(f"  项目数量: {cur.fetchone()[0]}")

    cur.execute("""
        SELECT d.dept_name, d.dept_code, p.name, p.code
        FROM sys_dept d
        LEFT JOIN projects p ON d.project_id = p.id
        WHERE d.dept_type = 'project'
        ORDER BY d.id
    """)
    print("\n  项目部-项目对照表:")
    for row in cur.fetchall():
        print(f"    部门: {row[0]} ({row[1]}) <-> 项目: {row[2] or '无'} ({row[3] or '-'})")

    c.close()
    print("\n清理完成。")

if __name__ == '__main__':
    clean_organization_data()