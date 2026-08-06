# -*- coding: utf-8 -*-
"""清理 auth_core 组织树中的示例占位节点（CORP/SUBCO/PROJ）

安全：仅当节点无用户、无子节点、无项目绑定时才删除。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))
from run import app
from app import db
from app.auth_core.models import AuthOrgUnit, AuthUser

TARGETS = ['CORP', 'SUBCO', 'PROJ']

with app.app_context():
    # 自底向上删（PROJ -> SUBCO -> CORP）
    for code in reversed(TARGETS):
        ou = AuthOrgUnit.query.filter_by(org_code=code).first()
        if not ou:
            print("  跳过 %s：不存在" % code)
            continue
        ucnt = AuthUser.query.filter_by(org_id=ou.id).count()
        ccnt = AuthOrgUnit.query.filter_by(parent_id=ou.id).count()
        if ucnt or ccnt or ou.project_id:
            print("  ⚠️ 保留 %s(%s)：用户=%d 子节点=%d project_id=%s" % (
                code, ou.org_name, ucnt, ccnt, ou.project_id))
            continue
        db.session.delete(ou)
        print("  ✅ 已删除示例节点 %s(%s)" % (code, ou.org_name))
    db.session.commit()

    print("--- 清理后组织树 ---")
    orgs = AuthOrgUnit.query.order_by(AuthOrgUnit.id).all()
    by_id = {o.id: o for o in orgs}

    def show(node, depth=0):
        print("   %s%s  [%s]" % ('    ' * depth, node.org_name, node.org_code))
        for c in sorted([o for o in orgs if o.parent_id == node.id], key=lambda x: x.sort or 0):
            show(c, depth + 1)

    for r in [o for o in orgs if not o.parent_id]:
        show(r)
    print("   总节点数 = %d" % len(orgs))
