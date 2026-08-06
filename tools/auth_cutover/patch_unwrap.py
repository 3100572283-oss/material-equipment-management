# -*- coding: utf-8 -*-
"""给 permission_service 所有接收 user 的方法统一插入 LocalProxy 解包。

根因：flask_login.current_user 是 LocalProxy，isinstance(user, AuthUser) 恒为 False，
导致「委托 AuthGateway」的分支在真实请求里被整体跳过，静默退回旧 RBAC 逻辑。
"""
import ast
import io

P = 'app/services/permission_service.py'
HELPER = '''

def _unwrap_user(user):
    """解包 flask_login 的 LocalProxy，拿到真实用户对象。

    current_user 是 LocalProxy，isinstance(user, AuthUser) 恒为 False，
    会让所有「委托 AuthGateway」的分支被静默跳过。所有对外方法入口统一解包。
    user 为 None 时取当前登录用户（与原有 `if user is None: user = current_user` 语义一致）。
    """
    if user is None:
        from flask_login import current_user as _cu
        user = _cu
    getter = getattr(user, '_get_current_object', None)
    if callable(getter):
        try:
            user = getter()
        except Exception:
            pass
    return user

'''

src = io.open(P, encoding='utf-8').read()
if '_unwrap_user' in src:
    print('already patched')
    raise SystemExit(0)

tree = ast.parse(src)
lines = src.split('\n')

targets = []
for node in ast.walk(tree):
    if not isinstance(node, ast.FunctionDef):
        continue
    if 'user' not in [a.arg for a in node.args.args]:
        continue
    body = node.body
    first = body[0]
    is_doc = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
              and isinstance(first.value.value, str))
    if is_doc:
        if len(body) < 2:
            continue
        stmt = body[1]
    else:
        stmt = first
    targets.append((stmt.lineno, stmt.col_offset, node.name))

targets.sort(key=lambda x: -x[0])
for lineno, col, name in targets:
    lines.insert(lineno - 1, ' ' * col + 'user = _unwrap_user(user)')

out = '\n'.join(lines)

# 在 class 定义之前插入 helper
idx = out.find('class PermissionService')
assert idx > 0
out = out[:idx] + HELPER.lstrip('\n') + '\n' + out[idx:]

io.open(P, 'w', encoding='utf-8').write(out)
print('patched %d 个方法:' % len(targets))
for _, _, n in sorted(targets, key=lambda x: x[2]):
    print('   -', n)
