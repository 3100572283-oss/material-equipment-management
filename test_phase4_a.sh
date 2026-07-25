#!/bin/bash
# 阶段四验收测试脚本
set +e
BASE='http://127.0.0.1:80'
COOKIE=/tmp/cookies.txt
rm -f $COOKIE

echo "=== 阶段四验收测试 ==="
echo

echo "[1] 角色A登录 (test_user_view)"
HTTP=$(curl -s -c $COOKIE -b $COOKIE -L "$BASE/auth/login" -d "username=test_user_view&password=Test@123456" -o /dev/null -w "%{http_code}")
echo "login=$HTTP"

echo
echo "[2] 角色A获取菜单权限"
curl -s -b $COOKIE "$BASE/system/api/user/menus" > /tmp/menu.json
echo "原始响应:"
head -c 500 /tmp/menu.json
echo
python3 -c "
import json
try:
    d=json.load(open('/tmp/menu.json'))
    print('code=', d.get('code'))
    print('msg=', d.get('message'))
    data=d.get('data') or []
    print(f'  顶层菜单数: {len(data)}')
    for x in data:
        print('  -', x.get('name'), x.get('code'), 'children:', len(x.get('children') or []))
        for c in (x.get('children') or [])[:8]:
            print('     -', c.get('name'), c.get('code'))
except Exception as e:
    print('parse error:', e)
"

echo
echo "[3] 角色A权限标识列表（前20个）"
curl -s -b $COOKIE "$BASE/system/api/user/permissions" > /tmp/perms.json
python3 -c "
import json
try:
    d=json.load(open('/tmp/perms.json'))
    perms=d.get('data') or []
    print(f'  共 {len(perms)} 个权限标识')
    for p in perms[:20]:
        print('  -', p)
except Exception as e:
    print('parse error:', e)
"

echo
echo "[4] 角色A访问各页面（HTTP状态）"
for path in "/stock_in/" "/stock_out/" "/contract/" "/equipment/" "/master/material_master/"; do
    HTTP=$(curl -s -b $COOKIE -o /dev/null -w "%{http_code}" "$BASE$path")
    echo "  $path => $HTTP"
done

echo
echo "[5] 角色A POST入库创建（应403,只有view权限）"
HTTP=$(curl -s -b $COOKIE -o /dev/null -w "%{http_code}" -X POST "$BASE/stock_in/create" -d "test=1")
echo "  /stock_in/create POST => $HTTP"

echo
rm -f $COOKIE
echo "=== 角色A测试完成 ==="
