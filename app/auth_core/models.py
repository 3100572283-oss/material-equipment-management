# app/auth_core/models.py
"""M0 权限中台数据模型（独立命名空间 auth_core_*）

绞杀者模式：干净重写，不引用旧权限代码，仅复用 app.db 绑定。
所有业务调用经 AuthGateway 接口，不直接触碰本模块表结构。
"""
from datetime import datetime
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


class AuthUser(db.Model):
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
    status = db.Column(db.Boolean, default=True)
    must_change_password = db.Column(db.Boolean, default=False)
    failed_login_count = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(64), nullable=True)
    source = db.Column(db.String(16), default='self')  # etl/self
    created_at = db.Column(db.DateTime, default=datetime.now)

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
