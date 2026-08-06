# -*- coding: utf-8 -*-
"""开启行级隔离后，用真实 HTTP 打一遍全部业务列表页，检出 500 / 异常。

对比「关闭态」与「开启态」的状态码，任何由 false->true 引入的新错误都会被标出。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))

import os
from run import app
from app import db
from flask import g
from app.auth_core.models import AuthUser

app.config['WTF_CSRF_ENABLED'] = False
app.config['SESSION_COOKIE_SECURE'] = False
app.login_manager.session_protection = None

SKIP_PREFIX = ('/static', '/auth/', '/api/health')


def list_routes():
    out = []
    for rule in app.url_map.iter_rules():
        if 'GET' not in rule.methods:
            continue
        if rule.arguments:            # 需要路径参数的详情页跳过
            continue
        p = str(rule.rule)
        if p.startswith(SKIP_PREFIX):
            continue
        out.append(p)
    return sorted(set(out))


def client_for(user):
    c = app.test_client()
    with c.session_transaction() as s:
        s['_user_id'] = str(user.id)
        s['_fresh'] = True
    return c


def fresh_get(c, path):
    # 清掉 Flask-Login 在 g 上的用户缓存，避免同上下文串号
    for attr in ('_login_user', '_cached_user', '_ds_cache', '_ds_resolving'):
        try:
            if hasattr(g, attr):
                delattr(g, attr)
        except Exception:
            pass
    return c.get(path, follow_redirects=False)


def sweep(user, paths):
    res = {}
    c = client_for(user)
    for p in paths:
        try:
            r = fresh_get(c, p)
            res[p] = r.status_code
        except Exception as e:
            res[p] = 'EXC:' + type(e).__name__ + ':' + str(e)[:70]
    return res


with app.app_context():
    users = {}
    for un in ('admin', 'zhangwu', '19818623768'):
        u = AuthUser.query.filter_by(username=un).first()
        if u:
            users[un] = u

    paths = list_routes()
    print('待扫描 GET 路由：%d 条' % len(paths))

    os.environ['DATA_SCOPE_ENFORCE'] = 'false'
    before = {un: sweep(u, paths) for un, u in users.items()}

    os.environ['DATA_SCOPE_ENFORCE'] = 'true'
    after = {un: sweep(u, paths) for un, u in users.items()}

    total_new_err = 0
    for un in users:
        b, a = before[un], after[un]
        regress = []
        errs = []
        for p in paths:
            sb, sa = b[p], a[p]
            if isinstance(sa, str) or (isinstance(sa, int) and sa >= 500):
                errs.append('%s -> %s' % (p, sa))
            if sb != sa:
                regress.append('%s: %s -> %s' % (p, sb, sa))
        print('=' * 78)
        print('用户 %s' % un)
        print('  开启后 5xx/异常：%d' % len(errs))
        for e in errs[:15]:
            print('    ❌ %s' % e)
        print('  开关前后状态码变化：%d' % len(regress))
        for r in regress[:15]:
            print('    ⚠️  %s' % r)
        total_new_err += len(errs)

    print('=' * 78)
    codes = {}
    for p in paths:
        c0 = after['admin'][p]
        codes[c0] = codes.get(c0, 0) + 1
    print('admin 开启态状态码分布：%s' % dict(sorted(codes.items(), key=lambda x: str(x[0]))))
    print('结论：开启隔离后 5xx/异常总数 = %d' % total_new_err)
    os.environ['DATA_SCOPE_ENFORCE'] = 'false'
