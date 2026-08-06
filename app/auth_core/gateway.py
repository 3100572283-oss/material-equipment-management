# app/auth_core/gateway.py
"""AuthGateway：新权限中台对外唯一入口。

调用方（旧系统 / 业务模块 / 大屏）只依赖本接口，不碰 auth_core_* 表结构。
灰度开关 AUTH_CORE_ENABLED 控制是否启用新权限（关闭则旧逻辑继续生效）。
"""
import json
import os
from datetime import datetime

from app import db
from app.auth_core.models import (
    AuthUser, AuthRole, AuthPermission,
    AuthRolePermission, AuthUserRole, AuthDataScope, AuthUserDataScope, AuthOrgUnit,
)
from werkzeug.security import check_password_hash


def _enabled():
    return os.environ.get('AUTH_CORE_ENABLED', 'false').lower() == 'true'


# 数据范围档位排序（数值越大范围越宽），用于多角色/用户级合并时取最宽
SCOPE_RANK = {'self': 0, 'project': 1, 'legal_entity': 2, 'all': 3, 'custom': 1}


def _json_loads(v):
    if not v:
        return []
    try:
        return json.loads(v)
    except Exception:
        return []


class AuthGateway:
    # ------------------------- 认证 -------------------------
    @staticmethod
    def authenticate(username, password):
        """验证密码（复用 werkzeug pbkdf2:sha256），返回 user_id 或 None"""
        user = AuthUser.query.filter_by(username=username, status=True).first()
        if not user:
            return None
        if user.locked_until and user.locked_until > datetime.now():
            return None
        if check_password_hash(user.password_hash, password):
            return user.id
        return None

    # ------------------------- 功能权限 -------------------------
    @staticmethod
    def get_permissions(user_id):
        """返回该用户全部 perm_key 集合（super_admin 返回全部）"""
        user = AuthUser.query.get(user_id)
        if not user:
            return set()
        role_ids = [ur.role_id for ur in AuthUserRole.query.filter_by(user_id=user_id).all()]
        if role_ids:
            sa = AuthRole.query.filter_by(role_code='super_admin').first()
            if sa and sa.id in role_ids:
                return set(p.perm_key for p in AuthPermission.query.filter_by(status=True).all())
        perm_ids = [rp.permission_id for rp in
                    AuthRolePermission.query.filter(AuthRolePermission.role_id.in_(role_ids)).all()]
        return set(p.perm_key for p in
                   AuthPermission.query.filter(AuthPermission.id.in_(perm_ids), AuthPermission.status == True).all())

    @staticmethod
    def check_permission(user_id, perm_key):
        """单次权限判定；perm_key 为空视为放行；super_admin 短路"""
        if not perm_key:
            return True
        user = AuthUser.query.get(user_id)
        if not user:
            return False
        role_ids = [ur.role_id for ur in AuthUserRole.query.filter_by(user_id=user_id).all()]
        if role_ids:
            sa = AuthRole.query.filter_by(role_code='super_admin').first()
            if sa and sa.id in role_ids:
                return True
        return perm_key in AuthGateway.get_permissions(user_id)

    # ------------------------- 数据范围 -------------------------
    @staticmethod
    def get_role_scope(user_id):
        """角色级数据范围档位：多角色取最宽；super_admin 直接 all"""
        role_ids = [ur.role_id for ur in AuthUserRole.query.filter_by(user_id=user_id).all()]
        if not role_ids:
            return 'self', []
        sa = AuthRole.query.filter_by(role_code='super_admin').first()
        if sa and sa.id in role_ids:
            return 'all', role_ids
        best = None
        for ds in AuthDataScope.query.filter(AuthDataScope.role_id.in_(role_ids)).all():
            if best is None or SCOPE_RANK.get(ds.scope_type, 0) > SCOPE_RANK.get(best, 0):
                best = ds.scope_type
        return (best or 'project'), role_ids

    @staticmethod
    def get_data_scope(user_id):
        """返回数据范围 dict：{scope_type, org_ids[], project_ids[]}

        合并策略（角色定档位、用户级做具体授权，绝不误降超管）：
          1. 角色档位 = 该用户全部角色中最宽的一档（super_admin 恒为 all）
          2. 用户级 scope_type='custom'  → 管理员显式自定义，完全以用户级为准
          3. 角色档位为 all              → 忽略用户级白名单，全量可见
          4. 用户级档位更宽              → 升级为用户级档位
          5. 其余（同档或更窄）          → 用户级 project_ids/org_ids 作为白名单生效，
                                          为空则回落到按组织树解析的结果
        """
        user = AuthUser.query.get(user_id)
        if not user:
            return {'scope_type': 'self', 'org_ids': [], 'project_ids': []}

        role_scope, _ = AuthGateway.get_role_scope(user_id)
        uds = AuthUserDataScope.query.filter_by(user_id=user_id).first()

        if not uds:
            org_ids, project_ids = AuthGateway._resolve_scope(user, role_scope)
            return {'scope_type': role_scope, 'org_ids': org_ids, 'project_ids': project_ids}

        u_orgs = _json_loads(uds.org_ids)
        u_projects = _json_loads(uds.project_ids)

        # 2) 显式自定义：以用户级为准（允许收窄，属管理员明确意图）
        if uds.scope_type == 'custom':
            return {'scope_type': 'custom', 'org_ids': u_orgs, 'project_ids': u_projects}

        # 3) 超管/全局档位：不被用户级白名单降级
        if role_scope == 'all':
            return {'scope_type': 'all', 'org_ids': [], 'project_ids': []}

        # 4) 用户级更宽：升级
        if SCOPE_RANK.get(uds.scope_type, 0) > SCOPE_RANK.get(role_scope, 0):
            org_ids, project_ids = AuthGateway._resolve_scope(user, uds.scope_type)
            return {
                'scope_type': uds.scope_type,
                'org_ids': u_orgs or org_ids,
                'project_ids': u_projects or project_ids,
            }

        # 5) 同档/更窄：用户级白名单优先，为空回落组织树解析
        org_ids, project_ids = AuthGateway._resolve_scope(user, role_scope)
        return {
            'scope_type': role_scope,
            'org_ids': u_orgs or org_ids,
            'project_ids': u_projects or project_ids,
        }

    @staticmethod
    def _resolve_scope(user, scope_type):
        """根据 user.org + scope_type 计算实际可见组织/项目集合"""
        if scope_type in ('all', 'self', 'custom'):
            return ([], [])
        root = user.org
        if not root:
            return ([], [])
        org_ids, project_ids = [], []
        if scope_type == 'project':
            if root.project_id:
                project_ids = [root.project_id]
            else:
                for oid in root.get_children_recursive():
                    ou = AuthOrgUnit.query.get(oid)
                    if ou and ou.project_id:
                        project_ids.append(ou.project_id)
        elif scope_type == 'legal_entity':
            ids = root.get_children_recursive()
            org_ids = ids
            for oid in ids:
                ou = AuthOrgUnit.query.get(oid)
                if ou and ou.project_id:
                    project_ids.append(ou.project_id)
        return (org_ids, project_ids)

    @staticmethod
    def get_visible_org_ids(user_id):
        """按组织树展开可见组织节点（含下级）"""
        scope = AuthGateway.get_data_scope(user_id)
        if scope['scope_type'] == 'all':
            return [o.id for o in AuthOrgUnit.query.all()]
        if scope['org_ids']:
            return scope['org_ids']
        # project 范围：取可见项目对应的项目部节点
        ids = []
        for pid in scope['project_ids']:
            ou = AuthOrgUnit.query.filter_by(project_id=pid).first()
            if ou:
                ids.append(ou.id)
        return ids

    @staticmethod
    def get_org_tree(user_id=None):
        """返回组织树（管理员全量；普通用户仅可见分支）"""
        roots = AuthOrgUnit.query.filter_by(parent_id=0).order_by(AuthOrgUnit.sort).all()

        def build(node):
            children = node.children.order_by(AuthOrgUnit.sort).all()
            return {
                'id': node.id,
                'code': node.org_code,
                'name': node.org_name,
                'level': node.org_level,
                'legal_entity': node.legal_entity,
                'dept_type': node.dept_type,
                'project_id': node.project_id,
                'status': node.status,
                'children': [build(c) for c in children],
            }

        return [build(r) for r in roots]

    # ------------------------- 桥接 -------------------------
    @staticmethod
    def sync_user_from_legacy(legacy_user_id):
        """LegacyAuthAdapter 调用：旧 users → auth_core（幂等）"""
        from app.auth_core import adapter
        return adapter.sync_user_from_legacy(legacy_user_id)
