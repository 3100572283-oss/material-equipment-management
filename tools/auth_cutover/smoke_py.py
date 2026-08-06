# -*- coding: utf-8 -*-
"""auth_core 全量接管冒烟测试（test_client，绕过图形验证码）"""
import re
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))
from run import app
from app import db
from app.auth_core.models import AuthUser
from app.auth_core.gateway import AuthGateway

app.config['WTF_CSRF_ENABLED'] = False
app.config['SESSION_COOKIE_SECURE'] = False
# test_client 直接注入会话，需关掉 Flask-Login 的强会话保护（否则 _id 不匹配即登出）
app.login_manager.session_protection = None

PASS = [0]
FAIL = []


def chk(name, cond, extra=''):
    if cond:
        PASS[0] += 1
        print("  ✅ %s %s" % (name, extra))
    else:
        FAIL.append(name)
        print("  ❌ %s %s" % (name, extra))


def as_user(username):
    """构造已登录 test_client（直接写 flask_login session）

    注意：必须清掉 g._login_user —— 整个脚本跑在同一个 app_context 内时，
    Flask-Login 会把上一个请求的用户缓存在 g 上，导致后续请求全部命中前一个
    身份（生产环境每请求独立 context，不存在该问题）。
    """
    from flask import g
    u = AuthUser.query.filter_by(username=username).first()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess.clear()
        sess['_user_id'] = str(u.id)
        sess['_fresh'] = True
    for k in ('_login_user', '_cached_user'):
        if hasattr(g, k):
            try:
                delattr(g, k)
            except Exception:
                pass
    return c, u


def fresh_get(client, path):
    """发请求前清掉 Flask-Login 在 g 上的用户缓存，避免身份串台"""
    from flask import g
    for k in ('_login_user', '_cached_user'):
        if hasattr(g, k):
            try:
                delattr(g, k)
            except Exception:
                pass
    return client.get(path)


with app.app_context():
    print("=" * 66)
    print("1) 身份源已切换到 AuthUser")
    from app.models import load_user
    lu = load_user(1)
    chk("user_loader 返回 AuthUser", type(lu).__name__ == 'AuthUser', "-> %s" % type(lu).__name__)
    chk("密码校验（原 werkzeug hash 直接可用）", AuthGateway.authenticate('admin', 'Admin@2024') == 1)
    chk("AuthUser 实现 Flask-Login 协议",
        all(hasattr(lu, a) for a in ('is_authenticated', 'is_active', 'get_id', 'is_anonymous')))

    print("=" * 66)
    print("2) 权限中台三页 + API（admin）")
    c, admin = as_user('admin')
    for p in ['/auth_core/', '/auth_core/orgs', '/auth_core/roles', '/auth_core/users']:
        r = fresh_get(c, p)
        chk("GET %s" % p, r.status_code == 200, "-> %s" % r.status_code)
    for p in ['/auth_core/api/org/tree', '/auth_core/api/role/list',
              '/auth_core/api/user/list', '/auth_core/api/meta']:
        r = fresh_get(c, p)
        chk("GET %s" % p, r.status_code == 200, "-> %s" % r.status_code)
    r = fresh_get(c, '/auth_core/api/user/list')
    if r.status_code == 200:
        try:
            data = r.get_json()
            n = len(data.get('data', data) if isinstance(data, dict) else data)
            chk("用户列表返回 7 条", n == 7, "-> %d 条" % n)
        except Exception as e:
            chk("用户列表 JSON 可解析", False, str(e)[:60])

    print("=" * 66)
    print("3) 旧 RBAC 路由被绞杀")
    for p, kw in [('/system/depts', 'orgs'), ('/system/roles', 'roles'),
                  ('/system/menus', 'roles'), ('/admin/users', 'users')]:
        r = fresh_get(c, p)
        loc = r.headers.get('Location', '')
        chk("%s 重定向新中台" % p, r.status_code in (301, 302) and 'auth_core' in loc and kw in loc,
            "-> %s %s" % (r.status_code, loc))

    print("=" * 66)
    print("4) 业务模块可访问（admin，scope=all）")
    for p in ['/material/', '/equipment/', '/project/', '/supplier/']:
        r = fresh_get(c, p)
        chk("GET %s" % p, r.status_code == 200, "-> %s" % r.status_code)

    print("=" * 66)
    print("5) 权限裁决走 AuthGateway（业务模块隔离）")
    for uname in ['admin', 'zhangwu', 'branch_test', '19818623768']:
        cc, uu = as_user(uname)
        sc = AuthGateway.get_data_scope(uu.id)
        perms = AuthGateway.get_permissions(uu.id)
        r = fresh_get(cc, '/auth_core/users')
        allowed = uu.has_permission('system:user:view') or uu.is_admin()
        blocked = r.status_code in (302, 403)
        state = '可进' if r.status_code == 200 else ('被拦 %s' % r.status_code)
        ok = (r.status_code == 200) == bool(allowed)
        chk("%-12s 中台用户页 %s" % (uname, state), ok,
            "| scope=%s projects=%s perms=%d admin=%s" % (
                sc['scope_type'], sc['project_ids'], len(perms), uu.is_admin()))

    print("=" * 66)
    print("6) permission_service 已委托 AuthGateway（业务列表数据范围）")
    from app.services.permission_service import permission_service as PS
    for uname in ['admin', 'zhangwu', '19818623768']:
        u = AuthUser.query.filter_by(username=uname).first()
        try:
            scope = PS.get_user_data_scope(u)
            projs = PS.get_user_allowed_projects(u)
            visible = PS.get_user_visible_projects(u)
            gw = AuthGateway.get_data_scope(u.id)
            consistent = (scope['scope'] == gw['scope_type'])
            chk("%-12s PS.scope=%-6s 与 Gateway 一致" % (uname, scope['scope']), consistent,
                "| allowed=%s 可见项目=%d" % (
                    projs if projs is not None else 'ALL(None)',
                    len(visible) if visible is not None else -1))
        except Exception as e:
            chk("%-12s permission_service 调用" % uname, False, "异常: %s" % str(e)[:90])

    print("=" * 66)
    print("7) 旧 users/sys_* 表不再作为身份源")
    from app.models import User as LegacyUser
    lc = LegacyUser.query.count()
    ac = AuthUser.query.count()
    chk("旧表仍在（可回退）", lc == 7, "-> legacy users=%d" % lc)
    chk("新表已全量承载", ac == lc, "-> auth_core users=%d" % ac)
    chk("load_user 不再返回旧 User", type(load_user(1)).__name__ != 'User')

    print("=" * 66)
    print("结果：通过 %d，失败 %d" % (PASS[0], len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("   - %s" % f)
