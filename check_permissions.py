#!/usr/bin/env python3
"""
权限体系验证脚本
运行方式: python3 check_permissions.py
"""
import sqlite3
import sys

def check():
    conn = sqlite3.connect("material_mgmt.db")
    c = conn.cursor()
    errors = []
    warnings = []

    print("=" * 60)
    print("物资设备管理系统 - 权限体系数据验证报告")
    print("=" * 60)

    # 1. 检查核心表是否存在
    print("\n【1. 核心表检查】")
    for tbl in ["sys_menu", "sys_role", "sys_role_menu", "sys_module", "users", "sys_dept"]:
        c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
        if c.fetchone()[0] == 0:
            errors.append("表 %s 不存在" % tbl)
        else:
            print("  OK %s" % tbl)

    # 2. 检查sys_menu数据
    print("\n【2. 菜单权限点检查】")
    c.execute("SELECT menu_type, COUNT(*) FROM sys_menu GROUP BY menu_type")
    menu_counts = dict(c.fetchall())
    print("  目录(catalog): %s" % menu_counts.get("catalog", 0))
    print("  菜单(menu): %s" % menu_counts.get("menu", 0))
    print("  按钮(button): %s" % menu_counts.get("button", 0))

    total_menus = sum(menu_counts.values())
    if total_menus < 50:
        errors.append("菜单数量过少(%s)，权限点可能未初始化" % total_menus)

    c.execute("SELECT COUNT(*) FROM sys_menu WHERE permission IS NOT NULL AND permission != ''")
    perm_count = c.fetchone()[0]
    print("  有permission字段: %s/%s" % (perm_count, total_menus))
    if perm_count < total_menus * 0.8:
        warnings.append("部分菜单缺少permission字段 (%s/%s)" % (perm_count, total_menus))

    # 3. 检查角色数据
    print("\n【3. 角色检查】")
    c.execute("SELECT COUNT(*) FROM sys_role")
    role_count = c.fetchone()[0]
    print("  角色总数: %s" % role_count)

    c.execute("SELECT role_code, role_name FROM sys_role WHERE role_code='super_admin'")
    super_admin = c.fetchone()
    if not super_admin:
        errors.append("缺少 super_admin 角色")
    else:
        print("  OK super_admin 角色存在: %s" % super_admin[1])

    # 4. 检查角色权限关联
    print("\n【4. 角色权限关联检查】")
    c.execute("SELECT COUNT(*) FROM sys_role_menu")
    rm_count = c.fetchone()[0]
    print("  关联总数: %s" % rm_count)

    c.execute("SELECT role_id, COUNT(*) FROM sys_role_menu GROUP BY role_id")
    role_perms = c.fetchall()
    for rid, cnt in sorted(role_perms, key=lambda x: -x[1])[:5]:
        c.execute("SELECT role_code, role_name FROM sys_role WHERE id=?", (rid,))
        r = c.fetchone()
        print("  %s (%s): %s 个权限" % (r[0], r[1], cnt))

    # 5. 检查super_admin是否有完整权限
    print("\n【5. 超级管理员权限完整性检查】")
    c.execute("SELECT id FROM sys_role WHERE role_code='super_admin'")
    sa_id = c.fetchone()
    if sa_id:
        sa_id = sa_id[0]
        c.execute("SELECT operation, COUNT(*) FROM sys_role_menu WHERE role_id=? GROUP BY operation", (sa_id,))
        sa_ops = dict(c.fetchall())
        all_ops = ["view", "create", "edit", "delete", "import", "export", "approve", "print"]
        for op in all_ops:
            cnt = sa_ops.get(op, 0)
            status = "OK" if cnt >= total_menus * 0.9 else "NG"
            print("  %s %s: %s" % (status, op, cnt))
            if cnt < total_menus * 0.9:
                errors.append("super_admin 缺少 %s 权限 (%s/%s)" % (op, cnt, total_menus))

    # 6. 检查admin用户
    print("\n【6. admin用户检查】")
    c.execute("SELECT id, username, role_id, name, dept_id FROM users WHERE username='admin'")
    admin = c.fetchone()
    if not admin:
        errors.append("admin用户不存在")
    else:
        print("  admin用户: id=%s, role_id=%s, name=%s, dept_id=%s" % (admin[0], admin[2], admin[3], admin[4]))
        if admin[2] != sa_id:
            warnings.append("admin用户role_id(%s)不等于super_admin角色id(%s)" % (admin[2], sa_id))
        else:
            print("  OK admin用户已关联super_admin角色")

    # 7. 检查模块注册表
    print("\n【7. 模块注册表检查】")
    c.execute("SELECT module_key, module_name, status, is_required FROM sys_module ORDER BY sort")
    for row in c.fetchall():
        status_icon = "OK" if row[2] else "OFF"
        req_text = "必需" if row[3] else "可选"
        print("  %s %s (%s) - %s" % (status_icon, row[0], row[1], req_text))

    # 8. 检查权限标识重复
    print("\n【8. 权限标识唯一性检查】")
    c.execute("SELECT permission, COUNT(*) FROM sys_menu WHERE permission IS NOT NULL GROUP BY permission HAVING COUNT(*) > 1")
    dups = c.fetchall()
    if dups:
        warnings.append("发现 %s 个重复permission" % len(dups))
        for perm, cnt in dups:
            print("  WARN %s: %s 个菜单" % (perm, cnt))
    else:
        print("  OK 无重复permission")

    # 总结
    print("\n" + "=" * 60)
    print("验证结果总结")
    print("=" * 60)
    if errors:
        print("FAIL 错误: %s 项" % len(errors))
        for e in errors:
            print("  - %s" % e)
    if warnings:
        print("WARN 警告: %s 项" % len(warnings))
        for w in warnings:
            print("  - %s" % w)
    if not errors and not warnings:
        print("PASS 所有检查通过！权限体系数据完整。")

    conn.close()
    return len(errors) == 0

if __name__ == "__main__":
    ok = check()
    sys.exit(0 if ok else 1)
