# app/auth_core/middleware.py
"""数据范围中间件：对所有业务表统一注入 org_id / project_id 过滤。

业务代码只需：
    query = AuthMiddleware.apply_data_scope(query, SomeModel, current_user.id)
即可获得该用户可见的数据集，无需自己写隔离逻辑。
"""
from app.auth_core.gateway import AuthGateway


class AuthMiddleware:
    @staticmethod
    def apply_data_scope(query, model, user_id):
        """按用户数据范围过滤查询。scope_type:
        all     -> 不过滤
        self    -> 仅本人创建（模型需有 created_by）
        project -> 仅可见项目（模型需有 project_id）
        legal_entity -> 仅可见法人下级组织（模型需有 org_id）
        custom  -> org_ids ∪ project_ids
        """
        scope = AuthGateway.get_data_scope(user_id)
        st = scope.get('scope_type')
        if st in (None, 'all'):
            return query

        if st == 'self':
            if hasattr(model, 'created_by'):
                return query.filter(model.created_by == user_id)
            return query.filter(False)  # 无创建人字段则视为不可见

        if st == 'project':
            pids = scope.get('project_ids') or []
            if hasattr(model, 'project_id'):
                return query.filter(model.project_id.in_(pids)) if pids else query.filter(False)
            return query  # 无项目字段则不隔离

        if st == 'legal_entity':
            oids = scope.get('org_ids') or []
            if hasattr(model, 'org_id'):
                return query.filter(model.org_id.in_(oids)) if oids else query.filter(False)
            return query

        # custom：组织集 ∪ 项目集
        oids = scope.get('org_ids') or []
        pids = scope.get('project_ids') or []
        conds = []
        if hasattr(model, 'org_id') and oids:
            conds.append(model.org_id.in_(oids))
        if hasattr(model, 'project_id') and pids:
            conds.append(model.project_id.in_(pids))
        if not conds:
            return query
        from sqlalchemy import or_  # noqa
        return query.filter(or_(*conds))
