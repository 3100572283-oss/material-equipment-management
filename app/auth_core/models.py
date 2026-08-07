# app/auth_core/models.py
"""M0 权限中台数据模型（独立命名空间 auth_core_*）

绞杀者模式：干净重写，不引用旧权限代码，仅复用 app.db 绑定。
所有业务调用经 AuthGateway 接口，不直接触碰本模块表结构。
"""
from datetime import datetime
from flask_login import UserMixin
from app import db


class AuthOrgUnit(db.Model):
    """组织树：对齐铁建三级四层 + 两层管理（法人管项目）"""
    __tablename__ = 'auth_core_org_unit'
    id = db.Column(db.Integer, primary_key=True)
    org_code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    org_name = db.Column(db.String(128), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('auth_core_org_unit.id'), default=0)
    org_level = db.Column(db.Integer, default=4)  # 1股份公司 2二级集团 3三级公司 4项目部
    legal_entity = db.Column(db.Boolean, default=False)  # 是否法人节点
    dept_type = db.Column(db.String(16), default='dept')  # company/branch/project/dept/team
    project_id = db.Column(db.Integer, nullable=True)  # 项目部节点绑项目
    leader = db.Column(db.String(64), nullable=True)
    sort = db.Column(db.Integer, default=0)
    status = db.Column(db.Boolean, default=True)
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    children = db.relationship('AuthOrgUnit', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')

    def get_children_recursive(self):
        result = [self.id]
        for child in self.children.all():
            result.extend(child.get_children_recursive())
        return result


class AuthUser(UserMixin, db.Model):
    """用户：归属组织 + 岗位（选组织+岗位自动带权）"""
    __tablename__ = 'auth_core_user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(64), nullable=True)
    email = db.Column(db.String(128), nullable=True)
    phone = db.Column(db.String(32), nullable=True)
    org_id = db.Column(db.Integer, db.ForeignKey('auth_core_org_unit.id'), nullable=True)
    post = db.Column(db.String(64), nullable=True)  # 岗位：项目经理/物资部长...
    dept_id = db.Column(db.Integer, nullable=True)  # 旧 sys_dept.id，供 Project.dept_id 查询兼容
    status = db.Column(db.Boolean, default=True)
    must_change_password = db.Column(db.Boolean, default=False)
    failed_login_count = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(64), nullable=True)
    source = db.Column(db.String(16), default='self')  # etl/self
    created_at = db.Column(db.DateTime, default=datetime.now)

    # ---------------- 旧系统兼容接口（委托 AuthGateway，使 current_user 引用零改写） ----------------
    @property
    def is_active(self):
        return bool(self.status)

    def is_admin(self):
        """超管判定：拥有 super_admin 角色即视为超管"""
        sa = AuthRole.query.filter_by(role_code='super_admin').first()
        if not sa:
            return False
        return AuthUserRole.query.filter_by(user_id=self.id, role_id=sa.id).first() is not None

    def has_permission(self, permission):
        from app.auth_core.gateway import AuthGateway
        return AuthGateway.check_permission(self.id, permission)

    @property
    def roles(self):
        """该用户的全部角色对象列表（支持多角色）"""
        rids = [ur.role_id for ur in AuthUserRole.query.filter_by(user_id=self.id).all()]
        if not rids:
            return []
        return AuthRole.query.filter(AuthRole.id.in_(rids)).all()

    @property
    def primary_role(self):
        """主角色：多角色时优先超管，其次数据范围最宽者（避免 first() 顺序不定）"""
        rs = self.roles
        if not rs:
            return None
        for r in rs:
            if r.role_code == 'super_admin':
                return r
        rank = {'self': 0, 'project': 1, 'custom': 1, 'legal_entity': 2, 'all': 3}
        best, best_rank = rs[0], -1
        for r in rs:
            ds = AuthDataScope.query.filter_by(role_id=r.id).first()
            cur = rank.get(ds.scope_type if ds else 'project', 1)
            if cur > best_rank:
                best, best_rank = r, cur
        return best

    @property
    def role_obj(self):
        """兼容旧 User.role_obj：返回带 id/role_code/role_name/data_scope 的轻对象"""
        role = self.primary_role
        if not role:
            return None

        class _Role:
            pass

        r = _Role()
        r.id = role.id
        r.role_code = role.role_code
        r.role_name = role.role_name
        ds = AuthDataScope.query.filter_by(role_id=role.id).first()
        r.data_scope = ds.scope_type if ds else 'project'
        return r

    @property
    def role_id(self):
        role = self.primary_role
        return role.id if role else None

    @property
    def role(self):
        ro = self.role_obj
        return ro.role_code if ro else None

    def get_role_code(self):
        ro = self.role_obj
        return ro.role_code if ro else None

    def get_role_name(self):
        ro = self.role_obj
        return ro.role_name if ro else None

    def can_edit(self):
        editable = ('super_admin', 'material_admin', 'material_manager', 'finance_user',
                    'finance', 'admin', 'editor', 'ROLE001', 'ROLE002', 'ROLE006',
                    'ROLE007', 'material_staff', 'project_admin')
        return self.is_admin() or (self.get_role_code() in editable)

    @property
    def data_scope(self):
        from app.auth_core.gateway import AuthGateway
        return AuthGateway.get_data_scope(self.id)['scope_type']

    def get_data_scope(self):
        from app.auth_core.gateway import AuthGateway
        return AuthGateway.get_data_scope(self.id)['scope_type']

    def get_allowed_projects(self):
        from app.auth_core.gateway import AuthGateway
        sc = AuthGateway.get_data_scope(self.id)
        return None if sc['scope_type'] == 'all' else (sc['project_ids'] or [])

    def get_visible_projects(self):
        from app.auth_core.gateway import AuthGateway
        from app.models import Project
        sc = AuthGateway.get_data_scope(self.id)
        if sc['scope_type'] == 'all':
            return Project.query.filter_by(is_archived=False).all()
        pids = sc['project_ids'] or []
        if not pids:
            return []
        return Project.query.filter(Project.id.in_(pids), Project.is_archived == False).all()

    def can_access_project(self, project_id):
        allowed = self.get_allowed_projects()
        if allowed is None:
            return True
        return int(project_id) in [int(p) for p in allowed]

    def get_main_project(self):
        projects = self.get_visible_projects()
        return projects[0] if projects else None

    @property
    def user_projects(self):
        import json as _json
        uds = AuthUserDataScope.query.filter_by(user_id=self.id).first()
        pids = []
        if uds and uds.project_ids:
            try:
                pids = _json.loads(uds.project_ids)
            except Exception:
                pids = []
        result = []
        for pid in pids:
            class _UP:
                pass
            up = _UP()
            up.project_id = pid
            up.is_main = False
            up.project = None
            result.append(up)
        return result

    org = db.relationship('AuthOrgUnit', backref='users')


class AuthRole(db.Model):
    """角色模板：按铁建岗位预设"""
    __tablename__ = 'auth_core_role'
    id = db.Column(db.Integer, primary_key=True)
    role_code = db.Column(db.String(64), unique=True, nullable=False)
    role_name = db.Column(db.String(128), nullable=False)
    is_system = db.Column(db.Boolean, default=True)
    sort = db.Column(db.Integer, default=0)
    remark = db.Column(db.String(256), nullable=True)
    status = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class AuthPermission(db.Model):
    """功能权限点（替代旧 sys_menu.permission 混用）"""
    __tablename__ = 'auth_core_permission'
    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(64), nullable=False)        # material/equipment/cost/subcontract...
    resource = db.Column(db.String(64), nullable=False)      # stock_in/contract/supplier...
    action = db.Column(db.String(32), nullable=False)        # view/create/edit/delete/export/approve
    perm_key = db.Column(db.String(128), unique=True, nullable=False, index=True)  # material:stock_in:create
    module_name = db.Column(db.String(64), nullable=True)     # 模块中文名（物资管理/设备管理等），供权限矩阵展示
    resource_name = db.Column(db.String(128), nullable=True)  # 资源中文名（入库管理/合同台账等），供权限矩阵展示
    status = db.Column(db.Boolean, default=True)
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class AuthRolePermission(db.Model):
    __tablename__ = 'auth_core_role_permission'
    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('auth_core_role.id'), nullable=False)
    permission_id = db.Column(db.Integer, db.ForeignKey('auth_core_permission.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('role_id', 'permission_id', name='uq_ac_role_perm'),)


class AuthUserRole(db.Model):
    """用户-角色（支持多角色）"""
    __tablename__ = 'auth_core_user_role'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('auth_core_user.id'), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('auth_core_role.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'role_id', name='uq_ac_user_role'),)


class AuthDataScope(db.Model):
    """角色级数据范围（整合旧 users.data_scope + sys_role.data_scope + sys_role_dept）"""
    __tablename__ = 'auth_core_data_scope'
    id = db.Column(db.Integer, primary_key=True)
    scope_type = db.Column(db.String(16), default='project')  # self/project/legal_entity/all/custom
    role_id = db.Column(db.Integer, db.ForeignKey('auth_core_role.id'), nullable=True)
    org_ids = db.Column(db.Text, nullable=True)  # JSON 组织ID集
    created_at = db.Column(db.DateTime, default=datetime.now)


class AuthUserDataScope(db.Model):
    """用户级数据范围覆盖（替代旧 users.allowed_projects）"""
    __tablename__ = 'auth_core_user_data_scope'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('auth_core_user.id'), nullable=False)
    scope_type = db.Column(db.String(16), default='project')
    org_ids = db.Column(db.Text, nullable=True)
    project_ids = db.Column(db.Text, nullable=True)  # JSON 项目ID集
    created_at = db.Column(db.DateTime, default=datetime.now)


class AuthMenu(db.Model):
    """菜单目录（替代旧 sys_menu 的目录/结构本体；可见性仍由 auth_core 权限派生）

    绞杀者阶段 A：先作为影子目录与 sys_menu 并存（ETL 同步），经 parity 验证一致后，
    再将侧边栏/面包屑等活路径从 sys_menu 切到本表，最终 RENAME 退役 sys_menu。
    permission 列与 auth_core_permission.perm_key 同命名空间（module:resource:action）。
    """
    __tablename__ = 'auth_core_menu'
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('auth_core_menu.id'), nullable=True, default=None)
    menu_name = db.Column(db.String(128), nullable=False)
    menu_code = db.Column(db.String(64), nullable=True, unique=True)  # 与 Flask 端点名一致
    menu_type = db.Column(db.String(16), default='menu')  # catalog/menu/button
    path = db.Column(db.String(256), nullable=True)
    component = db.Column(db.String(256), nullable=True)
    icon = db.Column(db.String(64), nullable=True)
    sort = db.Column(db.Integer, default=0)
    status = db.Column(db.Boolean, default=True)
    permission = db.Column(db.String(128), nullable=True)  # 权限标识，如 stock:in:view
    module_key = db.Column(db.String(32), nullable=True)
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    children = db.relationship('AuthMenu', backref=db.backref('parent_menu', remote_side=[id]), lazy='dynamic')
