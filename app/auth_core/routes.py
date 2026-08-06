# app/auth_core/routes.py
"""auth_core 管理后台三页（组织 / 角色 / 用户）JSON API 骨架。

完整前端页面后续接入；此处提供可驱动前端的数据接口。
鉴权后续接 AuthGateway（灰度开启后由新权限控制），当前仅做最小骨架。
"""
from flask import request, jsonify
from flask_login import login_required

from app.auth_core import bp
from app import db
from app.auth_core.models import (
    AuthOrgUnit, AuthUser, AuthRole, AuthPermission,
    AuthRolePermission, AuthUserRole, AuthDataScope, AuthUserDataScope,
)
from app.auth_core.gateway import AuthGateway


# ------------------------- 组织管理 -------------------------
@bp.route('/org/list')
@login_required
def org_list():
    tree = AuthGateway.get_org_tree()
    return jsonify({'code': 0, 'data': tree})


@bp.route('/org', methods=['POST'])
@login_required
def org_create():
    data = request.get_json(force=True, silent=True) or {}
    ou = AuthOrgUnit(
        org_code=data.get('org_code'),
        org_name=data.get('org_name'),
        parent_id=data.get('parent_id', 0),
        org_level=data.get('org_level', 4),
        legal_entity=bool(data.get('legal_entity', False)),
        dept_type=data.get('dept_type', 'dept'),
        project_id=data.get('project_id'),
        leader=data.get('leader'),
        sort=data.get('sort', 0),
        status=data.get('status', True),
    )
    db.session.add(ou)
    db.session.commit()
    return jsonify({'code': 0, 'msg': 'ok', 'id': ou.id})


# ------------------------- 角色管理 -------------------------
@bp.route('/role/list')
@login_required
def role_list():
    roles = AuthRole.query.order_by(AuthRole.sort).all()
    data = [{
        'id': r.id, 'role_code': r.role_code, 'role_name': r.role_name,
        'is_system': r.is_system, 'status': r.status,
        'scope': (AuthDataScope.query.filter_by(role_id=r.id).first().scope_type if
                  AuthDataScope.query.filter_by(role_id=r.id).first() else 'project'),
    } for r in roles]
    return jsonify({'code': 0, 'data': data})


@bp.route('/role/<int:role_id>/permissions')
@login_required
def role_permissions(role_id):
    granted = set(rp.permission_id for rp in AuthRolePermission.query.filter_by(role_id=role_id).all())
    perms = AuthPermission.query.filter_by(status=True).all()
    data = [{
        'id': p.id, 'perm_key': p.perm_key, 'module': p.module,
        'resource': p.resource, 'action': p.action, 'granted': p.id in granted,
    } for p in perms]
    return jsonify({'code': 0, 'data': data})


# ------------------------- 用户管理 -------------------------
@bp.route('/user/list')
@login_required
def user_list():
    users = AuthUser.query.all()
    data = [{
        'id': u.id, 'username': u.username, 'name': u.name,
        'org_id': u.org_id, 'post': u.post, 'status': u.status,
        'source': u.source,
    } for u in users]
    return jsonify({'code': 0, 'data': data})


@bp.route('/user', methods=['POST'])
@login_required
def user_create():
    """新增用户 = 选组织 + 选岗位(自动带角色) + 基本信息。运维极简。"""
    data = request.get_json(force=True, silent=True) or {}
    from werkzeug.security import generate_password_hash
    from flask import current_app

    pwd = data.get('password') or current_app.config.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')
    user = AuthUser(
        username=data.get('username'),
        password_hash=generate_password_hash(pwd, method='pbkdf2:sha256'),
        name=data.get('name'),
        email=data.get('email'),
        phone=data.get('phone'),
        org_id=data.get('org_id'),
        post=data.get('post'),
        must_change_password=True,
    )
    db.session.add(user)
    db.session.flush()
    # 选岗位 → 自动带对应角色模板
    post_code = data.get('post')
    if post_code:
        role = AuthRole.query.filter_by(role_code=post_code).first()
        if role and not AuthUserRole.query.filter_by(user_id=user.id, role_id=role.id).first():
            db.session.add(AuthUserRole(user_id=user.id, role_id=role.id))
    db.session.commit()
    return jsonify({'code': 0, 'msg': 'ok', 'id': user.id})
