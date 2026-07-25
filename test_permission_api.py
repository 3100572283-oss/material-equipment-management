#!/usr/bin/env python3
"""测试权限API接口"""
import requests
import json

def test_admin_permissions():
    session = requests.Session()
    session.post("http://127.0.0.1:5001/auth/login", data={"username": "admin", "password": "Admin@123456"}, allow_redirects=False)
    r = session.get("http://127.0.0.1:5001/auth/api/user/permissions")
    print(f"admin API状态: {r.status_code}")
    if r.status_code == 200:
        data = json.loads(r.text)
        print(f"  菜单树节点数: {len(data['menus'])} 个目录")
        print(f"  权限标识数: {len(data['permissions'])} 个")
        print(f"  数据权限: {data['dataScope']}")
        print(f"  可见项目数: {len(data['projects'])} 个")
        print(f"  示例权限标识:")
        for p in data['permissions'][:5]:
            print(f"    - {p}")
    else:
        print(f"  错误: {r.text}")

def test_viewer_permissions():
    session = requests.Session()
    session.post("http://127.0.0.1:5001/auth/login", data={"username": "test_user_a1", "password": "Test@123456"}, allow_redirects=False)
    r = session.get("http://127.0.0.1:5001/auth/api/user/permissions")
    print(f"\ntest_user_a1 API状态: {r.status_code}")
    if r.status_code == 200:
        data = json.loads(r.text)
        print(f"  菜单树节点数: {len(data['menus'])} 个目录")
        print(f"  权限标识数: {len(data['permissions'])} 个")
        print(f"  数据权限: {data['dataScope']}")
        print(f"  可见项目数: {len(data['projects'])} 个")

if __name__ == '__main__':
    test_admin_permissions()
    test_viewer_permissions()