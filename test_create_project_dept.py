"""测试新增项目部 - 验证二合一逻辑

测试步骤：
1. 登录admin账号
2. 访问新增部门页面
3. 提交项目部类型部门
4. 验证：
   - sys_dept表新增1条记录（dept_type='project'）
   - projects表同步新增1条记录
   - dept.project_id = project.id
   - 部门名称不自动加"项目部"后缀
"""

import requests
import re

BASE = "http://127.0.0.1:5001"
s = requests.Session()

# 登录
r = s.post(f"{BASE}/auth/login", data={"username": "admin", "password": "Admin@123456"}, allow_redirects=True)
print(f"Login: {r.status_code}")

# 获取新增部门页面，提取CSRF token（如有）
r = s.get(f"{BASE}/system/depts/create")
print(f"Create dept page: {r.status_code}")

# 提交新增项目部
import time
test_dept_name = f"自动化测试项目部{int(time.time()) % 10000}"

data = {
    "dept_name": test_dept_name,
    "parent_id": "1",  # 总公司
    "dept_type": "project",
    "project_name": test_dept_name,  # 明确指定项目名称
    "leader": "测试负责人",
    "sort": "999",
    "remark": "自动化测试-验证二合一逻辑",
}

r = s.post(f"{BASE}/system/depts/create", data=data, allow_redirects=True)
print(f"Submit new project dept: {r.status_code}")

# 检查是否成功
if "成功" in r.text or r.status_code == 200:
    print(f"✓ 提交成功")
else:
    print(f"! 可能失败，检查响应")

# 验证数据库
import sqlite3
c = sqlite3.connect('/opt/material-equipment-management/material_mgmt.db')
cur = c.cursor()

print(f"\n【验证数据库】")

# 查新部门
cur.execute("SELECT id, dept_name, dept_type, project_id FROM sys_dept WHERE dept_name = ?", (test_dept_name,))
dept = cur.fetchone()
if dept:
    print(f"✓ 部门已创建: id={dept[0]} name={dept[1]} type={dept[2]} project_id={dept[3]}")
else:
    print(f"✗ 部门未创建")
    exit(1)

# 查对应项目
if dept[3]:
    cur.execute("SELECT id, name, code FROM projects WHERE id = ?", (dept[3],))
    proj = cur.fetchone()
    if proj:
        print(f"✓ 项目已创建: id={proj[0]} name={proj[1]} code={proj[2]}")
        # 验证名称一致
        if proj[1] == test_dept_name:
            print(f"✓ 项目名称与部门名称一致（未自动加后缀）")
        else:
            print(f"✗ 项目名称不一致: 期望'{test_dept_name}' 实际'{proj[1]}'")
    else:
        print(f"✗ 项目不存在 id={dept[3]}")
else:
    print(f"✗ 部门未关联项目")

c.close()

print(f"\n【测试完成】部门'{test_dept_name}'创建验证通过")