# -*- coding: utf-8 -*-
"""一次性数据治理：把 LEGACY_HQ（旧系统顶级部门"中国铁建"）合并进 CRC 根节点

同一法人实体不应在组织树上出现两个根。合并后 adapter.ORG_CODE_ALIAS
保证后续 ETL 不会再重建 LEGACY_HQ。幂等：已合并则直接跳过。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))
from run import app
from app import db
from app.auth_core.models import AuthOrgUnit, AuthUser

with app.app_context():
    crc = AuthOrgUnit.query.filter_by(org_code='CRC').first()
    hq = AuthOrgUnit.query.filter_by(org_code='LEGACY_HQ').first()

    if not hq:
        print("  ✅ 已是归一状态（LEGACY_HQ 不存在），无需合并")
    elif not crc:
        print("  ⚠️ CRC 根节点缺失，改为直接把 LEGACY_HQ 提升为 CRC")
        hq.org_code = 'CRC'
        hq.org_name = '中国铁建股份有限公司'
        hq.parent_id = 0
        hq.org_level = 1
        hq.legal_entity = True
        db.session.commit()
    else:
        children = AuthOrgUnit.query.filter_by(parent_id=hq.id).all()
        users = AuthUser.query.filter_by(org_id=hq.id).all()
        print("  合并 LEGACY_HQ(#%d) → CRC(#%d)：子节点=%d 用户=%d" % (
            hq.id, crc.id, len(children), len(users)))
        for c in children:
            c.parent_id = crc.id
            db.session.add(c)
        for u in users:
            u.org_id = crc.id
            db.session.add(u)
        # 继承旧节点上的业务属性（若 CRC 侧为空）
        if hq.leader and not crc.leader:
            crc.leader = hq.leader
        if hq.project_id and not crc.project_id:
            crc.project_id = hq.project_id
        crc.dept_type = crc.dept_type or hq.dept_type or 'company'
        db.session.add(crc)
        db.session.delete(hq)
        db.session.commit()
        print("  ✅ 合并完成，LEGACY_HQ 已删除")

    print("--- 最终组织树 ---")
    orgs = AuthOrgUnit.query.order_by(AuthOrgUnit.id).all()

    def show(node, depth=0):
        ucnt = AuthUser.query.filter_by(org_id=node.id).count()
        print("   %s%s  [%s]  L%s%s%s" % (
            '    ' * depth, node.org_name, node.org_code, node.org_level,
            '  法人' if node.legal_entity else '',
            ('  用户%d' % ucnt) if ucnt else ''))
        for c in sorted([o for o in orgs if o.parent_id == node.id], key=lambda x: x.sort or 0):
            show(c, depth + 1)

    roots = [o for o in orgs if not o.parent_id]
    for r in roots:
        show(r)
    print("   根节点数=%d  总节点数=%d  %s" % (
        len(roots), len(orgs), "✅ 单根" if len(roots) == 1 else "⚠️ 多根"))
    orphan = AuthUser.query.filter(AuthUser.org_id.is_(None)).all()
    if orphan:
        print("   ⚠️ 无组织归属用户：%s" % ', '.join(u.username for u in orphan))
