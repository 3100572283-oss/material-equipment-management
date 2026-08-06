# -*- coding: utf-8 -*-
"""探查带 project_id 的模型：可空性、NULL 行数、总行数 —— 用于评估集中式行级隔离的影响面"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))

from run import app
from app import db
from sqlalchemy import text

with app.app_context():
    rows = []
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        try:
            t = mapper.local_table
        except Exception:
            continue
        if t is None or 'project_id' not in {c.name for c in t.columns}:
            continue
        col = t.columns['project_id']
        try:
            total = db.session.execute(text('SELECT COUNT(*) FROM `%s`' % t.name)).scalar()
            nulls = db.session.execute(text('SELECT COUNT(*) FROM `%s` WHERE project_id IS NULL' % t.name)).scalar()
            distinct = db.session.execute(
                text('SELECT COUNT(DISTINCT project_id) FROM `%s`' % t.name)).scalar()
        except Exception as e:
            rows.append((cls.__name__, t.name, '?', '?', '?', 'ERR:' + str(e)[:40]))
            continue
        rows.append((cls.__name__, t.name, 'NULLABLE' if col.nullable else 'NOT NULL',
                     total, nulls, distinct))

    rows.sort(key=lambda r: (-(r[4] if isinstance(r[4], int) else 0),
                             -(r[3] if isinstance(r[3], int) else 0)))
    print('%-30s %-30s %-9s %8s %8s %6s' % ('MODEL', 'TABLE', 'NULLABLE', 'ROWS', 'NULL_PID', 'PROJS'))
    print('-' * 100)
    for r in rows:
        print('%-30s %-30s %-9s %8s %8s %6s' % r)
    print('-' * 100)
    print('模型数 = %d' % len(rows))
    nz = [r for r in rows if isinstance(r[4], int) and r[4] > 0]
    print('存在 project_id 为 NULL 的表 = %d  ->  这些表若直接过滤会丢数据，须放行 NULL' % len(nz))
