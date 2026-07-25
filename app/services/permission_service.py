from flask_login import current_user


class PermissionService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_user_data_scope(self, user):
        """获取用户数据权限范围

        Args:
            user: User 对象

        Returns:
            dict: {
                'scope': 'all' | 'dept_and_sub' | 'dept' | 'self' | 'custom',
                'custom_depts': []  # data_scope=custom 时的部门ID列表
            }
        """
        if user.is_admin():
            return {'scope': 'all', 'custom_depts': []}

        role = user.role_obj
        if not role:
            return {'scope': user.data_scope or 'all', 'custom_depts': []}

        from app.models import SysRoleDataScope
        scope_cfg = SysRoleDataScope.query.filter_by(role_id=role.id).first()
        if scope_cfg:
            data_scope = scope_cfg.data_scope or 'all'
            custom_depts = []
            if scope_cfg.custom_depts:
                import json as _json
                try:
                    raw = _json.loads(scope_cfg.custom_depts)
                    custom_depts = [int(x) for x in raw if str(x).isdigit()]
                except Exception:
                    custom_depts = [int(x.strip()) for x in scope_cfg.custom_depts.split(',') if x.strip().isdigit()]
            return {'scope': data_scope, 'custom_depts': custom_depts}

        return {'scope': role.data_scope or 'all', 'custom_depts': []}

    def get_user_allowed_projects(self, user):
        """获取用户可访问的项目ID列表

        返回 None 表示拥有全部数据权限，可访问所有项目；
        返回列表表示仅可访问这些项目ID。

        规则：
        - all: 所有项目
        - dept_and_sub: 本部门及下级部门归属的项目
        - dept: 仅本部门归属的项目
        - self: 仅自己被分配的项目（sys_user_project）
        - custom: 自定义部门列表归属的项目

        用户手动绑定的项目始终保留。
        """
        if user.is_admin():
            return None

        scope_info = self.get_user_data_scope(user)
        data_scope = scope_info['scope']
        custom_depts = scope_info['custom_depts']

        if data_scope == 'all':
            return None

        from app.models import Project

        project_ids = set()

        if data_scope == 'dept_and_sub':
            if user.dept_id:
                dept = user.dept
                if dept:
                    dept_ids = [user.dept_id]
                    try:
                        dept_ids.extend(dept.get_children_recursive())
                    except Exception:
                        pass
                    projects = Project.query.filter(
                        Project.dept_id.in_(dept_ids),
                        Project.is_archived == False
                    ).all()
                    for p in projects:
                        project_ids.add(p.id)

        elif data_scope == 'dept':
            if user.dept_id:
                projects = Project.query.filter_by(
                    dept_id=user.dept_id, is_archived=False
                ).all()
                for p in projects:
                    project_ids.add(p.id)

        elif data_scope == 'custom':
            if custom_depts:
                projects = Project.query.filter(
                    Project.dept_id.in_(custom_depts),
                    Project.is_archived == False
                ).all()
                for p in projects:
                    project_ids.add(p.id)

        for up in user.user_projects:
            project_ids.add(up.project_id)

        return list(project_ids) if project_ids else []

    def check_permission(self, user, permission):
        """检查用户是否拥有指定按钮权限

        Args:
            user: User 对象
            permission: 权限标识,支持多种格式:
                - 'system:dept:list' (三段落: 模块:功能:操作)
                - 'stock_in:create' (两段落: 权限代码:操作)
                - '26:view' (菜单ID:操作)
                - '26' (仅菜单ID, 默认检查view权限)

        Returns:
            bool: 是否有权限
        """
        if not permission:
            return True
        if user.is_admin():
            return True
        if not user.role_id:
            if user.role in ('admin', 'editor'):
                return True
            return False

        from app.models import SysMenu, SysRoleMenu

        if ':' in permission:
            parts = permission.split(':')

            if len(parts) == 3:
                module, func, operation = parts
                menu = SysMenu.query.filter_by(permission=permission).first()
                if menu:
                    check_menu_id = menu.parent_id if menu.menu_type == 'button' else menu.id
                    exists = SysRoleMenu.query.filter_by(
                        role_id=user.role_id,
                        menu_id=check_menu_id,
                        operation=operation
                    ).first()
                    return exists is not None
                menus = SysMenu.query.filter(
                    SysMenu.menu_code.like(f'{module}.{func}')
                ).all()
                if menus:
                    menu_ids = [m.id for m in menus]
                    exists = SysRoleMenu.query.filter(
                        SysRoleMenu.role_id == user.role_id,
                        SysRoleMenu.menu_id.in_(menu_ids),
                        SysRoleMenu.operation == operation
                    ).first()
                    return exists is not None
                return False

            if len(parts) == 2:
                perm_code, operation = parts
                try:
                    menu_id = int(perm_code)
                    exists = SysRoleMenu.query.filter(
                        SysRoleMenu.role_id == user.role_id,
                        SysRoleMenu.menu_id == menu_id,
                        SysRoleMenu.operation == operation
                    ).first()
                    return exists is not None
                except ValueError:
                    menus = SysMenu.query.filter(
                        (SysMenu.permission == perm_code) |
                        (SysMenu.menu_code == perm_code)
                    ).all()
                    if menus:
                        menu_ids = [m.id for m in menus]
                        exists = SysRoleMenu.query.filter(
                            SysRoleMenu.role_id == user.role_id,
                            SysRoleMenu.menu_id.in_(menu_ids),
                            SysRoleMenu.operation == operation
                        ).first()
                        return exists is not None
                return False

        try:
            menu_id = int(permission)
            exists = SysRoleMenu.query.filter(
                SysRoleMenu.role_id == user.role_id,
                SysRoleMenu.menu_id == menu_id,
                SysRoleMenu.operation == 'view'
            ).first()
            return exists is not None
        except ValueError:
            pass

        return False

    def get_user_menu_tree(self, user):
        """获取用户有权限的菜单树（含按钮权限）

        Returns:
            list: 菜单树结构，每个节点包含 id, name, type, permission, children 等
        """
        from app.models import SysMenu, SysModule

        enabled_modules = {}
        for m in SysModule.query.all():
            enabled_modules[m.module_key] = bool(m.status)

        allowed_menu_ids = set()
        if user.is_admin():
            allowed_menu_ids = set(m.id for m in SysMenu.query.all())
        else:
            from app.models import SysRoleMenu
            rms = SysRoleMenu.query.filter_by(role_id=user.role_id).all()
            allowed_menu_ids = set(rm.menu_id for rm in rms)

        def build_tree(parent_id=0):
            children = []
            menus = SysMenu.query.filter_by(
                parent_id=parent_id,
                status=True
            ).order_by(SysMenu.sort).all()

            for menu in menus:
                if menu.module_key and not enabled_modules.get(menu.module_key, False):
                    continue

                if menu.menu_type == 'catalog':
                    sub_children = build_tree(menu.id)
                    if sub_children:
                        children.append({
                            'id': menu.id,
                            'name': menu.menu_name,
                            'code': menu.menu_code,
                            'type': menu.menu_type,
                            'icon': menu.icon or '',
                            'path': menu.path or '',
                            'component': menu.component or '',
                            'permission': menu.permission or '',
                            'moduleKey': menu.module_key,
                            'children': sub_children
                        })
                elif menu.menu_type == 'menu':
                    if user.is_admin() or menu.id in allowed_menu_ids:
                        children.append({
                            'id': menu.id,
                            'name': menu.menu_name,
                            'code': menu.menu_code,
                            'type': menu.menu_type,
                            'icon': menu.icon or '',
                            'path': menu.path or '',
                            'component': menu.component or '',
                            'permission': menu.permission or '',
                            'moduleKey': menu.module_key,
                            'children': build_tree(menu.id)
                        })
                elif menu.menu_type == 'button':
                    pass

            return children

        return build_tree()

    def get_user_permissions_list(self, user):
        """获取用户所有按钮权限标识列表

        Returns:
            list: 权限标识数组，如 ['system:dept:view', 'stock:in:create']
        """
        from app.models import SysMenu, SysRoleMenu, SysModule

        enabled_modules = {}
        for m in SysModule.query.all():
            enabled_modules[m.module_key] = bool(m.status)

        permissions = []

        if user.is_admin():
            all_menus = SysMenu.query.filter_by(menu_type='menu').all()
            for menu in all_menus:
                if menu.permission:
                    if menu.module_key and not enabled_modules.get(menu.module_key, False):
                        continue
                    base_perm = menu.permission.rsplit(':', 1)[0]
                    for op in ['view', 'create', 'edit', 'delete', 'import', 'export', 'approve', 'print']:
                        permissions.append(f"{base_perm}:{op}")
        else:
            role_menus = SysRoleMenu.query.filter_by(role_id=user.role_id).all()
            for rm in role_menus:
                menu = SysMenu.query.get(rm.menu_id)
                if menu and menu.permission:
                    if menu.module_key and not enabled_modules.get(menu.module_key, False):
                        continue
                    base_perm = menu.permission.rsplit(':', 1)[0]
                    permissions.append(f"{base_perm}:{rm.operation}")

        return permissions

    def get_parent_menu_ids(self, menu_id):
        """递归获取菜单的所有上级父菜单ID列表

        Args:
            menu_id: 菜单ID

        Returns:
            list: 父菜单ID列表（不含自身）
        """
        from app.models import SysMenu

        parent_ids = []
        menu = SysMenu.query.get(menu_id)
        while menu and menu.parent_id and menu.parent_id != 0:
            parent_ids.append(menu.parent_id)
            menu = SysMenu.query.get(menu.parent_id)
        return parent_ids

    def apply_parent_menu_permissions(self, role_id, menu_ids_with_ops):
        """自动为子菜单权限补全父菜单的查看权限

        Args:
            role_id: 角色ID
            menu_ids_with_ops: set of (menu_id, operation) tuples

        Returns:
            set: 补全后的权限集合
        """
        from app.models import SysMenu, SysRoleMenu

        result = set(menu_ids_with_ops)

        for menu_id, operation in list(result):
            parent_ids = self.get_parent_menu_ids(menu_id)
            for parent_id in parent_ids:
                parent_menu = SysMenu.query.get(parent_id)
                if parent_menu and parent_menu.menu_type != 'button':
                    result.add((parent_id, 'view'))

        return result

    def get_allowed_dept_ids(self, user):
        """获取用户有权限访问的部门ID集合

        Returns:
            set | None: None 表示拥有全部部门权限
        """
        if user.is_admin():
            return None

        scope_info = self.get_user_data_scope(user)
        data_scope = scope_info['scope']
        custom_depts = scope_info['custom_depts']

        from app.models import SysDept

        allowed_dept_ids = set()

        if data_scope == 'dept_and_sub' and user.dept_id:
            user_dept = SysDept.query.get(user.dept_id)
            if user_dept:
                allowed_dept_ids = set(user_dept.get_children_recursive())

        elif data_scope == 'dept' and user.dept_id:
            allowed_dept_ids = {user.dept_id}

        elif data_scope == 'self' and user.dept_id:
            allowed_dept_ids = {user.dept_id}

        elif data_scope == 'custom':
            for dept_id in custom_depts:
                dept = SysDept.query.get(dept_id)
                if dept:
                    allowed_dept_ids.update(dept.get_children_recursive())

        return allowed_dept_ids if allowed_dept_ids else None

    def get_user_visible_projects(self, user):
        """获取用户可见的 Project 对象列表"""
        from app.models import Project
        allowed = self.get_user_allowed_projects(user)
        if allowed is None:
            return Project.query.filter_by(is_archived=False).order_by(Project.created_at.desc()).all()
        if not allowed:
            return []
        return Project.query.filter(Project.id.in_(allowed), Project.is_archived == False).order_by(Project.created_at.desc()).all()

    def can_access_project(self, user, project_id):
        """检查用户是否可访问指定项目"""
        allowed = self.get_user_allowed_projects(user)
        if allowed is None:
            return True
        return project_id in allowed

    def compute_project_scope_by_role(self, role_id, dept_id):
        """根据角色ID和部门ID计算项目范围（用于创建/编辑用户时预计算）

        Args:
            role_id: 角色ID
            dept_id: 部门ID

        Returns:
            tuple: (project_ids, data_scope)
            - project_ids: 项目ID列表（None表示全部项目）
            - data_scope: 数据权限范围字符串
        """
        from app.models import SysRole, SysRoleDataScope, Project, SysDept

        role = SysRole.query.get(role_id) if role_id else None
        if not role:
            return [], 'all'

        scope_cfg = SysRoleDataScope.query.filter_by(role_id=role.id).first()
        data_scope = scope_cfg.data_scope if scope_cfg else role.data_scope or 'all'
        custom_depts = []

        if data_scope == 'all':
            return None, 'all'

        if scope_cfg and scope_cfg.custom_depts:
            import json as _json
            try:
                raw = _json.loads(scope_cfg.custom_depts)
                custom_depts = [int(x) for x in raw if str(x).isdigit()]
            except Exception:
                custom_depts = [int(x.strip()) for x in scope_cfg.custom_depts.split(',') if x.strip().isdigit()]

        project_ids = set()

        if data_scope == 'dept_and_sub':
            if dept_id:
                dept = SysDept.query.get(dept_id)
                if dept:
                    dept_ids = [dept_id]
                    try:
                        dept_ids.extend(dept.get_children_recursive())
                    except Exception:
                        pass
                    projects = Project.query.filter(
                        Project.dept_id.in_(dept_ids),
                        Project.is_archived == False
                    ).all()
                    for p in projects:
                        project_ids.add(p.id)

        elif data_scope == 'dept':
            if dept_id:
                projects = Project.query.filter_by(
                    dept_id=dept_id, is_archived=False
                ).all()
                for p in projects:
                    project_ids.add(p.id)

        elif data_scope == 'custom':
            if custom_depts:
                projects = Project.query.filter(
                    Project.dept_id.in_(custom_depts),
                    Project.is_archived == False
                ).all()
                for p in projects:
                    project_ids.add(p.id)

        return list(project_ids) if project_ids else [], data_scope

    def apply_data_scope_filter(self, query, model_cls, user=None):
        """对查询追加数据权限过滤

        Args:
            query: SQLAlchemy 查询对象
            model_cls: 模型类
            user: 用户对象（默认 current_user）

        Returns:
            query: 追加过滤条件后的查询对象
        """
        if user is None:
            user = current_user

        if not user.is_authenticated:
            from app import db
            return query.filter(db.false())

        if user.is_admin():
            return query

        scope_info = self.get_user_data_scope(user)
        data_scope = scope_info['scope']
        custom_depts = scope_info['custom_depts']

        if data_scope == 'all':
            return query

        from flask import session
        from app.utils import get_sub_dept_ids

        project_id = session.get('current_project_id')
        if project_id and hasattr(model_cls, 'project_id'):
            query = query.filter(model_cls.project_id == project_id)

        if data_scope == 'self':
            if hasattr(model_cls, 'created_by_id'):
                query = query.filter(model_cls.created_by_id == user.id)
            elif hasattr(model_cls, 'applicant_id'):
                query = query.filter(model_cls.applicant_id == user.id)
            elif hasattr(model_cls, 'operator_id'):
                query = query.filter(model_cls.operator_id == user.id)
            elif hasattr(model_cls, 'created_by'):
                query = query.filter(model_cls.created_by == user.username)
        elif data_scope == 'dept':
            if user.dept_id and hasattr(model_cls, 'dept_id'):
                query = query.filter(model_cls.dept_id == user.dept_id)
        elif data_scope == 'dept_and_sub':
            if user.dept_id and hasattr(model_cls, 'dept_id'):
                dept_ids = get_sub_dept_ids(user.dept_id)
                dept_ids.append(user.dept_id)
                query = query.filter(model_cls.dept_id.in_(dept_ids))
        elif data_scope == 'custom':
            if custom_depts and hasattr(model_cls, 'dept_id'):
                query = query.filter(model_cls.dept_id.in_(custom_depts))

        return query


    def get_org_data_scope(self, user):
        """获取用户组织数据范围（部门维度）

        方法三：获取用户组织数据范围
        输入：用户对象
        输出：数据权限类型 + 有权限的部门ID列表

        Returns:
            dict: {
                'scope': 'all' | 'dept_and_sub' | 'dept' | 'self' | 'custom',
                'dept_ids': list,   # 有权限的部门ID列表（all 时为 None 表示全部）
                'dept_names': list  # 部门名称列表（用于展示）
            }
        """
        scope_info = self.get_user_data_scope(user)
        data_scope = scope_info['scope']
        custom_depts = scope_info['custom_depts']

        if data_scope == 'all':
            return {'scope': 'all', 'dept_ids': None, 'dept_names': ['全部']}

        from app.models import SysDept
        dept_ids = set()

        if data_scope == 'dept_and_sub' and user.dept_id:
            dept = SysDept.query.get(user.dept_id)
            if dept:
                dept_ids.add(user.dept_id)
                try:
                    dept_ids.update(dept.get_children_recursive())
                except Exception:
                    pass

        elif data_scope == 'dept' and user.dept_id:
            dept_ids.add(user.dept_id)

        elif data_scope == 'self' and user.dept_id:
            dept_ids.add(user.dept_id)

        elif data_scope == 'custom':
            for dept_id in custom_depts:
                dept = SysDept.query.get(dept_id)
                if dept:
                    dept_ids.add(dept_id)
                    try:
                        dept_ids.update(dept.get_children_recursive())
                    except Exception:
                        pass

        dept_id_list = list(dept_ids)
        dept_names = []
        for did in dept_id_list:
            d = SysDept.query.get(did)
            if d:
                dept_names.append(d.name)

        return {
            'scope': data_scope,
            'dept_ids': dept_id_list,
            'dept_names': dept_names
        }

    def get_accessible_projects(self, user):
        """获取用户可访问的项目列表（含基础信息）

        方法四：获取用户可访问项目列表
        输入：用户对象
        输出：用户有权限的项目ID列表 + 项目基础信息

        Returns:
            list: 项目字典列表，每个项目包含 id, name, code, dept_id, status
        """
        projects = self.get_user_visible_projects(user)
        result = []
        for p in projects:
            result.append({
                'id': p.id,
                'name': p.name,
                'code': p.code,
                'dept_id': p.dept_id,
                'status': p.status
            })
        return result

    def get_menu_button_permissions(self, user, menu_identifier=None):
        """获取指定菜单下用户拥有的按钮权限集

        方法二：获取用户页面按钮权限集
        输入：用户对象 + 菜单路径/菜单ID
        输出：该菜单下用户拥有的按钮权限标识列表

        Args:
            user: 用户对象
            menu_identifier: 菜单标识，可以是菜单ID（int/str数字）、菜单路径、菜单code

        Returns:
            list: 按钮权限标识列表，如 ['view', 'create', 'edit', 'delete', 'export', 'import', 'approve', 'print']
        """
        if user.is_admin():
            return ['view', 'create', 'edit', 'delete', 'export', 'import', 'approve', 'print']

        from app.models import SysMenu, SysRoleMenu

        menu_id = None

        if menu_identifier is None:
            return []

        try:
            menu_id = int(menu_identifier)
        except (ValueError, TypeError):
            menu = SysMenu.query.filter(
                (SysMenu.path == menu_identifier) |
                (SysMenu.menu_code == menu_identifier)
            ).first()
            if menu:
                menu_id = menu.id

        if not menu_id:
            return []

        role_menus = SysRoleMenu.query.filter_by(
            role_id=user.role_id,
            menu_id=menu_id
        ).all()

        permissions = [rm.operation for rm in role_menus if rm.operation]
        return list(set(permissions))

    def get_permission_detail(self, user):
        """权限自检：获取用户所有权限明细（用于排查问题）

        输入：用户对象
        输出：该用户所有权限明细，确保配置、内核计算、前端显示三者可追溯

        Returns:
            dict: 完整的权限明细
        """
        from app.models import SysRole, SysMenu

        result = {
            'user': {
                'id': user.id,
                'username': user.username,
                'real_name': getattr(user, 'name', None) or getattr(user, 'real_name', None) or user.username,
                'dept_id': user.dept_id,
                'role_id': user.role_id,
                'is_admin': user.is_admin()
            },
            'role': None,
            'data_scope': {},
            'org_data_scope': {},
            'allowed_projects': [],
            'menu_tree': [],
            'button_permissions': [],
            'menu_permissions_detail': []
        }

        if user.role_id:
            role = SysRole.query.get(user.role_id)
            if role:
                result['role'] = {
                    'id': role.id,
                    'role_code': role.role_code,
                    'role_name': role.role_name,
                    'data_scope': role.data_scope
                }

        result['data_scope'] = self.get_user_data_scope(user)

        result['org_data_scope'] = self.get_org_data_scope(user)

        result['allowed_projects'] = self.get_accessible_projects(user)

        result['menu_tree'] = self.get_user_menu_tree(user)

        result['button_permissions'] = self.get_user_permissions_list(user)

        if not user.is_admin() and user.role_id:
            from app.models import SysRoleMenu
            rms = SysRoleMenu.query.filter_by(role_id=user.role_id).all()
            detail = []
            for rm in rms:
                menu = SysMenu.query.get(rm.menu_id)
                if menu:
                    detail.append({
                        'menu_id': rm.menu_id,
                        'menu_name': menu.menu_name,
                        'menu_code': menu.menu_code,
                        'menu_path': menu.path,
                        'menu_type': menu.menu_type,
                        'operation': rm.operation,
                        'permission': menu.permission
                    })
            result['menu_permissions_detail'] = detail

        return result


permission_service = PermissionService()