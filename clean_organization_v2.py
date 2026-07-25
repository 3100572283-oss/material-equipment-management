"""第一阶段修复V2：精确清理项目部重复数据

策略：
1. 按 project_id 去重（不是按名称）
2. 保留合理的 parent_id 位置的记录
3. 删除其他重复记录
"""

import sqlite3
from datetime import datetime

DB_PATH = '/opt/material-equipment-management/material_mgmt.db'

def clean_project_depts_v2():
    c = sqlite3.connect(DB_PATH)
    cur = c.cursor()

    print("=" * 60)
    print("组织架构数据清理修复 V2（按project_id去重）")
    print("=" * 60)

    # 1. 查找所有项目部类型部门
    print("\n【1. 分析所有项目部】")
    cur.execute("""
        SELECT d.id, d.dept_name, d.dept_code, d.parent_id, d.project_id, p.name as proj_name
        FROM sys_dept d
        LEFT JOIN projects p ON d.project_id = p.id
        WHERE d.dept_type = 'project'
        ORDER BY d.project_id, d.id
    """)
    all_project_depts = cur.fetchall()
    print(f"  项目部总数: {len(all_project_depts)}")
    for r in all_project_depts:
        print(f"    id={r[0]:3d} name={r[1]:30s} parent={r[3]} proj_id={r[4]} proj_name={r[5]}")

    # 2. 按 project_id 分组
    print("\n【2. 按 project_id 分组】")
    project_dept_map = {}
    for r in all_project_depts:
        proj_id = r[4]
        if proj_id not in project_dept_map:
            project_dept_map[proj_id] = []
        project_dept_map[proj_id].append(r)

    # 3. 处理重复
    print("\n【3. 处理重复项目部门】")
    to_delete = []

    for proj_id, depts in project_dept_map.items():
        if len(depts) > 1:
            print(f"\n  项目ID {proj_id} 有 {len(depts)} 个部门记录:")
            for d in depts:
                print(f"    id={d[0]} parent={d[3]}")

            # 策略：保留parent_id在分公司(11或12)下的，删除parent_id在总公司(1)下的
            # 因为项目部应该挂在对应分公司下面
            # 如果都有相同的parent_id，保留id最小的

            parents = [d[3] for d in depts]
            if 11 in parents or 12 in parents:
                # 优先保留在分公司下的
                for d in depts:
                    if d[3] in [11, 12]:
                        keep_id = d[0]
                        break
            else:
                # 否则保留id最小的
                keep_id = min([d[0] for d in depts])

            # 记录要删除的
            for d in depts:
                if d[0] != keep_id:
                    to_delete.append(d[0])
                    print(f"    -> 删除 id={d[0]}")

            # 更新保留记录的parent_id（如果需要）
            for d in depts:
                if d[0] == keep_id and d[3] == 1:
                    # 需要移动到合适的分公司下
                    # 根据项目名称判断应该挂在哪个分公司
                    proj_name = d[5] or ''
                    if '郑州' in proj_name or '中原建设' in proj_name:
                        new_parent = 11
                    elif '洛阳' in proj_name:
                        new_parent = 12
                    else:
                        new_parent = 1
                    if new_parent != d[3]:
                        cur.execute("UPDATE sys_dept SET parent_id = ? WHERE id = ?", (new_parent, d[0]))
                        print(f"    -> 更新 id={d[0]} parent_id: {d[3]} -> {new_parent}")
        else:
            # 只有一条，检查parent_id是否合理
            d = depts[0]
            proj_name = d[5] or ''
            if '郑州' in proj_name or '中原建设' in proj_name:
                expected_parent = 11
            elif '洛阳' in proj_name:
                expected_parent = 12
            elif '总部' in proj_name or '测试项目部1' in proj_name:
                expected_parent = 1
            else:
                expected_parent = d[3]

            if d[3] != expected_parent and expected_parent != d[3]:
                cur.execute("UPDATE sys_dept SET parent_id = ? WHERE id = ?", (expected_parent, d[0]))
                print(f"  更新 id={d[0]} '{d[1]}' parent_id: {d[3]} -> {expected_parent}")

    # 4. 执行删除
    if to_delete:
        print(f"\n【4. 执行删除】")
        for del_id in to_delete:
            # 移动用户
            cur.execute("SELECT COUNT(*) FROM users WHERE dept_id = ?", (del_id,))
            user_cnt = cur.fetchone()[0]
            if user_cnt > 0 and len(project_dept_map) > 0:
                # 找到保留的部门ID
                for proj_id, depts in project_dept_map.items():
                    for d in depts:
                        if d[0] not in to_delete and d[4] == proj_id:
                            keep_id = d[0]
                            cur.execute("UPDATE users SET dept_id = ? WHERE dept_id = ?", (keep_id, del_id))
                            print(f"  移动 {user_cnt} 个用户: 部门{del_id} -> {keep_id}")
                            break

            # 移动子部门
            cur.execute("SELECT COUNT(*) FROM sys_dept WHERE parent_id = ?", (del_id,))
            child_cnt = cur.fetchone()[0]
            if child_cnt > 0:
                # 子部门移到保留的部门下
                for proj_id, depts in project_dept_map.items():
                    for d in depts:
                        if d[0] not in to_delete and d[4] == proj_id:
                            cur.execute("UPDATE sys_dept SET parent_id = ? WHERE parent_id = ?", (d[0], del_id))
                            print(f"  移动 {child_cnt} 个子部门: 父部门{del_id} -> {d[0]}")
                            break

            cur.execute("DELETE FROM sys_dept WHERE id = ?", (del_id,))
            print(f"  删除部门 id={del_id}")

    c.commit()

    # 5. 验证结果
    print("\n【5. 验证结果】")
    cur.execute("SELECT COUNT(*) FROM sys_dept WHERE dept_type = 'project'")
    print(f"  项目部数量: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM projects")
    print(f"  项目数量: {cur.fetchone()[0]}")

    cur.execute("""
        SELECT d.id, d.dept_name, d.parent_id, d.project_id, p.name
        FROM sys_dept d
        LEFT JOIN projects p ON d.project_id = p.id
        WHERE d.dept_type = 'project'
        ORDER BY d.project_id
    """)
    print("\n  最终对照表:")
    for r in cur.fetchall():
        print(f"    id={r[0]:3d} {r[1]:30s} parent={r[2]} proj_id={r[3]} proj_name={r[4]}")

    c.close()
    print("\n清理完成。")

if __name__ == '__main__':
    clean_project_depts_v2()