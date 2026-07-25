"""数据权限测试 - 验证不同部门用户看到不同数据"""
import requests
import re

BASE = "http://127.0.0.1:5001"

def login_and_get_count(user, pwd):
    """登录并访问入库列表，返回页面中数据条数"""
    s = requests.Session()
    # 登录
    s.post(f"{BASE}/auth/login", data={"username": user, "password": pwd}, allow_redirects=True)
    # 访问入库列表
    r = s.get(f"{BASE}/stock_in/")
    if r.status_code != 200:
        return r.status_code, []
    # 统计行数 - 查找 <tr class="data-row"> 或包含 RK2026 编号的元素
    codes = re.findall(r'RK2026\d{6}', r.text)
    return r.status_code, list(set(codes))

# 1. admin 看所有
print("=" * 60)
print("数据权限验证测试")
print("=" * 60)
print()
print("【期望】admin看到所有10条，部门A用户只看自己部门2条")
print()

# admin 全量
code, codes = login_and_get_count("admin", "Admin@123456")
print(f"admin: HTTP {code} 看到入库单 {len(codes)} 条")
if codes:
    print(f"  示例: {sorted(codes)[:3]}")

# 角色C 部门A (总公司) 用户
code, codes = login_and_get_count("test_user_a1", "Test@123456")
print(f"test_user_a1 (部门总公司): HTTP {code} 看到入库单 {len(codes)} 条")
if codes:
    print(f"  示例: {sorted(codes)[:3]}")

# 角色C 部门B (物资部) 用户
code, codes = login_and_get_count("test_user_b1", "Test@123456")
print(f"test_user_b1 (部门物资部): HTTP {code} 看到入库单 {len(codes)} 条")
if codes:
    print(f"  示例: {sorted(codes)[:3]}")

print()
print("【结果分析】")
print("  - admin 应看到 10 条")
print("  - 部门A用户 应看到 2 条（仅本部门数据）")
print("  - 部门B用户 应看到 2 条（仅本部门数据）")
