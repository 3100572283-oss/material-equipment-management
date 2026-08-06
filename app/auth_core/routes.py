# app/auth_core/routes.py
"""auth_core 管理后台：组织 / 角色 / 用户 三页（页面渲染 + JSON API）。

设计约束（绞杀者模式防污染）：
  1. 本文件不 import 任何旧权限模型（app.models 的 User/SysRole/SysMenu 等）。
     与旧登录态的唯一交互是读取 flask_login.current_user.username（通用接口，非旧模型契约）。
  2. 所有权限判定统一经 AuthGateway，不在此处硬编码 is_admin 分支。
  3. 灰度期（AUTH_CORE_ENABLED=false）：若当前登录用户尚未存在于 auth_core，
     回退到旧登录态的管理员标记放行，保证并存期可访问，且不破坏旧系统。
"""
import json
from functools import wraps
from datetime import datetime

from flask import request, jsonify, render_template, current_app
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from app.auth_core import bp
from app import db
from app.auth_core.models import (
    AuthOrgUnit, AuthUser, AuthRole, AuthPermission,
    AuthRolePermission, AuthUserRole, AuthDataScope, AuthUserDataScope,
)
from app.auth_core.gateway import AuthGateway
from app.auth_core.init_data import POST_OPTIONS, SCOPE_OPTIONS

# 组织层级中文名（对齐铁建三级四层）
ORG_LEVEL_NAMES = {1: '股份公司', 2: '二级集团/工程局', 3: '三级公司', 4: '项目部'}
DEPT_TYPE_NAMES = {
    'company': '公司', 'branch': '分支机构', 'project': '项目部',
    'dept': '部门', 'team': '作业队',
}


# ============================== 通用工具 ==============================
def ok(data=None, msg='ok', **extra):
    body = {'code': 0, 'msg': msg}
    if data is not None:
        body['data'] = data
    body.update(extra)
    return jsonify(body)


def fail(msg, code=1, status=200):
    return jsonify({'code': code, 'msg': msg}), status


def _json_loads(v):
    if not v:
        return []
    try:
        return json.loads(v)
    except Exception:
        return []


def current_auth_user():
    """取当前登录者在 auth_core 中的用户对象；不存在返回 None（灰度期正常现象）。"""
    username = getattr(current_user, 'username', None)
    if not username:
        return None
    return AuthUser.query.filter_by(username=username).first()


def _legacy_is_admin():
    """灰度回退：读旧登录态的管理员标记。用 getattr 安全探测，不 import 旧模型。"""
    for attr in ('is_admin',):
        fn = getattr(current_user, attr, None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                return False
        if isinstance(fn, bool):
            return fn
    return False


def require_perm(perm_key):
    """权限守卫：优先 AuthGateway 判定；用户未入 auth_core 时回退旧管理员标记。"""
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            au = current_auth_user()
            if au is not None:
                if not AuthGateway.check_permission(au.id, perm_key):
                    if request.path.endswith(('/orgs', '/roles', '/users')):
                        return render_template('auth_core/403.html', perm=perm_key), 403
                    return fail('无权限：%s' % perm_key, code=403, status=403)
            elif not _legacy_is_admin():
                if request.path.endswith(('/orgs', '/roles', '/users')):
                    return render_template('auth_core/403.html', perm=perm_key), 403
                return fail('无权限：%s' % perm_key, code=403, status=403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def current_perms():
    """当前用户权限集合，供模板做按钮级显隐。"""
    au = current_auth_user()
    if au is not None:
        return AuthGateway.get_permissions(au.id)
    if _legacy_is_admin():
        return set(p.perm_key for p in AuthPermission.query.filter_by(status=True).all())
    return set()


@bp.app_context_processor
def inject_auth_core_entry():
    """向全局模板注入权限中台入口可见性。

    判定逻辑留在 auth_core 内部，base.html 只消费一个布尔值 —— 入口是「接入」而非耦合。
    结果按请求缓存，避免每次渲染都查库。
    """
    from flask import g
    cached = getattr(g, '_ac_entry_visible', None)
    if cached is not None:
        return {'ac_entry_visible': cached}
    visible = False
    try:
        if getattr(current_user, 'is_authenticated', False):
            au = current_auth_user()
            if au is not None:
                visible = (AuthGateway.check_permission(au.id, 'system:org:view')
                           or AuthGateway.check_permission(au.id, 'system:user:view'))
            else:
                visible = _legacy_is_admin()
    except Exception:
        visible = False
    g._ac_entry_visible = visible
    return {'ac_entry_visible': visible}


def org_flat_list():
    """组织扁平列表（带缩进名），供下拉选择使用。"""
    result = []

    def walk(node, depth):
        result.append({
            'id': node.id,
            'name': node.org_name,
            'code': node.org_code,
            'level': node.org_level,
            'legal_entity': node.legal_entity,
            'indent_name': ('　' * depth) + ('└ ' if depth else '') + node.org_name,
        })
        for c in node.children.order_by(AuthOrgUnit.sort).all():
            walk(c, depth + 1)

    for root in AuthOrgUnit.query.filter_by(parent_id=0).order_by(AuthOrgUnit.sort).all():
        walk(root, 0)
    return result


# ============================== 页面渲染 ==============================
@bp.route('/')
@bp.route('/orgs')
@require_perm('system:org:view')
def page_orgs():
    return render_template(
        'auth_core/orgs.html',
        active_nav='orgs',
        perms=current_perms(),
        level_names=ORG_LEVEL_NAMES,
        dept_types=DEPT_TYPE_NAMES,
    )


@bp.route('/roles')
@require_perm('system:role:view')
def page_roles():
    return render_template(
        'auth_core/roles.html',
        active_nav='roles',
        perms=current_perms(),
        scope_options=SCOPE_OPTIONS,
    )


@bp.route('/users')
@require_perm('system:user:view')
def page_users():
    return render_template(
        'auth_core/users.html',
        active_nav='users',
        perms=current_perms(),
        post_options=POST_OPTIONS,
    )


# ============================== 元数据 ==============================
@bp.route('/api/meta')
@login_required
def api_meta():
    """一次性下发下拉源，减少前端请求。"""
    return ok({
        'orgs': org_flat_list(),
        'posts': POST_OPTIONS,
        'scopes': SCOPE_OPTIONS,
        'levels': [{'code': k, 'name': v} for k, v in ORG_LEVEL_NAMES.items()],
        'dept_types': [{'code': k, 'name': v} for k, v in DEPT_TYPE_NAMES.items()],
        'roles': [{'id': r.id, 'code': r.role_code, 'name': r.role_name}
                  for r in AuthRole.query.filter_by(status=True).order_by(AuthRole.sort).all()],
    })


# ============================== 组织管理 ==============================
@bp.route('/api/org/tree')
@require_perm('system:org:view')
def api_org_tree():
    return ok(AuthGateway.get_org_tree())


@bp.route('/api/org', methods=['POST'])
@require_perm('system:org:create')
def api_org_create():
    d = request.get_json(force=True, silent=True) or {}
    code = (d.get('org_code') or '').strip()
    name = (d.get('org_name') or '').strip()
    if not code or not name:
        return fail('组织编码与名称必填')
    if AuthOrgUnit.query.filter_by(org_code=code).first():
        return fail('组织编码已存在：%s' % code)

    parent_id = int(d.get('parent_id') or 0)
    # 层级自动推导：父级 +1（未显式指定时），保证三级四层结构不被手工填错
    level = d.get('org_level')
    if not level:
        parent = AuthOrgUnit.query.get(parent_id) if parent_id else None
        level = min((parent.org_level + 1), 4) if parent else 1

    ou = AuthOrgUnit(
        org_code=code, org_name=name, parent_id=parent_id,
        org_level=int(level),
        legal_entity=bool(d.get('legal_entity', False)),
        dept_type=d.get('dept_type', 'dept'),
        project_id=d.get('project_id') or None,
        leader=d.get('leader'), sort=int(d.get('sort') or 0),
        status=bool(d.get('status', True)), remark=d.get('remark'),
    )
    db.session.add(ou)
    db.session.commit()
    return ok({'id': ou.id}, '组织已创建')


@bp.route('/api/org/<int:org_id>', methods=['PUT'])
@require_perm('system:org:edit')
def api_org_update(org_id):
    ou = AuthOrgUnit.query.get(org_id)
    if not ou:
        return fail('组织不存在')
    d = request.get_json(force=True, silent=True) or {}

    new_code = (d.get('org_code') or ou.org_code).strip()
    if new_code != ou.org_code and AuthOrgUnit.query.filter_by(org_code=new_code).first():
        return fail('组织编码已存在：%s' % new_code)

    # 防环：父节点不可指向自己或自己的后代
    new_parent = int(d.get('parent_id', ou.parent_id) or 0)
    if new_parent and new_parent in ou.get_children_recursive():
        return fail('不能将上级设为自身或其下级组织')

    ou.org_code = new_code
    ou.org_name = (d.get('org_name') or ou.org_name).strip()
    ou.parent_id = new_parent
    ou.org_level = int(d.get('org_level') or ou.org_level)
    ou.legal_entity = bool(d.get('legal_entity', ou.legal_entity))
    ou.dept_type = d.get('dept_type', ou.dept_type)
    ou.project_id = d.get('project_id') if 'project_id' in d else ou.project_id
    ou.leader = d.get('leader', ou.leader)
    ou.sort = int(d.get('sort', ou.sort) or 0)
    ou.status = bool(d.get('status', ou.status))
    ou.remark = d.get('remark', ou.remark)
    db.session.commit()
    return ok(msg='组织已更新')


@bp.route('/api/org/<int:org_id>', methods=['DELETE'])
@require_perm('system:org:delete')
def api_org_delete(org_id):
    ou = AuthOrgUnit.query.get(org_id)
    if not ou:
        return fail('组织不存在')
    if ou.children.count() > 0:
        return fail('该组织下存在子组织，请先删除子组织')
    if AuthUser.query.filter_by(org_id=org_id).count() > 0:
        return fail('该组织下存在用户，请先调整用户归属')
    db.session.delete(ou)
    db.session.commit()
    return ok(msg='组织已删除')


# ============================== 角色管理 ==============================
def _role_scope_type(role_id):
    ds = AuthDataScope.query.filter_by(role_id=role_id).first()
    return ds.scope_type if ds else 'project'


@bp.route('/api/role/list')
@require_perm('system:role:view')
def api_role_list():
    roles = AuthRole.query.order_by(AuthRole.sort, AuthRole.id).all()
    scope_map = {s['code']: s['name'] for s in SCOPE_OPTIONS}
    data = []
    for r in roles:
        st = _role_scope_type(r.id)
        data.append({
            'id': r.id, 'role_code': r.role_code, 'role_name': r.role_name,
            'is_system': r.is_system, 'status': r.status, 'sort': r.sort,
            'remark': r.remark or '',
            'scope_type': st, 'scope_name': scope_map.get(st, st),
            'perm_count': AuthRolePermission.query.filter_by(role_id=r.id).count(),
            'user_count': AuthUserRole.query.filter_by(role_id=r.id).count(),
        })
    return ok(data)


@bp.route('/api/role', methods=['POST'])
@require_perm('system:role:create')
def api_role_create():
    d = request.get_json(force=True, silent=True) or {}
    code = (d.get('role_code') or '').strip()
    name = (d.get('role_name') or '').strip()
    if not code or not name:
        return fail('角色编码与名称必填')
    if AuthRole.query.filter_by(role_code=code).first():
        return fail('角色编码已存在：%s' % code)
    role = AuthRole(role_code=code, role_name=name, is_system=False,
                    sort=int(d.get('sort') or 99), remark=d.get('remark'),
                    status=bool(d.get('status', True)))
    db.session.add(role)
    db.session.flush()
    db.session.add(AuthDataScope(role_id=role.id, scope_type=d.get('scope_type', 'project')))
    db.session.commit()
    return ok({'id': role.id}, '角色已创建')


@bp.route('/api/role/<int:role_id>', methods=['PUT'])
@require_perm('system:role:edit')
def api_role_update(role_id):
    role = AuthRole.query.get(role_id)
    if not role:
        return fail('角色不存在')
    d = request.get_json(force=True, silent=True) or {}
    if 'role_name' in d:
        role.role_name = (d['role_name'] or role.role_name).strip()
    if 'sort' in d:
        role.sort = int(d['sort'] or 0)
    if 'remark' in d:
        role.remark = d['remark']
    if 'status' in d:
        role.status = bool(d['status'])
    if 'scope_type' in d:
        ds = AuthDataScope.query.filter_by(role_id=role.id).first()
        if not ds:
            ds = AuthDataScope(role_id=role.id)
            db.session.add(ds)
        ds.scope_type = d['scope_type']
        ds.org_ids = json.dumps(d.get('org_ids') or []) if d['scope_type'] == 'custom' else None
    db.session.commit()
    return ok(msg='角色已更新')


@bp.route('/api/role/<int:role_id>', methods=['DELETE'])
@require_perm('system:role:delete')
def api_role_delete(role_id):
    role = AuthRole.query.get(role_id)
    if not role:
        return fail('角色不存在')
    if role.is_system:
        return fail('系统内置角色不可删除，如需停用请设为禁用')
    if AuthUserRole.query.filter_by(role_id=role_id).count() > 0:
        return fail('该角色下仍有用户，请先解除关联')
    AuthRolePermission.query.filter_by(role_id=role_id).delete()
    AuthDataScope.query.filter_by(role_id=role_id).delete()
    db.session.delete(role)
    db.session.commit()
    return ok(msg='角色已删除')


@bp.route('/api/role/<int:role_id>/permissions')
@require_perm('system:role:view')
def api_role_permissions(role_id):
    """按模块分组返回权限点及授予状态，供权限矩阵渲染。"""
    granted = set(rp.permission_id for rp in AuthRolePermission.query.filter_by(role_id=role_id).all())
    perms = AuthPermission.query.filter_by(status=True).order_by(
        AuthPermission.module, AuthPermission.resource, AuthPermission.id).all()

    groups = {}
    for p in perms:
        g = groups.setdefault(p.module, {})
        g.setdefault(p.resource, []).append({
            'id': p.id, 'action': p.action, 'perm_key': p.perm_key,
            'granted': p.id in granted,
        })
    data = [{
        'module': m,
        'resources': [{'resource': r, 'actions': acts} for r, acts in res.items()],
    } for m, res in groups.items()]

    role = AuthRole.query.get(role_id)
    return ok(data, role_code=(role.role_code if role else ''),
              is_super=(role.role_code == 'super_admin' if role else False))


@bp.route('/api/role/<int:role_id>/permissions', methods=['POST'])
@require_perm('system:role:grant')
def api_role_grant(role_id):
    """全量覆盖式授权：提交勾选的 permission_id 列表。"""
    role = AuthRole.query.get(role_id)
    if not role:
        return fail('角色不存在')
    d = request.get_json(force=True, silent=True) or {}
    ids = set(int(i) for i in (d.get('permission_ids') or []))

    existing = {rp.permission_id: rp for rp in AuthRolePermission.query.filter_by(role_id=role_id).all()}
    for pid in ids - set(existing.keys()):
        db.session.add(AuthRolePermission(role_id=role_id, permission_id=pid))
    for pid in set(existing.keys()) - ids:
        db.session.delete(existing[pid])
    db.session.commit()
    return ok(msg='已保存 %d 项权限' % len(ids))


# ============================== 用户管理 ==============================
@bp.route('/api/user/list')
@require_perm('system:user:view')
def api_user_list():
    kw = (request.args.get('kw') or '').strip()
    org_id = request.args.get('org_id', type=int)
    page = request.args.get('page', 1, type=int)
    size = min(request.args.get('size', 20, type=int), 100)

    q = AuthUser.query
    if kw:
        like = '%%%s%%' % kw
        q = q.filter(db.or_(AuthUser.username.like(like),
                            AuthUser.name.like(like),
                            AuthUser.phone.like(like)))
    if org_id:
        ou = AuthOrgUnit.query.get(org_id)
        if ou:
            q = q.filter(AuthUser.org_id.in_(ou.get_children_recursive()))

    total = q.count()
    users = q.order_by(AuthUser.id).offset((page - 1) * size).limit(size).all()

    role_name_map = {r.id: r.role_name for r in AuthRole.query.all()}
    post_name_map = {p['code']: p['name'] for p in POST_OPTIONS}
    post_name_map['super_admin'] = '超级管理员'

    data = []
    for u in users:
        rids = [ur.role_id for ur in AuthUserRole.query.filter_by(user_id=u.id).all()]
        data.append({
            'id': u.id, 'username': u.username, 'name': u.name or '',
            'phone': u.phone or '', 'email': u.email or '',
            'org_id': u.org_id, 'org_name': (u.org.org_name if u.org else '未分配'),
            'post': u.post or '', 'post_name': post_name_map.get(u.post, u.post or '—'),
            'status': u.status, 'source': u.source,
            'must_change_password': u.must_change_password,
            'role_ids': rids,
            'role_names': [role_name_map.get(i, '') for i in rids],
            'last_login_at': u.last_login_at.strftime('%Y-%m-%d %H:%M') if u.last_login_at else '',
        })
    return ok(data, total=total, page=page, size=size)


@bp.route('/api/user', methods=['POST'])
@require_perm('system:user:create')
def api_user_create():
    """新增用户 = 选组织 + 选岗位（自动带角色）+ 基本信息。运维极简。"""
    d = request.get_json(force=True, silent=True) or {}
    username = (d.get('username') or '').strip()
    if not username:
        return fail('登录名必填')
    if AuthUser.query.filter_by(username=username).first():
        return fail('登录名已存在：%s' % username)

    pwd = d.get('password') or current_app.config.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')
    user = AuthUser(
        username=username,
        password_hash=generate_password_hash(pwd, method='pbkdf2:sha256'),
        name=(d.get('name') or '').strip() or None,
        email=d.get('email'), phone=d.get('phone'),
        org_id=d.get('org_id') or None,
        post=d.get('post'),
        status=bool(d.get('status', True)),
        must_change_password=True,
        source='self',
    )
    db.session.add(user)
    db.session.flush()

    # 选岗位 → 自动挂载同名角色模板；也允许显式传 role_ids 覆盖
    role_ids = d.get('role_ids')
    if not role_ids and d.get('post'):
        role = AuthRole.query.filter_by(role_code=d['post']).first()
        role_ids = [role.id] if role else []
    for rid in set(int(i) for i in (role_ids or [])):
        db.session.add(AuthUserRole(user_id=user.id, role_id=rid))

    db.session.commit()
    return ok({'id': user.id}, '用户已创建，初始密码需首次登录后修改')


@bp.route('/api/user/<int:user_id>', methods=['PUT'])
@require_perm('system:user:edit')
def api_user_update(user_id):
    u = AuthUser.query.get(user_id)
    if not u:
        return fail('用户不存在')
    d = request.get_json(force=True, silent=True) or {}

    for f in ('name', 'email', 'phone'):
        if f in d:
            setattr(u, f, d[f])
    if 'org_id' in d:
        u.org_id = d['org_id'] or None
    if 'status' in d:
        u.status = bool(d['status'])
    if 'post' in d:
        u.post = d['post']

    # 角色重置（显式传入则全量覆盖；未传则按岗位同步）
    if 'role_ids' in d:
        new_ids = set(int(i) for i in (d.get('role_ids') or []))
        existing = {ur.role_id: ur for ur in AuthUserRole.query.filter_by(user_id=user_id).all()}
        for rid in new_ids - set(existing.keys()):
            db.session.add(AuthUserRole(user_id=user_id, role_id=rid))
        for rid in set(existing.keys()) - new_ids:
            db.session.delete(existing[rid])

    # 用户级数据范围覆盖（可选，优先于角色级）
    if 'scope_type' in d:
        uds = AuthUserDataScope.query.filter_by(user_id=user_id).first()
        if d['scope_type']:
            if not uds:
                uds = AuthUserDataScope(user_id=user_id)
                db.session.add(uds)
            uds.scope_type = d['scope_type']
            uds.org_ids = json.dumps(d.get('scope_org_ids') or [])
            uds.project_ids = json.dumps(d.get('scope_project_ids') or [])
        elif uds:
            db.session.delete(uds)

    db.session.commit()
    return ok(msg='用户已更新')


@bp.route('/api/user/<int:user_id>', methods=['DELETE'])
@require_perm('system:user:delete')
def api_user_delete(user_id):
    u = AuthUser.query.get(user_id)
    if not u:
        return fail('用户不存在')
    if u.username == 'admin':
        return fail('超级管理员账号不可删除')
    me = current_auth_user()
    if me and me.id == user_id:
        return fail('不能删除当前登录账号')
    AuthUserRole.query.filter_by(user_id=user_id).delete()
    AuthUserDataScope.query.filter_by(user_id=user_id).delete()
    db.session.delete(u)
    db.session.commit()
    return ok(msg='用户已删除')


@bp.route('/api/user/<int:user_id>/reset_password', methods=['POST'])
@require_perm('system:user:reset_pwd')
def api_user_reset_password(user_id):
    u = AuthUser.query.get(user_id)
    if not u:
        return fail('用户不存在')
    d = request.get_json(force=True, silent=True) or {}
    pwd = d.get('password') or current_app.config.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')
    u.password_hash = generate_password_hash(pwd, method='pbkdf2:sha256')
    u.must_change_password = True
    u.failed_login_count = 0
    u.locked_until = None
    db.session.commit()
    return ok(msg='密码已重置，用户下次登录须修改')


@bp.route('/api/user/<int:user_id>/toggle_status', methods=['POST'])
@require_perm('system:user:edit')
def api_user_toggle_status(user_id):
    u = AuthUser.query.get(user_id)
    if not u:
        return fail('用户不存在')
    if u.username == 'admin':
        return fail('超级管理员账号不可停用')
    u.status = not u.status
    db.session.commit()
    return ok({'status': u.status}, '已%s' % ('启用' if u.status else '停用'))


@bp.route('/api/user/<int:user_id>/effective')
@require_perm('system:user:view')
def api_user_effective(user_id):
    """有效权限预览：把「这个人到底能看到什么」摊开给管理员核对。"""
    u = AuthUser.query.get(user_id)
    if not u:
        return fail('用户不存在')
    scope = AuthGateway.get_data_scope(u.id)
    scope_map = {s['code']: s['name'] for s in SCOPE_OPTIONS}
    visible_ids = AuthGateway.get_visible_org_ids(u.id)
    org_names = [o.org_name for o in AuthOrgUnit.query.filter(AuthOrgUnit.id.in_(visible_ids)).all()] \
        if visible_ids else []
    return ok({
        'username': u.username,
        'name': u.name or '',
        'org_name': u.org.org_name if u.org else '未分配',
        'roles': [r.role_name for r in AuthRole.query.filter(
            AuthRole.id.in_([ur.role_id for ur in AuthUserRole.query.filter_by(user_id=u.id).all()] or [0])).all()],
        'scope_type': scope['scope_type'],
        'scope_name': scope_map.get(scope['scope_type'], scope['scope_type']),
        'visible_orgs': org_names,
        'permissions': sorted(AuthGateway.get_permissions(u.id)),
    })
