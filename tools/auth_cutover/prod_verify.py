# -*- coding: utf-8 -*-
"""生产态验证：不覆盖任何环境变量，直接用 .env 的真实开关跑。"""
import os as _os, sys as _sys
_sys.path.insert(0, '/opt/material-equipment-management')
from run import app
from app import db
from flask import g
from sqlalchemy import text
from app.auth_core.models import AuthUser
from app.auth_core.data_scope import enforcing_enabled, current_allowed_projects

app.config['WTF_CSRF_ENABLED'] = False
app.login_manager.session_protection = None
P = F = 0
def ck(ok, msg, extra=''):
    global P, F
    globals()['P' if ok else 'F'] = (P + 1) if ok else P
    if not ok: globals()['F'] = F + 1
    print(('  \u2705 ' if ok else '  \u274c ') + msg + ' ' + extra)

PAGES = ['/', '/supplier/', '/stock_in/', '/contract/', '/project/',
         '/inventory/', '/master/', '/report/', '/mobile/']

with app.app_context():
    print('=' * 70)
    print('0) 真实开关状态（未做任何 os.environ 覆盖）')
    ck(enforcing_enabled(), 'DATA_SCOPE_ENFORCE 已启用',
       '= %s' % _os.environ.get('DATA_SCOPE_ENFORCE'))
    ck(_os.environ.get('AUTH_CORE_ENABLED') == 'true', 'AUTH_CORE_ENABLED 已启用')

    actives = [r[0] for r in db.session.execute(text(
        "SELECT username FROM users WHERE status='active' ORDER BY id"))]
    print('\n在用账号: %s' % ', '.join(actives))

    from app.auth_core.data_scope import _build_registry, _TABLE_RULES
    _build_registry(db)
    t2m = {}
    for mp in db.Model.registry.mappers:
        if mp.local_table is not None:
            t2m.setdefault(mp.local_table.name, mp.class_)

    for un in actives:
        u = AuthUser.query.filter_by(username=un).first()
        print('=' * 70)
        c = app.test_client()
        with c.session_transaction() as s:
            s['_user_id'] = str(u.id); s['_fresh'] = True

        bad = []
        for p in PAGES:
            g.pop('_login_user', None)
            r = c.get(p, follow_redirects=False)
            if r.status_code >= 500:
                bad.append('%s->%s' % (p, r.status_code))
        ck(not bad, '%-13s 关键页面无 5xx' % un, '| ' + (';'.join(bad) if bad else 'ok'))

        with app.test_request_context('/'):
            from flask_login import login_user, logout_user
            login_user(u)
            allowed = current_allowed_projects()
            rows = {}
            for t in ('stock_ins', 'contracts', 'inventory', 'project_supplier',
                      'suppliers', 'materials', 'categories', 'projects'):
                cls = t2m.get(t)
                rows[t] = cls.query.count() if cls else None
            logout_user()
        print('  可见项目 = %s' % (allowed if allowed is not None else '全部(管理员)'))
        print('  单据: stock_ins=%s contracts=%s inventory=%s project_supplier=%s projects=%s'
              % (rows['stock_ins'], rows['contracts'], rows['inventory'],
                 rows['project_supplier'], rows['projects']))
        print('  主数据: suppliers=%s materials=%s categories=%s'
              % (rows['suppliers'], rows['materials'], rows['categories']))

        full_s = db.session.execute(text('SELECT COUNT(*) FROM suppliers')).scalar()
        full_m = db.session.execute(text('SELECT COUNT(*) FROM materials')).scalar()
        full_c = db.session.execute(text('SELECT COUNT(*) FROM categories')).scalar()
        ck(rows['suppliers'] == full_s and rows['materials'] == full_m
           and rows['categories'] == full_c,
           '%-13s 公司主数据全量可见（业务不中断）' % un,
           '| %s/%s 供应商, %s/%s 物料, %s/%s 分类'
           % (rows['suppliers'], full_s, rows['materials'], full_m, rows['categories'], full_c))

        if allowed is not None:
            leak = db.session.execute(text(
                'SELECT COUNT(*) FROM contracts WHERE project_id NOT IN (%s)'
                % ','.join(str(int(x)) for x in allowed))).scalar()
            ck(rows['contracts'] + leak == db.session.execute(
                text('SELECT COUNT(*) FROM contracts')).scalar(),
               '%-13s 合同越权行已被隔离' % un, '| 越权 %s 行不可见' % leak)

    print('=' * 70)
    print('生产态验证：通过 %d，失败 %d' % (P, F))
