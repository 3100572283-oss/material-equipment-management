#!/bin/bash
# 完整权限验收测试脚本
set -e
HOST="127.0.0.1:5001"
BASE="http://${HOST}"

echo "==============================================="
echo "  统一权限中心 - 完整验收测试"
echo "==============================================="

# 登录函数
login() {
    local user="$1"; local pass="$2"; local cookie="$3"
    rm -f "$cookie"
    curl -s -c "$cookie" -X POST -d "username=${user}&password=${pass}" \
        "${BASE}/auth/login" -L -o /dev/null
}

# 测试访问（带状态码）
test_get() {
    local user="$1"; local path="$2"; local expected="$3"
    local code=$(curl -s -b "/tmp/${user}.cookie" -o /dev/null -w "%{http_code}" "${BASE}${path}")
    if [ "$code" = "$expected" ]; then
        echo "  ✅ ${user} ${path} -> ${code} (符合预期)"
    else
        echo "  ❌ ${user} ${path} -> ${code} (预期 ${expected})"
    fi
}

# 测试JSON API
test_api() {
    local user="$1"; local path="$2"; local expected="$3"
    local code=$(curl -s -b "/tmp/${user}.cookie" -H "Accept: application/json" -o /dev/null -w "%{http_code}" "${BASE}${path}")
    if [ "$code" = "$expected" ]; then
        echo "  ✅ ${user} API ${path} -> ${code} (符合预期)"
    else
        echo "  ❌ ${user} API ${path} -> ${code} (预期 ${expected})"
    fi
}

# 登录所有测试账号
echo ""
echo "--- 登录所有测试账号 ---"
login admin Admin@123456 admin
login test_user_view Test@123456 view
login test_user_noeq Test@123456 noeq
login test_user_a1 Test@123456 a1
login test_user_b1 Test@123456 b1
echo "完成"

# ============ 1. 功能权限验证 ============
echo ""
echo "==============================================="
echo "  1. 功能权限验证"
echo "==============================================="

echo ""
echo "[1.1] admin 全部应通过（200）"
test_get admin /stock_in/ 200
test_get admin /stock_out/ 200
test_get admin /equipment/ 200
test_get admin /system/roles 200
test_get admin /main/dashboard 200

echo ""
echo "[1.2] 角色A (test_user_view: 只查看入库)"
test_get view /stock_in/ 200        # ✅ 有权限
test_get view /equipment/ 403       # ✅ 无权限
test_get view /system/roles 403     # ✅ 无权限
test_get view /stock_out/ 403       # ✅ 无权限

echo ""
echo "[1.3] 角色B (test_user_noeq: 无任何业务权限，仅工作台)"
test_get noeq /stock_in/ 403        # ✅ 无权限
test_get noeq /equipment/ 403       # ✅ 无权限
test_get noeq /stock_out/ 403       # ✅ 无权限
test_get noeq /contract/ 403        # ✅ 无权限
test_get noeq /system/roles 403     # ✅ 无权限

# ============ 2. 数据权限验证 ============
echo ""
echo "==============================================="
echo "  2. 数据权限验证"
echo "==============================================="

echo ""
echo "[2.1] admin 应看到所有10条入库数据"
admin_count=$(curl -s -b /tmp/admin.cookie ${BASE}/stock_in/ | grep -oE 'RK[0-9]+' | sort -u | wc -l)
echo "  admin 看到 ${admin_count} 条"

echo ""
echo "[2.2] 角色C部门A用户(test_user_a1) 应只看到本部门2条"
a1_codes=$(curl -s -b /tmp/a1.cookie ${BASE}/stock_in/ | grep -oE 'RK[0-9]+' | sort -u)
echo "  test_user_a1 看到: ${a1_codes}"

echo ""
echo "[2.3] 角色C部门B用户(test_user_b1) 应只看到本部门2条"
b1_codes=$(curl -s -b /tmp/b1.cookie ${BASE}/stock_in/ | grep -oE 'RK[0-9]+' | sort -u)
echo "  test_user_b1 看到: ${b1_codes}"

# 验证部门A和部门B不重叠
if [[ "$a1_codes" == *"003"* ]] || [[ "$a1_codes" == *"004"* ]]; then
    echo "  ❌ 部门A用户看到了部门B数据"
else
    echo "  ✅ 部门A用户未看到部门B数据"
fi

if [[ "$b1_codes" == *"001"* ]] || [[ "$b1_codes" == *"002"* ]]; then
    echo "  ❌ 部门B用户看到了部门A数据"
else
    echo "  ✅ 部门B用户未看到部门A数据"
fi

# ============ 3. 接口权限验证（POST） ============
echo ""
echo "==============================================="
echo "  3. 接口权限验证（POST/非GET）"
echo "==============================================="

echo ""
echo "[3.1] 角色A (只查看) POST 应被403"
test_get view /stock_in/create 405  # GET不存在
# 测POST
post_code=$(curl -s -b /tmp/view.cookie -X POST -o /dev/null -w "%{http_code}" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -d '{"code":"TEST","stock_in_date":"2026-07-24","project_id":1,"supplier_id":1,"total_quantity":1,"total_amount":1}' \
    ${BASE}/stock_in/create)
echo "  test_user_view POST /stock_in/create -> ${post_code} (期望403或302)"

post_code=$(curl -s -b /tmp/view.cookie -X POST -o /dev/null -w "%{http_code}" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -d '{}' \
    ${BASE}/stock_in/create)
echo "  test_user_view POST /stock_in/create (json) -> ${post_code} (期望403)"

# ============ 4. 模块开关验证 ============
echo ""
echo "==============================================="
echo "  4. 模块开关验证"
echo "==============================================="

# 直接调系统管理获取模块状态
modules_status=$(curl -s -b /tmp/admin.cookie ${BASE}/system/modules/ | grep -oE 'module_status_[a-z]+|module_enabled' | head -20)
echo "当前模块状态: ${modules_status}"

# ============ 5. 配置回显验证 ============
echo ""
echo "==============================================="
echo "  5. 配置回显验证"
echo "==============================================="

# 通过API读取角色A的权限
role_a_perms=$(curl -s -b /tmp/admin.cookie "${BASE}/system/api/role/23/permissions" | head -c 300)
echo "角色A权限配置: ${role_a_perms}"

echo ""
echo "==============================================="
echo "  验收完成"
echo "==============================================="
