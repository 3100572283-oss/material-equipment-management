# -*- coding: utf-8 -*-
"""ETL 结果校验：组织树 / 角色 / 用户 / 权限 / 数据范围"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))
from run import app
from app import db
from app.auth_core.models import (AuthOrgUnit, AuthRole, AuthUser, AuthUserRole,
                                  AuthPermission, AuthRolePermission,
                                  AuthDataScope, AuthUserDataScope)
from app.auth_core.gateway import AuthGateway
from app.models import User as LegacyUser, SysDept, SysRole

with app.app_context():
    print("=" * 62)
    print("1) 组织树（新 auth_core_org_unit）")
    orgs = AuthOrgUnit.query.order_by(AuthOrgUnit.id).all()
    by_id = {o.id: o for o in orgs}
    for o in orgs:
        p = by_id.get(o.parent_id)
        ptxt = ("%s(#%s)" % (p.org_name, p.id)) if p else ("ROOT" if not o.parent_id else "!!悬空parent=%s" % o.parent_id)
        print("   #%-3s %-12s %-22s parent=%s" % (o.id, o.org_code, o.org_name, ptxt))
    orphan = [o for o in orgs if o.parent_id and o.parent_id not in by_id]
    print("   -> 组织总数=%d  悬空父节点=%d %s" % (len(orgs), len(orphan), "❌" if orphan else "✅"))

    print("=" * 62)
    print("2) 旧部门 → 新组织 覆盖检查")
    legacy_depts = SysDept.query.all()
    miss = []
    for d in legacy_depts:
        if not AuthOrgUnit.query.filter_by(org_code='LEGACY_%s' % d.dept_code).first():
            miss.append(d.dept_name)
    print("   旧部门=%d  已迁移=%d  缺失=%s %s" % (
        len(legacy_depts), len(legacy_depts) - len(miss), miss or '无', "❌" if miss else "✅"))

    print("=" * 62)
    print("3) 角色 + 权限点")
    print("   auth_core_role=%d  旧sys_role=%d" % (AuthRole.query.count(), SysRole.query.count()))
    print("   auth_core_permission=%d  role_permission=%d" % (
        AuthPermission.query.count(), AuthRolePermission.query.count()))
    for r in AuthRole.query.order_by(AuthRole.id).all():
        sc = AuthDataScope.query.filter_by(role_id=r.id).first()
        pc = AuthRolePermission.query.filter_by(role_id=r.id).count()
        print("   #%-3s %-22s scope=%-14s perms=%d" % (
            r.id, r.role_code, sc.scope_type if sc else '(无)', pc))

    print("=" * 62)
    print("4) 用户逐个核对（旧 vs 新）")
    ok = True
    for lu in LegacyUser.query.order_by(LegacyUser.id).all():
        au = AuthUser.query.filter_by(username=lu.username).first()
        if not au:
            print("   ❌ %s 未迁移" % lu.username); ok = False; continue
        rcodes = []
        for ur in AuthUserRole.query.filter_by(user_id=au.id).all():
            r = AuthRole.query.get(ur.role_id)
            if r: rcodes.append(r.role_code)
        sc = AuthGateway.get_data_scope(au.id)
        perms = AuthGateway.get_permissions(au.id)
        org = AuthOrgUnit.query.get(au.org_id) if au.org_id else None
        pw_ok = (au.password_hash == lu.password_hash)
        print("   %-12s 旧角色=%-18s 新角色=%-28s org=%-14s dept_id=%s" % (
            lu.username, (lu.role_obj.role_code if lu.role_obj else '-'),
            ','.join(rcodes) or '(无)', (org.org_name if org else '(无)'), au.dept_id))
        print("      scope=%-12s projects=%-14s perms=%-5s 密码同步=%s is_admin=%s" % (
            sc.get('scope_type'), sc.get('project_ids'),
            ('ALL' if perms == '*' else len(perms)), '✅' if pw_ok else '❌',
            au.is_admin()))
        if not rcodes or not pw_ok: ok = False

    print("=" * 62)
    print("5) 关键断言")
    admin = AuthUser.query.filter_by(username='admin').first()
    print("   admin 存在=%s is_admin=%s has(material:view)=%s" % (
        bool(admin), admin.is_admin() if admin else '-',
        admin.has_permission('material:view') if admin else '-'))
    print("   总用户 legacy=%d new=%d %s" % (
        LegacyUser.query.count(), AuthUser.query.count(),
        "✅" if LegacyUser.query.count() == AuthUser.query.count() else "⚠️"))
    print("   整体：%s" % ("✅ 全部通过" if ok and not orphan and not miss else "⚠️ 见上方标记"))
