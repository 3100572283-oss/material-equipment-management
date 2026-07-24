from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from app import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    # 使用 joinedload 预加载角色,避免每次访问 role_obj 都触发 DB 查询
    return User.query.options(db.joinedload(User.role_obj)).get(int(user_id))


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='viewer')  # 兼容旧数据，逐步迁移到role_id
    role_id = db.Column(db.Integer, db.ForeignKey('sys_role.id'), nullable=True)
    dept_id = db.Column(db.Integer, db.ForeignKey('sys_dept.id'), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    name = db.Column(db.String(64), nullable=True)
    department = db.Column(db.String(64), nullable=True)  # 兼容旧数据
    email = db.Column(db.String(128), nullable=True)
    phone = db.Column(db.String(32), nullable=True)
    data_scope = db.Column(db.String(16), default='all')  # all/dept/dept_and_sub/custom/self
    allowed_projects = db.Column(db.Text, nullable=True)  # JSON数组，可访问项目ID列表
    can_view_amount = db.Column(db.Boolean, default=True)  # 是否可查看金额
    notify_channels = db.Column(db.Text, nullable=True)  # JSON，通知渠道偏好
    per_page = db.Column(db.Integer, default=10)  # 每页显示条数偏好
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_login_at = db.Column(db.DateTime, nullable=True)
    failed_login_count = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(64), nullable=True)
    must_change_password = db.Column(db.Boolean, default=False)

    dept = db.relationship('SysDept', backref=db.backref('users', lazy='dynamic'))
    role_obj = db.relationship('SysRole', backref=db.backref('users', lazy='dynamic'))

    def is_admin(self):
        if self.role_obj and self.role_obj.role_code == 'super_admin':
            return True
        # 兜底：兼容老数据/role 字段被清空的情况
        if not self.role_obj and self.role:
            return self.role == 'admin'
        return self.role == 'admin'

    def is_editor(self):
        if self.role_obj:
            editable_codes = ('super_admin', 'material_admin', 'material_manager',
                              'finance_user', 'finance', 'admin', 'editor',
                              'ROLE001', 'ROLE002', 'ROLE006', 'ROLE007',
                              'material_staff', 'project_admin')
            if self.role_obj.role_code in editable_codes:
                return True
        return self.role in ('admin', 'editor')

    def is_viewer(self):
        return self.role in ('admin', 'editor', 'viewer')

    def can_edit(self):
        if self.role_obj:
            editable_codes = ('super_admin', 'material_admin', 'material_manager',
                             'finance_user', 'finance', 'admin', 'editor', 'material_staff',
                             'ROLE001', 'ROLE002', 'ROLE006', 'ROLE007',
                             'project_admin')
            return self.role_obj.role_code in editable_codes
        return self.role in ('admin', 'editor')

    def get_role_code(self):
        """获取当前用户角色代码"""
        if self.role_obj:
            return self.role_obj.role_code
        return self.role

    def get_role_name(self):
        """获取当前用户角色显示名"""
        if self.role_obj:
            return self.role_obj.role_name
        return {'admin': '管理员', 'editor': '录入员', 'viewer': '查看员'}.get(self.role, self.role)

    def has_permission(self, permission):
        """检查用户是否拥有指定按钮权限

        Args:
            permission: 权限标识,支持多种格式:
                - 'stock_in:create' (旧格式: 权限代码:操作)
                - '26:view' (菜单ID:操作)
                - 'stock:in:create' (新格式: 模块:功能:操作)
                - '26' (仅菜单ID, 默认检查view权限)

        Returns:
            bool: 是否有权限
        """
        if not permission:
            return True
        if self.is_admin():
            return True
        if not self.role_id:
            if self.role in ('admin', 'editor'):
                return True
            return False
        from app.models import SysMenu, SysRoleMenu

        # 处理冒号分隔的权限标识
        if ':' in permission:
            parts = permission.split(':')

            # 三段落格式: module:func:operation (如 stock:in:create)
            if len(parts) == 3:
                module, func, operation = parts
                # 查找匹配的菜单
                menus = SysMenu.query.filter(
                    SysMenu.menu_code.like(f'{module}.{func}')
                ).all()
                if menus:
                    menu_ids = [m.id for m in menus]
                    exists = SysRoleMenu.query.filter(
                        SysRoleMenu.role_id == self.role_id,
                        SysRoleMenu.menu_id.in_(menu_ids),
                        SysRoleMenu.operation == operation
                    ).first()
                    return exists is not None
                return False

            # 两段落格式: 可能是 menu_id:operation 或 perm_code:operation
            if len(parts) == 2:
                perm_code, operation = parts
                try:
                    # 尝试解析为菜单ID
                    menu_id = int(perm_code)
                    exists = SysRoleMenu.query.filter(
                        SysRoleMenu.role_id == self.role_id,
                        SysRoleMenu.menu_id == menu_id,
                        SysRoleMenu.operation == operation
                    ).first()
                    return exists is not None
                except ValueError:
                    # 作为权限代码查找
                    menus = SysMenu.query.filter_by(permission=perm_code).all()
                    if menus:
                        menu_ids = [m.id for m in menus]
                        exists = SysRoleMenu.query.filter(
                            SysRoleMenu.role_id == self.role_id,
                            SysRoleMenu.menu_id.in_(menu_ids),
                            SysRoleMenu.operation == operation
                        ).first()
                        return exists is not None
                return False

        # 纯数字: 菜单ID, 默认检查 view 权限
        try:
            menu_id = int(permission)
            exists = SysRoleMenu.query.filter(
                SysRoleMenu.role_id == self.role_id,
                SysRoleMenu.menu_id == menu_id,
                SysRoleMenu.operation == 'view'
            ).first()
            return exists is not None
        except ValueError:
            pass

        return False

    def get_allowed_projects(self):
        """获取用户可访问的项目ID列表

        返回 None 表示拥有全部数据权限，可访问所有项目；
        返回列表表示仅可访问这些项目ID。
        可见项目 = 数据权限为全部 → 所有项目 ∪ 用户归属部门及下级关联项目 ∪ 直接分配项目
        """
        if self.get_data_scope() == 'all' or self.is_admin():
            return None

        project_ids = set()

        # 1. 用户归属部门及下级部门关联的项目
        if self.dept_id:
            dept = SysDept.query.get(self.dept_id)
            if dept:
                dept_ids = dept.get_children_recursive()
                depts = SysDept.query.filter(SysDept.id.in_(dept_ids), SysDept.project_id.isnot(None)).all()
                for d in depts:
                    project_ids.add(d.project_id)

        # 2. 用户被直接分配的项目（sys_user_project）
        for up in self.user_projects:
            project_ids.add(up.project_id)

        return list(project_ids) if project_ids else []

    def get_visible_projects(self):
        """获取用户可见的Project对象列表"""
        from app.models import Project
        allowed = self.get_allowed_projects()
        if allowed is None:
            return Project.query.filter_by(is_archived=False).order_by(Project.created_at.desc()).all()
        if not allowed:
            return []
        return Project.query.filter(Project.id.in_(allowed), Project.is_archived == False).order_by(Project.created_at.desc()).all()

    def get_main_project(self):
        """获取用户主项目，返回Project对象或None"""
        for up in self.user_projects:
            if up.is_main:
                return up.project
        # 没有主项目时取第一个
        if self.user_projects:
            return self.user_projects[0].project
        return None

    def can_access_project(self, project_id):
        """检查用户是否可访问指定项目"""
        allowed = self.get_allowed_projects()
        if allowed is None:
            return True
        return project_id in allowed

    def can_view_amount_field(self):
        return self.can_view_amount if self.can_view_amount is not None else True

    def get_role_code(self):
        """获取角色编码"""
        if self.role_obj:
            return self.role_obj.role_code
        return self.role

    def get_data_scope(self):
        """获取数据权限范围（优先从 sys_role_data_scope 表读取）"""
        if self.role_obj:
            from app.models import SysRoleDataScope
            scope_cfg = SysRoleDataScope.query.filter_by(role_id=self.role_obj.id).first()
            if scope_cfg:
                return scope_cfg.data_scope or 'all'
            return self.role_obj.data_scope
        return self.data_scope


class SysDept(db.Model):
    """部门表 - 支持树形组织架构"""
    __tablename__ = 'sys_dept'
    id = db.Column(db.Integer, primary_key=True)
    dept_code = db.Column(db.String(64), nullable=False, unique=True)
    dept_name = db.Column(db.String(128), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('sys_dept.id'), default=0)
    dept_type = db.Column(db.String(16), default='dept')  # company/branch/project/dept/team 公司/分公司/项目部/部门/班组
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)  # 仅项目部类型有值
    leader = db.Column(db.String(64), nullable=True)  # 负责人
    sort = db.Column(db.Integer, default=0)
    status = db.Column(db.Boolean, default=True)
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    children = db.relationship('SysDept', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')
    project = db.relationship('Project', backref='depts')

    _DEPT_TYPE_MAP = {
        'company': '公司', 'branch': '分公司', 'project': '项目部',
        'dept': '部门', 'team': '班组',
    }

    def get_dept_type_display(self):
        return self._DEPT_TYPE_MAP.get(self.dept_type, self.dept_type)

    def get_children_recursive(self):
        """递归获取所有子部门ID列表"""
        result = [self.id]
        children = self.children.all()
        for child in children:
            result.extend(child.get_children_recursive())
        return result

    def has_children(self):
        return self.children.count() > 0

    def has_users(self):
        return self.users.count() > 0


class SysUserProject(db.Model):
    """用户项目关联表 - 支持一个用户归属/兼职多个项目"""
    __tablename__ = 'sys_user_project'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    is_main = db.Column(db.Boolean, default=False)  # 是否主项目

    user = db.relationship('User', backref=db.backref('user_projects', cascade='all, delete-orphan'))
    project = db.relationship('Project', backref=db.backref('user_projects', cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('user_id', 'project_id', name='uq_user_project'),
    )


class SysAnnouncement(db.Model):
    """系统公告表"""
    __tablename__ = 'sys_announcement'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    content = db.Column(db.Text, nullable=True)
    type = db.Column(db.String(16), default='notice')  # notice/maintenance/important
    is_popup = db.Column(db.Boolean, default=False)
    publish_time = db.Column(db.DateTime, default=datetime.now)
    expire_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.String(64), nullable=True)
    created_by_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    visible_scope = db.Column(db.String(16), default='all')  # all/role/dept
    visible_roles = db.Column(db.Text, nullable=True)  # JSON数组，角色ID列表
    visible_depts = db.Column(db.Text, nullable=True)  # JSON数组，部门ID列表

class SysAnnouncementRead(db.Model):
    """公告已读记录表"""
    __tablename__ = 'sys_announcement_read'
    id = db.Column(db.Integer, primary_key=True)
    announcement_id = db.Column(db.Integer, db.ForeignKey('sys_announcement.id'), nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    read_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('announcement_id', 'user_id', name='uq_announcement_user'),
    )


class SysRoleDataScope(db.Model):
    """角色统一数据权限配置表 - 一个角色一条配置

    整合原 sys_role.data_scope + sys_role_dept 的功能，
    做到功能权限和数据权限统一在角色权限中心管理。
    """
    __tablename__ = 'sys_role_data_scope'
    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('sys_role.id'), nullable=False, unique=True)
    data_scope = db.Column(db.String(16), default='all')  # all/dept_and_sub/dept/self/custom
    custom_depts = db.Column(db.Text, nullable=True)  # JSON数组，data_scope=custom时使用
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class SysRole(db.Model):
    """角色表"""
    __tablename__ = 'sys_role'
    id = db.Column(db.Integer, primary_key=True)
    role_code = db.Column(db.String(64), nullable=False, unique=True)
    role_name = db.Column(db.String(128), nullable=False)
    data_scope = db.Column(db.String(16), default='all')  # 兼容旧字段，逐步迁移到sys_role_data_scope
    status = db.Column(db.Boolean, default=True)
    sort = db.Column(db.Integer, default=0)
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    _DATA_SCOPE_MAP = {
        'all': '全部数据',
        'dept': '本部门数据',
        'dept_and_sub': '本部门及下级',
        'custom': '自定义数据权限',
        'self': '仅本人数据',
    }

    def get_data_scope_display(self):
        return self._DATA_SCOPE_MAP.get(self.data_scope, self.data_scope)


class SysModule(db.Model):
    """业务模块注册表 - 用于模块开关控制"""
    __tablename__ = 'sys_module'
    id = db.Column(db.Integer, primary_key=True)
    module_key = db.Column(db.String(32), nullable=False, unique=True)  # 模块标识，如 'module_turnover'
    module_name = db.Column(db.String(64), nullable=False)  # 模块名称
    is_required = db.Column(db.Boolean, default=False)  # 是否必需模块（不可关闭）
    default_enabled = db.Column(db.Boolean, default=True)  # 默认是否启用
    sort = db.Column(db.Integer, default=0)
    status = db.Column(db.Boolean, default=True)  # 是否启用
    remark = db.Column(db.String(256), nullable=True)


class SysMenu(db.Model):
    """菜单表 - 统一权限点管理"""
    __tablename__ = 'sys_menu'
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('sys_menu.id'), default=0)
    menu_name = db.Column(db.String(128), nullable=False)
    menu_code = db.Column(db.String(64), nullable=True, unique=True)
    menu_type = db.Column(db.String(16), default='menu')  # catalog/menu/button
    path = db.Column(db.String(256), nullable=True)
    component = db.Column(db.String(256), nullable=True)
    icon = db.Column(db.String(64), nullable=True)
    sort = db.Column(db.Integer, default=0)
    status = db.Column(db.Boolean, default=True)
    permission = db.Column(db.String(128), nullable=True)  # 权限标识，如 stock:in:view
    module_key = db.Column(db.String(32), nullable=True)  # 所属模块标识
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    children = db.relationship('SysMenu', backref=db.backref('parent_menu', remote_side=[id]), lazy='dynamic')


class SysRoleMenu(db.Model):
    """角色菜单权限表"""
    __tablename__ = 'sys_role_menu'
    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('sys_role.id'), nullable=False)
    menu_id = db.Column(db.Integer, db.ForeignKey('sys_menu.id'), nullable=False)
    operation = db.Column(db.String(16), nullable=False, default='view')

    __table_args__ = (
        db.UniqueConstraint('role_id', 'menu_id', 'operation', name='uq_role_menu_op'),
    )


class SysRoleDept(db.Model):
    """角色数据权限表（自定义数据权限时使用）"""
    __tablename__ = 'sys_role_dept'
    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('sys_role.id'), nullable=False)
    dept_id = db.Column(db.Integer, db.ForeignKey('sys_dept.id'), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('role_id', 'dept_id', name='uq_role_dept'),
    )


class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    code = db.Column(db.String(64), nullable=True)
    address = db.Column(db.String(256), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    planned_end_date = db.Column(db.Date, nullable=True)
    actual_end_date = db.Column(db.Date, nullable=True)
    manager = db.Column(db.String(64), nullable=True)
    contact_phone = db.Column(db.String(32), nullable=True)
    building_area = db.Column(db.Numeric(18, 2), default=0)  # 建筑面积（平方米）
    contract_amount = db.Column(db.Numeric(18, 2), default=0)  # 合同金额（元）
    status = db.Column(db.String(16), default='active')  # active在建/completed已竣工/suspended停工
    project_type = db.Column(db.String(32), nullable=True)  # 项目类型（字典 project_type）
    is_archived = db.Column(db.Boolean, default=False)
    module_config = db.Column(db.Text, nullable=True)  # JSON格式存储模块开关配置
    created_at = db.Column(db.DateTime, default=datetime.now)

    # Relationships
    materials = db.relationship('Material', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    suppliers = db.relationship('Supplier', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    categories = db.relationship('Category', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    usage_units = db.relationship('UsageUnit', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    work_numbers = db.relationship('WorkNumber', backref='project', lazy='dynamic', cascade='all, delete-orphan')

    _STATUS_MAP = {
        'active': '在建',
        'completed': '已竣工',
        'suspended': '停工',
    }

    def get_status_display(self):
        return self._STATUS_MAP.get(self.status, self.status)

    # 默认模块配置
    _DEFAULT_MODULES = {
        'module_turnover': False,       # 周转材管理
        'module_equipment': True,       # 设备管理
        'module_quality_check': False,  # 入库质检流程
        'module_batch': False,          # 批次保质期管理
        'module_approval': True,        # 审批流程
        'module_ai': False,             # AI助手功能
        'module_industry_tools': True,  # 行业工具（商砼、钢材、条码）
        'module_scrap': True,           # 物资报废
        'module_period_close': False,   # 期末结账
        'module_subcontract': False,    # 分包扣款
    }

    _MODULE_LABELS = {
        'module_turnover': ('周转材管理', '管理周转材料的出入库、摊销、盘点'),
        'module_equipment': ('设备管理', '管理设备台账、租赁、折旧、状态'),
        'module_quality_check': ('入库质检流程', '入库单增加待检状态，需质检后才能入库'),
        'module_batch': ('批次保质期管理', '出入库记录批次，库存按批次管理'),
        'module_approval': ('审批流程', '单据需审批后生效，支持多级审批'),
        'module_ai': ('AI助手功能', 'AI智能问答、数据洞察等AI功能'),
        'module_industry_tools': ('行业工具', '商砼小票、钢材过磅、条码扫描等工具'),
        'module_scrap': ('物资报废', '物资报废申请、审批、处理记录'),
        'module_period_close': ('期末结账', '月度期末结账，锁定历史数据'),
        'module_subcontract': ('分包扣款', '分包队伍扣款管理、台账统计'),
    }

    def get_module_config(self):
        """获取项目模块配置（合并默认值）"""
        import json
        config = dict(self._DEFAULT_MODULES)
        if self.module_config:
            try:
                user_config = json.loads(self.module_config)
                config.update(user_config)
            except Exception:
                pass
        return config

    def is_module_enabled(self, module_key):
        """检查指定模块是否启用"""
        config = self.get_module_config()
        return config.get(module_key, self._DEFAULT_MODULES.get(module_key, False))

    def set_module_config(self, config_dict):
        """设置模块配置（只保存非默认值）"""
        import json
        self.module_config = json.dumps(config_dict, ensure_ascii=False)

    @classmethod
    def get_default_module_config(cls):
        return dict(cls._DEFAULT_MODULES)

    @classmethod
    def get_module_labels(cls):
        return dict(cls._MODULE_LABELS)


class Supplier(db.Model):
    __tablename__ = 'suppliers'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    code = db.Column(db.String(64), nullable=True)
    credit_code = db.Column(db.String(64), nullable=True)
    contact_person = db.Column(db.String(64), nullable=True)
    phone = db.Column(db.String(32), nullable=True)
    legal_person = db.Column(db.String(64), nullable=True)
    address = db.Column(db.String(256), nullable=True)
    bank_name = db.Column(db.String(128), nullable=True)
    bank_account = db.Column(db.String(64), nullable=True)
    license_image = db.Column(db.String(256), nullable=True)
    license_expire_date = db.Column(db.Date, nullable=True)
    certificate_expire_date = db.Column(db.Date, nullable=True)
    opening_balance = db.Column(db.Numeric(18, 2), default=0)
    # 主数据统一改造：公司级/项目级 + 状态
    source = db.Column(db.String(16), default='project')  # company 公司级主库 / project 项目级
    status = db.Column(db.String(16), default='qualified')  # qualified 合格 / unqualified 不合格 / blacklist 黑名单
    create_dept = db.Column(db.Integer, nullable=True)  # 创建部门
    created_at = db.Column(db.DateTime, default=datetime.now)


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('categories.id'), default=0)  # 0=一级分类
    level = db.Column(db.Integer, default=1)  # 1/2/3
    category_code = db.Column(db.String(32), nullable=True)  # MC01 / MC0106 / MC010601
    name = db.Column(db.String(128), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    negative_stock_policy = db.Column(db.String(16), default='global')  # global / allow / forbid
    batch_management = db.Column(db.Boolean, default=False)  # 是否启用批次管理
    # 主数据统一改造：公司级统一分类（source=company 时全公司共享）
    source = db.Column(db.String(16), default='project')  # company 公司级 / project 项目级
    created_at = db.Column(db.DateTime, default=datetime.now)

    materials = db.relationship('Material', backref='category', lazy='dynamic')
    children = db.relationship('Category', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')


class Material(db.Model):
    __tablename__ = 'materials'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    name = db.Column(db.String(128), nullable=False)
    code = db.Column(db.String(64), nullable=True)
    specification = db.Column(db.String(256), nullable=True)
    unit = db.Column(db.String(32), nullable=False)
    remark = db.Column(db.Text, nullable=True)
    # 主数据统一改造：公司级/项目级 + 状态 + 创建部门
    source = db.Column(db.String(16), default='project')  # company 公司级主库 / project 项目级
    status = db.Column(db.String(16), default='active')  # active 启用 / inactive 停用
    create_dept = db.Column(db.Integer, nullable=True)  # 创建部门
    created_at = db.Column(db.DateTime, default=datetime.now)


# 主数据统一改造：项目常用物资关联表
class ProjectMaterial(db.Model):
    """项目常用物资关联表：记录每个项目从公司库勾选的常用物资子集"""
    __tablename__ = 'project_material'
    __table_args__ = (db.UniqueConstraint('project_id', 'material_id', name='uq_project_material_link'),)
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    is_common = db.Column(db.Boolean, default=True)  # 是否常用
    sort = db.Column(db.Integer, default=0)  # 项目内排序
    create_time = db.Column(db.DateTime, default=datetime.now)

    project = db.relationship('Project', backref='project_materials')
    material = db.relationship('Material', backref='project_links')


# 主数据统一改造：项目常用供应商关联表
class ProjectSupplier(db.Model):
    """项目常用供应商关联表：记录每个项目从公司库勾选的常用供应商子集"""
    __tablename__ = 'project_supplier'
    __table_args__ = (db.UniqueConstraint('project_id', 'supplier_id', name='uq_project_supplier_link'),)
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    is_common = db.Column(db.Boolean, default=True)  # 是否常用
    sort = db.Column(db.Integer, default=0)  # 项目内排序
    create_time = db.Column(db.DateTime, default=datetime.now)

    project = db.relationship('Project', backref='project_suppliers')
    supplier = db.relationship('Supplier', backref='project_links')


class UsageUnit(db.Model):
    __tablename__ = 'usage_units'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    code = db.Column(db.String(64), nullable=True)
    manager = db.Column(db.String(64), nullable=True)
    contact_phone = db.Column(db.String(32), nullable=True)
    auth_file = db.Column(db.String(256), nullable=True)
    is_subcontractor = db.Column(db.Boolean, default=False)  # 是否分包单位
    subcontract_contract = db.Column(db.String(128), nullable=True)  # 关联分包合同
    created_at = db.Column(db.DateTime, default=datetime.now)

    teams = db.relationship('UnitTeam', backref='unit', lazy='dynamic', cascade='all, delete-orphan')


class UnitTeam(db.Model):
    __tablename__ = 'unit_teams'
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey('usage_units.id'), nullable=False)
    team_name = db.Column(db.String(128), nullable=False)
    picker_name = db.Column(db.String(64), nullable=True)
    phone = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class WorkNumber(db.Model):
    __tablename__ = 'work_numbers'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('work_numbers.id'), nullable=True)
    code = db.Column(db.String(64), nullable=False)
    division_name = db.Column(db.String(128), nullable=True)
    item_name = db.Column(db.String(128), nullable=True)
    team_name = db.Column(db.String(128), nullable=True)
    picker = db.Column(db.String(64), nullable=True)
    remark = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    children = db.relationship('WorkNumber', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')
    quotas = db.relationship('MaterialQuota', backref='work_number', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def is_division(self):
        return self.parent_id is None

    @property
    def is_item(self):
        return self.parent_id is not None


class MaterialQuota(db.Model):
    __tablename__ = 'material_quotas'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    work_number_id = db.Column(db.Integer, db.ForeignKey('work_numbers.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    quota_type = db.Column(db.String(16), default='quantity')  # quantity / amount
    quota_quantity = db.Column(db.Numeric(18, 4), default=0)
    quota_amount = db.Column(db.Numeric(18, 2), default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    material = db.relationship('Material')

    __table_args__ = (
        db.UniqueConstraint('work_number_id', 'material_id', name='uq_worknum_material'),
    )


class Contract(db.Model):
    __tablename__ = 'contracts'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    code = db.Column(db.String(64), nullable=False, index=True)
    name = db.Column(db.String(256), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    contract_type = db.Column(db.String(32), nullable=True)  # 采购合同/租赁合同等
    business_type = db.Column(db.String(32), nullable=True)  # 材料采购/设备租赁等
    procurement_method = db.Column(db.String(32), nullable=True)  # 招标/议标/直接采购
    sign_date = db.Column(db.Date, nullable=True)
    amount_with_tax = db.Column(db.Numeric(18, 2), default=0)
    tax_rate = db.Column(db.Numeric(5, 2), default=13)  # 13/9/3/6
    amount_without_tax = db.Column(db.Numeric(18, 2), default=0)
    status = db.Column(db.String(32), default='正常履约')  # 正常履约/履约异常/已终止/已结算
    approval_status = db.Column(db.String(16), default='passed')  # draft/pending/approving/passed/rejected/withdrawn
    is_final_settled = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    is_litigated = db.Column(db.Boolean, default=False)
    attachment = db.Column(db.String(256), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    # Relationships
    items = db.relationship('ContractItem', backref='contract', lazy='dynamic', cascade='all, delete-orphan')
    invoices = db.relationship('Invoice', backref='contract', lazy='dynamic')
    payments = db.relationship('Payment', backref='contract', lazy='dynamic')
    stock_ins = db.relationship('StockIn', backref='contract', lazy='dynamic')

    def calc_amount_without_tax(self):
        try:
            rate = float(self.tax_rate or 0) / 100
            if rate > 0:
                return round(float(self.amount_with_tax or 0) / (1 + rate), 2)
        except Exception:
            pass
        return float(self.amount_with_tax or 0)


class ContractItem(db.Model):
    __tablename__ = 'contract_items'
    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    price_type = db.Column(db.String(32), default='固定单价')  # 固定单价/浮动单价
    quantity = db.Column(db.Numeric(18, 4), default=0)
    tax_rate = db.Column(db.Numeric(5, 2), default=13)
    unit_price_with_tax = db.Column(db.Numeric(18, 4), default=0)
    unit_price_without_tax = db.Column(db.Numeric(18, 4), default=0)
    amount_with_tax = db.Column(db.Numeric(18, 2), default=0)
    remark = db.Column(db.String(256), nullable=True)

    material = db.relationship('Material', backref='contract_items', lazy='select')
    total_in_qty = db.Column(db.Numeric(18, 4), default=0)  # 累计入库数量


class Invoice(db.Model):
    __tablename__ = 'invoices'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    invoice_code = db.Column(db.String(64), nullable=True)
    invoice_number = db.Column(db.String(64), nullable=True)
    invoice_date = db.Column(db.Date, nullable=True)
    amount_with_tax = db.Column(db.Numeric(18, 2), default=0)
    tax_rate = db.Column(db.Numeric(5, 2), default=13)
    amount_without_tax = db.Column(db.Numeric(18, 2), default=0)
    file_path = db.Column(db.String(256), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    payment_code = db.Column(db.String(64), nullable=False)
    payment_date = db.Column(db.Date, nullable=True)
    amount = db.Column(db.Numeric(18, 2), default=0)
    method = db.Column(db.String(32), default='银行转账')  # 银行转账/现金/承兑汇票
    approval_status = db.Column(db.String(16), default='passed')
    source_application_id = db.Column(db.Integer, db.ForeignKey('payment_applications.id'), nullable=True)
    amount_without_tax = db.Column(db.Numeric(18, 2), default=0)
    tax_amount = db.Column(db.Numeric(18, 2), default=0)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class PaymentApplication(db.Model):
    """付款申请单"""
    __tablename__ = 'payment_applications'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    application_code = db.Column(db.String(64), nullable=False, index=True)
    apply_date = db.Column(db.Date, nullable=True)
    applicant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    applicant_name = db.Column(db.String(64), nullable=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=False)
    reconciliation_ids = db.Column(db.String(512), nullable=True)  # 逗号分隔
    apply_amount = db.Column(db.Numeric(18, 2), default=0)
    payment_method = db.Column(db.String(32), default='银行转账')
    expected_payment_date = db.Column(db.Date, nullable=True)
    payment_description = db.Column(db.Text, nullable=True)
    attachment = db.Column(db.String(256), nullable=True)
    status = db.Column(db.String(16), default='draft')
    approval_status = db.Column(db.String(16), default='draft')
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'), nullable=True)
    change_reason = db.Column(db.String(256), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    supplier = db.relationship('Supplier', backref=db.backref('payment_applications', lazy='dynamic'))
    contract = db.relationship('Contract', backref=db.backref('payment_applications', lazy='dynamic'))
    applicant = db.relationship('User', foreign_keys=[applicant_id], backref=db.backref('payment_applications', lazy='dynamic'))
    generated_payment = db.relationship('Payment', foreign_keys=[payment_id], backref=db.backref('source_application', uselist=False))
    payments = db.relationship('Payment', backref='source_application_ref', foreign_keys='Payment.source_application_id', lazy='dynamic')

    @property
    def paid_amount(self):
        from app import db
        from sqlalchemy import func
        result = db.session.query(func.sum(Payment.amount)).filter(
            Payment.source_application_id == self.id,
            Payment.approval_status == 'passed'
        ).scalar()
        return float(result or 0)

    @property
    def unpaid_amount(self):
        return max(0, float(self.apply_amount or 0) - self.paid_amount)

    @property
    def payment_status(self):
        paid = self.paid_amount
        total = float(self.apply_amount or 0)
        if paid <= 0:
            return '未付款'
        if paid >= total:
            return '已付款'
        return '部分付款'

    @property
    def payment_status_badge(self):
        s = self.payment_status
        if s == '未付款':
            return (s, 'secondary')
        elif s == '部分付款':
            return (s, 'warning')
        else:
            return (s, 'success')


class StockIn(db.Model):
    __tablename__ = 'stock_ins'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    code = db.Column(db.String(64), nullable=False, index=True)
    stock_in_date = db.Column(db.Date, nullable=True)
    stock_in_type = db.Column(db.String(32), default='采购入库')
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    operator = db.Column(db.String(64), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    total_quantity = db.Column(db.Numeric(18, 4), default=0)
    total_amount = db.Column(db.Numeric(18, 2), default=0)
    estimated_amount = db.Column(db.Numeric(18, 2), default=0)
    actual_amount = db.Column(db.Numeric(18, 2), default=0)
    is_reconciled = db.Column(db.Boolean, default=False)
    is_initial = db.Column(db.Boolean, default=False)
    approval_status = db.Column(db.String(16), default='passed')
    quality_status = db.Column(db.String(16), default='draft')  # draft/pending/passed/rejected
    quality_checker = db.Column(db.String(64), nullable=True)
    quality_check_time = db.Column(db.DateTime, nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)
    dept_id = db.Column(db.Integer, db.ForeignKey('sys_dept.id'), nullable=True)
    status = db.Column(db.String(16), default='draft')
    quality_remark = db.Column(db.Text, nullable=True)
    location_lat = db.Column(db.Float, nullable=True)
    location_lng = db.Column(db.Float, nullable=True)
    location_accuracy = db.Column(db.Float, nullable=True)
    location_time = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    items = db.relationship('StockInItem', backref='stock_in', lazy='dynamic', cascade='all, delete-orphan')
    supplier = db.relationship('Supplier', backref=db.backref('stock_ins', lazy='dynamic'))


class StockInItem(db.Model):
    __tablename__ = 'stock_in_items'
    id = db.Column(db.Integer, primary_key=True)
    stock_in_id = db.Column(db.Integer, db.ForeignKey('stock_ins.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    quantity = db.Column(db.Numeric(18, 4), default=0)
    unit_price = db.Column(db.Numeric(18, 4), default=0)
    amount = db.Column(db.Numeric(18, 2), default=0)
    contract_item_id = db.Column(db.Integer, db.ForeignKey('contract_items.id'), nullable=True)
    is_over_contract = db.Column(db.Boolean, default=False)
    price_status = db.Column(db.String(16), default='unpriced')
    # 批次管理字段
    batch_no = db.Column(db.String(64), nullable=True)  # 批次号
    production_date = db.Column(db.Date, nullable=True)  # 生产日期
    shelf_life_days = db.Column(db.Integer, nullable=True)  # 保质期（天）
    expire_date = db.Column(db.Date, nullable=True)  # 到期日期（自动计算）

    material = db.relationship('Material', backref='stock_in_items', lazy='select')


class Inventory(db.Model):
    __tablename__ = 'inventory'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    quantity = db.Column(db.Numeric(18, 4), default=0)
    in_transit_qty = db.Column(db.Numeric(18, 4), default=0)  # 在途数量
    estimated_amount = db.Column(db.Numeric(18, 2), default=0)
    actual_amount = db.Column(db.Numeric(18, 2), default=0)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('project_id', 'material_id', name='uq_project_material'),
    )

    material = db.relationship('Material', backref='inventories', lazy='select')


class StockOut(db.Model):
    __tablename__ = 'stock_outs'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    code = db.Column(db.String(64), nullable=False, index=True)
    stock_out_date = db.Column(db.Date, nullable=True)
    stock_out_type = db.Column(db.String(32), default='工程领用')
    usage_unit_id = db.Column(db.Integer, db.ForeignKey('usage_units.id'), nullable=True)
    work_number_id = db.Column(db.Integer, db.ForeignKey('work_numbers.id'), nullable=True)
    issue_location = db.Column(db.String(256), nullable=True)
    purpose = db.Column(db.String(256), nullable=True)
    team_id = db.Column(db.Integer, db.ForeignKey('unit_teams.id'), nullable=True)
    operator = db.Column(db.String(64), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    total_quantity = db.Column(db.Numeric(18, 4), default=0)
    total_amount = db.Column(db.Numeric(18, 2), default=0)
    is_reconciled = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(16), default='approved')  # draft/pending/approved/rejected/voided
    approval_status = db.Column(db.String(16), default='passed')
    is_deleted = db.Column(db.Boolean, default=False)
    location_lat = db.Column(db.Float, nullable=True)
    location_lng = db.Column(db.Float, nullable=True)
    location_accuracy = db.Column(db.Float, nullable=True)
    location_time = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    items = db.relationship('StockOutItem', backref='stock_out', lazy='dynamic', cascade='all, delete-orphan')
    usage_unit = db.relationship('UsageUnit', backref='stock_outs')
    work_number = db.relationship('WorkNumber', backref='stock_outs')
    team = db.relationship('UnitTeam', backref='stock_outs')


class StockOutItem(db.Model):
    __tablename__ = 'stock_out_items'
    id = db.Column(db.Integer, primary_key=True)
    stock_out_id = db.Column(db.Integer, db.ForeignKey('stock_outs.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    quantity = db.Column(db.Numeric(18, 4), default=0)
    unit_price = db.Column(db.Numeric(18, 4), default=0)
    amount = db.Column(db.Numeric(18, 2), default=0)
    # 批次管理字段
    batch_no = db.Column(db.String(64), nullable=True)  # 出库的批次号

    material = db.relationship('Material', backref='stock_out_items', lazy='select')

    def __getattr__(self, name):
        if name == 'display_unit_price':
            return getattr(self, '_display_unit_price', self.unit_price)
        elif name == 'display_amount':
            return getattr(self, '_display_amount', self.amount)
        elif name == 'price_type':
            return getattr(self, '_price_type', None)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


class PriceFormula(db.Model):
    """价格方案（浮动价计算公式）"""
    __tablename__ = 'price_formula'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    formula_name = db.Column(db.String(128), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)  # 为空则通用
    material_category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    base_price_type = db.Column(db.String(16), default='手动输入')  # 信息价/合同价/手动输入
    float_type = db.Column(db.String(16), default='none')  # ratio(比例浮动)/amount(金额浮动)/none(不浮动)
    float_value = db.Column(db.Numeric(18, 4), default=0)  # 正数=上浮，负数=下浮，0=不浮动
    service_fee_rate = db.Column(db.Numeric(18, 4), default=0)  # 服务费率(%)
    service_fee_fixed = db.Column(db.Numeric(18, 4), default=0)  # 服务费固定金额
    capital_fee_rate = db.Column(db.Numeric(18, 4), default=0)  # 资金使用费率(月息%)
    capital_fee_days = db.Column(db.Integer, nullable=True)  # 资金占用天数
    tax_rate = db.Column(db.Numeric(5, 2), default=13)  # 税率%
    tax_included = db.Column(db.Boolean, default=True)  # 计算结果是否含税
    status = db.Column(db.String(16), default='启用')  # 启用/禁用
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    supplier = db.relationship('Supplier', backref=db.backref('price_formulas', lazy='dynamic'))

    @property
    def discount_type(self):
        if self.float_type == 'ratio':
            return '比例'
        elif self.float_type == 'amount':
            return '金额'
        return '不下浮'

    @property
    def discount_value(self):
        return abs(float(self.float_value or 0))


class Reconciliation(db.Model):
    __tablename__ = 'reconciliations'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    code = db.Column(db.String(64), nullable=False, index=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=False)
    price_formula_id = db.Column(db.Integer, db.ForeignKey('price_formula.id'), nullable=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(16), default='draft')  # draft/pending/approved/rejected/voided
    total_amount = db.Column(db.Numeric(18, 2), default=0)  # 含税合计
    total_amount_without_tax = db.Column(db.Numeric(18, 2), default=0)  # 不含税合计
    total_tax_amount = db.Column(db.Numeric(18, 2), default=0)  # 税额合计
    approval_status = db.Column(db.String(16), default='passed')
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    confirmed_at = db.Column(db.DateTime, nullable=True)
    confirmed_by = db.Column(db.String(64), nullable=True)

    supplier = db.relationship('Supplier', backref=db.backref('reconciliations', lazy='dynamic'))
    contract = db.relationship('Contract', backref=db.backref('reconciliations', lazy='dynamic'))
    price_formula = db.relationship('PriceFormula', backref=db.backref('reconciliations', lazy='dynamic'))
    items = db.relationship('ReconciliationItem', backref='reconciliation', lazy='dynamic', cascade='all, delete-orphan')


class ReconciliationItem(db.Model):
    __tablename__ = 'reconciliation_items'
    id = db.Column(db.Integer, primary_key=True)
    reconciliation_id = db.Column(db.Integer, db.ForeignKey('reconciliations.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    cost_subject = db.Column(db.String(128), nullable=True)
    price_type = db.Column(db.String(32), nullable=True)
    quantity = db.Column(db.Numeric(18, 4), default=0)
    unit_price = db.Column(db.Numeric(18, 4), default=0)  # 兼容原含税单价
    amount = db.Column(db.Numeric(18, 2), default=0)  # 兼容原含税金额
    base_price = db.Column(db.Numeric(18, 4), default=0)  # 基准价
    settlement_price = db.Column(db.Numeric(18, 4), default=0)  # 结算单价(含税)
    amount_without_tax = db.Column(db.Numeric(18, 2), default=0)  # 不含税金额
    tax_amount = db.Column(db.Numeric(18, 2), default=0)  # 税额
    amount_with_tax = db.Column(db.Numeric(18, 2), default=0)  # 含税金额
    calc_detail = db.Column(db.Text, nullable=True)  # 计算明细JSON
    specification = db.Column(db.String(256), nullable=True)
    unit = db.Column(db.String(32), nullable=True)

    source_stock_in_ids = db.Column(db.Text, nullable=True)
    material = db.relationship('Material', backref=db.backref('reconciliation_items', lazy='dynamic'))


class SystemConfig(db.Model):
    __tablename__ = 'system_config'
    id = db.Column(db.Integer, primary_key=True)
    config_key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    config_value = db.Column(db.String(256), nullable=True)
    description = db.Column(db.String(256), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class OperationLog(db.Model):
    __tablename__ = 'operation_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    username = db.Column(db.String(64), nullable=True)
    action = db.Column(db.String(32), nullable=False)  # 登录、新增、修改、删除
    module = db.Column(db.String(64), nullable=True)
    description = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', backref='operation_logs')


class SysDictType(db.Model):
    __tablename__ = 'sys_dict_type'
    id = db.Column(db.Integer, primary_key=True)
    dict_type = db.Column(db.String(64), unique=True, nullable=False, index=True)  # contract_type, payment_method...
    dict_name = db.Column(db.String(128), nullable=False)
    remark = db.Column(db.String(256), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    items = db.relationship('SysDictItem', backref='dict_type_ref', lazy='dynamic', cascade='all, delete-orphan')


class SysDictItem(db.Model):
    __tablename__ = 'sys_dict_item'
    id = db.Column(db.Integer, primary_key=True)
    dict_type_id = db.Column(db.Integer, db.ForeignKey('sys_dict_type.id'), nullable=False)
    item_label = db.Column(db.String(128), nullable=False)  # 显示文字
    item_value = db.Column(db.String(128), nullable=False)  # 存储值
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


# ========== AI助手 ==========

class AICallLog(db.Model):
    """AI调用日志表"""
    __tablename__ = 'ai_call_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(64), nullable=True)
    module = db.Column(db.String(64), nullable=True)  # chat/analysis/input/summary/approval
    prompt = db.Column(db.Text, nullable=True)
    response = db.Column(db.Text, nullable=True)
    input_tokens = db.Column(db.Integer, default=0)
    output_tokens = db.Column(db.Integer, default=0)
    cost_time = db.Column(db.Integer, default=0)
    success = db.Column(db.Boolean, default=True)
    error_msg = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


# ========== 审批流程引擎 ==========

class ApprovalFlow(db.Model):
    """审批流程定义表"""
    __tablename__ = 'approval_flow'
    id = db.Column(db.Integer, primary_key=True)
    flow_code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    flow_name = db.Column(db.String(128), nullable=False)
    biz_type = db.Column(db.String(32), nullable=False, index=True)  # contract/stockin/stockout/reconcile/payment
    enabled = db.Column(db.Boolean, default=True)
    has_branch = db.Column(db.Boolean, default=False)
    description = db.Column(db.String(256), nullable=True)
    scope = db.Column(db.String(16), default='company', index=True)  # company/project
    project_ids = db.Column(db.Text, nullable=True)  # JSON数组 ["1", "2"]
    is_default = db.Column(db.Boolean, default=False)  # 是否公司默认流程
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    nodes = db.relationship('ApprovalNode', backref='flow', lazy='dynamic',
                            cascade='all, delete-orphan', order_by='ApprovalNode.node_order')
    branches = db.relationship('ApprovalBranch', backref='flow', lazy='dynamic',
                               cascade='all, delete-orphan', order_by='ApprovalBranch.priority')
    instances = db.relationship('ApprovalInstance', backref='flow', lazy='dynamic')

    def get_project_list(self):
        """获取适用项目ID列表"""
        if not self.project_ids:
            return []
        import json
        try:
            return json.loads(self.project_ids)
        except:
            return []

    def is_project_flow(self):
        """是否项目级流程"""
        return self.scope == 'project'


class ApprovalBranch(db.Model):
    """审批流程分支表"""
    __tablename__ = 'approval_branch'
    id = db.Column(db.Integer, primary_key=True)
    flow_id = db.Column(db.Integer, db.ForeignKey('approval_flow.id'), nullable=False)
    branch_name = db.Column(db.String(128), nullable=False)
    condition_field = db.Column(db.String(64), nullable=True)
    condition_operator = db.Column(db.String(16), nullable=True)
    condition_value = db.Column(db.String(128), nullable=True)
    condition_logic = db.Column(db.String(16), default='AND')  # AND/OR 多条件组合关系
    is_default = db.Column(db.Boolean, default=False)
    priority = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.now)

    branch_nodes = db.relationship('ApprovalNode', backref='branch', lazy='dynamic',
                                   cascade='all, delete-orphan', order_by='ApprovalNode.node_order')
    conditions = db.relationship('ApprovalBranchCondition', backref='branch', lazy='dynamic',
                                 cascade='all, delete-orphan', order_by='ApprovalBranchCondition.sort')


class ApprovalBranchCondition(db.Model):
    """审批分支条件明细表"""
    __tablename__ = 'approval_branch_condition'
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('approval_branch.id'), nullable=False)
    sort = db.Column(db.Integer, default=1)  # 条件排序
    condition_field = db.Column(db.String(64), nullable=False)  # 条件字段
    condition_operator = db.Column(db.String(16), nullable=False)  # 运算符
    condition_value = db.Column(db.String(128), nullable=False)  # 条件值


class ApprovalNode(db.Model):
    """审批节点表"""
    __tablename__ = 'approval_node'
    id = db.Column(db.Integer, primary_key=True)
    flow_id = db.Column(db.Integer, db.ForeignKey('approval_flow.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('approval_branch.id'), nullable=True)
    node_order = db.Column(db.Integer, nullable=False)  # 1,2,3...
    node_name = db.Column(db.String(128), nullable=False)
    approve_type = db.Column(db.String(16), nullable=False, default='role')  # role / user
    approve_role = db.Column(db.String(64), nullable=True)  # 逗号分隔的角色，如 admin,editor
    approve_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    pass_rule = db.Column(db.String(16), default='ANY')  # ANY或签 / ALL会签
    sign_required = db.Column(db.Boolean, default=False)

    approver_user = db.relationship('User', foreign_keys=[approve_user_id])

    def can_approve(self, user):
        """判断用户是否可审批此节点"""
        if not user:
            return False
        if self.approve_type == 'user':
            return user.id == self.approve_user_id
        else:  # role
            roles = [r.strip() for r in (self.approve_role or '').split(',') if r.strip()]
            return user.role in roles

    def get_all_approvers(self):
        """获取所有审批人用户对象列表"""
        from app.models import User
        approvers = []
        if self.approve_type == 'user':
            if self.approve_user_id:
                user = User.query.get(self.approve_user_id)
                if user:
                    approvers.append(user)
        else:
            roles = [r.strip() for r in (self.approve_role or '').split(',') if r.strip()]
            if roles:
                users = User.query.filter(User.role.in_(roles)).all()
                approvers = users
        return list(set(approvers))


class ApprovalInstance(db.Model):
    """审批实例表"""
    __tablename__ = 'approval_instance'
    id = db.Column(db.Integer, primary_key=True)
    flow_id = db.Column(db.Integer, db.ForeignKey('approval_flow.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('approval_branch.id'), nullable=True)
    biz_type = db.Column(db.String(32), nullable=False, index=True)
    biz_id = db.Column(db.Integer, nullable=False, index=True)
    biz_title = db.Column(db.String(256), nullable=True)
    applicant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)
    submit_time = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(16), default='draft', index=True)  # draft/pending/approving/passed/rejected/withdrawn
    current_node_id = db.Column(db.Integer, db.ForeignKey('approval_node.id'), nullable=True)
    reject_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    applicant = db.relationship('User', foreign_keys=[applicant_id], backref='submitted_approvals')
    current_node = db.relationship('ApprovalNode', foreign_keys=[current_node_id])
    branch = db.relationship('ApprovalBranch', foreign_keys=[branch_id])
    project = db.relationship('Project', foreign_keys=[project_id])
    records = db.relationship('ApprovalRecord', backref='instance', lazy='dynamic',
                              cascade='all, delete-orphan', order_by='ApprovalRecord.approve_time')


class ApprovalRecord(db.Model):
    """审批记录表"""
    __tablename__ = 'approval_record'
    id = db.Column(db.Integer, primary_key=True)
    instance_id = db.Column(db.Integer, db.ForeignKey('approval_instance.id'), nullable=False)
    node_id = db.Column(db.Integer, db.ForeignKey('approval_node.id'), nullable=True)
    approver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(16), nullable=False)  # approve/reject/withdraw/skip
    opinion = db.Column(db.Text, nullable=True)
    approve_time = db.Column(db.DateTime, default=datetime.now)

    approver = db.relationship('User', foreign_keys=[approver_id])
    node = db.relationship('ApprovalNode', foreign_keys=[node_id])


# ========== 盘点管理 ==========

class StockCheck(db.Model):
    """盘点单主表"""
    __tablename__ = 'stock_check'
    id = db.Column(db.Integer, primary_key=True)
    check_no = db.Column(db.String(64), unique=True, nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    check_date = db.Column(db.Date, nullable=False)
    check_type = db.Column(db.String(16), default='full')  # full:全盘 / category:按分类
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    status = db.Column(db.String(16), default='draft')  # draft/ongoing/confirmed/cancelled
    checker = db.Column(db.String(64), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    items = db.relationship('StockCheckItem', backref='stock_check', lazy='dynamic',
                            cascade='all, delete-orphan')

    @property
    def diff_count(self):
        """差异笔数"""
        cnt = 0
        for item in self.items:
            if (item.actual_qty or 0) != (item.book_qty or 0):
                cnt += 1
        return cnt


class StockCheckItem(db.Model):
    """盘点明细表"""
    __tablename__ = 'stock_check_item'
    id = db.Column(db.Integer, primary_key=True)
    check_id = db.Column(db.Integer, db.ForeignKey('stock_check.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    material_name = db.Column(db.String(128), nullable=False)
    specification = db.Column(db.String(128), nullable=True)
    unit = db.Column(db.String(32), nullable=True)
    book_qty = db.Column(db.Numeric(18, 4), default=0)
    actual_qty = db.Column(db.Numeric(18, 4), nullable=True)
    diff_reason = db.Column(db.String(256), nullable=True)

    material = db.relationship('Material')

    @property
    def diff_qty(self):
        """差异数量 = 实盘 - 账面"""
        actual = float(self.actual_qty or 0)
        book = float(self.book_qty or 0)
        return actual - book


# ========== 采购申请 ==========

class PurchaseRequisition(db.Model):
    """采购申请主表"""
    __tablename__ = 'purchase_requisition'
    id = db.Column(db.Integer, primary_key=True)
    pr_no = db.Column(db.String(64), unique=True, nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    apply_dept = db.Column(db.String(64), nullable=True)
    apply_user = db.Column(db.String(64), nullable=True)
    apply_date = db.Column(db.Date, nullable=False)
    demand_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(16), default='draft')
    approval_instance_id = db.Column(db.Integer, db.ForeignKey('approval_instance.id'), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    items = db.relationship('PurchaseRequisitionItem', backref='purchase_requisition', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def item_count(self):
        return self.items.count()

    @property
    def unapproved_qty(self):
        total = 0
        for item in self.items:
            total += float(item.apply_qty or 0) - float(item.approve_qty or 0)
        return total


class PurchaseRequisitionItem(db.Model):
    """采购申请明细表"""
    __tablename__ = 'purchase_requisition_item'
    id = db.Column(db.Integer, primary_key=True)
    pr_id = db.Column(db.Integer, db.ForeignKey('purchase_requisition.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    material_name = db.Column(db.String(128), nullable=False)
    specification = db.Column(db.String(128), nullable=True)
    unit = db.Column(db.String(32), nullable=True)
    apply_qty = db.Column(db.Numeric(18, 4), default=0)
    approve_qty = db.Column(db.Numeric(18, 4), default=0)
    purpose = db.Column(db.String(256), nullable=True)
    converted = db.Column(db.Boolean, default=False)

    material = db.relationship('Material')


# ========== 项目间调拨 ==========

class MaterialTransfer(db.Model):
    """调拨单主表"""
    __tablename__ = 'material_transfer'
    id = db.Column(db.Integer, primary_key=True)
    transfer_no = db.Column(db.String(64), unique=True, nullable=False, index=True)
    from_project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    to_project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    transfer_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(16), default='draft')
    applicant = db.Column(db.String(64), nullable=True)
    handler = db.Column(db.String(64), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    approval_instance_id = db.Column(db.Integer, db.ForeignKey('approval_instance.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    items = db.relationship('MaterialTransferItem', backref='material_transfer', lazy='dynamic', cascade='all, delete-orphan')
    from_project = db.relationship('Project', foreign_keys=[from_project_id])
    to_project = db.relationship('Project', foreign_keys=[to_project_id])


class MaterialTransferItem(db.Model):
    """调拨明细表"""
    __tablename__ = 'material_transfer_item'
    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(db.Integer, db.ForeignKey('material_transfer.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    material_name = db.Column(db.String(128), nullable=False)
    specification = db.Column(db.String(128), nullable=True)
    unit = db.Column(db.String(32), nullable=True)
    transfer_qty = db.Column(db.Numeric(18, 4), default=0)
    out_qty = db.Column(db.Numeric(18, 4), default=0)
    in_qty = db.Column(db.Numeric(18, 4), default=0)

    material = db.relationship('Material')


# ========== 供应商评价 ==========

class SupplierEvaluation(db.Model):
    """供应商评价表"""
    __tablename__ = 'supplier_evaluation'
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    evaluate_period = db.Column(db.String(32), nullable=False)
    evaluate_date = db.Column(db.Date, nullable=False)
    evaluator = db.Column(db.String(64), nullable=True)
    quality_score = db.Column(db.Integer, default=0)
    price_score = db.Column(db.Integer, default=0)
    delivery_score = db.Column(db.Integer, default=0)
    service_score = db.Column(db.Integer, default=0)
    total_score = db.Column(db.Numeric(5, 2), default=0)
    level = db.Column(db.String(16), nullable=True)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    supplier = db.relationship('Supplier')


# ========== 周转材料管理 ==========

class TurnoverMaterial(db.Model):
    """周转材料基础表"""
    __tablename__ = 'turnover_material'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    code = db.Column(db.String(64), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    category = db.Column(db.String(64), nullable=True)
    specification = db.Column(db.String(128), nullable=True)
    unit = db.Column(db.String(32), nullable=True)
    material_type = db.Column(db.String(16), default='own')  # own自有/rental租赁
    rental_price = db.Column(db.Numeric(12, 4), default=0)
    rental_unit = db.Column(db.String(16), default='day')  # day/meter
    original_value = db.Column(db.Numeric(18, 2), default=0)
    amortize_months = db.Column(db.Integer, default=0)
    status = db.Column(db.String(16), default='active')
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class TurnoverInventory(db.Model):
    """周转材料库存表"""
    __tablename__ = 'turnover_inventory'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('turnover_material.id'), nullable=False)
    quantity = db.Column(db.Numeric(18, 4), default=0)
    rent_out_qty = db.Column(db.Numeric(18, 4), default=0)

    material = db.relationship('TurnoverMaterial')
    __table_args__ = (db.UniqueConstraint('project_id', 'material_id', name='uq_turnover_inv'),)


class TurnoverRecord(db.Model):
    """周转材料领用归还记录表"""
    __tablename__ = 'turnover_record'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('turnover_material.id'), nullable=False)
    team = db.Column(db.String(128), nullable=True)
    qty = db.Column(db.Numeric(18, 4), default=0)
    returned_qty = db.Column(db.Numeric(18, 4), default=0)
    lost_qty = db.Column(db.Numeric(18, 4), default=0)
    out_date = db.Column(db.Date, nullable=False)
    expect_return_date = db.Column(db.Date, nullable=True)
    actual_return_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(16), default='in_use')  # in_use在用/returned已归还/partial部分归还
    rent_fee = db.Column(db.Numeric(18, 2), default=0)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    material = db.relationship('TurnoverMaterial')

    @property
    def using_qty(self):
        return float(self.qty or 0) - float(self.returned_qty or 0) - float(self.lost_qty or 0)

    @property
    def using_days(self):
        from datetime import date
        end = self.actual_return_date or date.today()
        if self.out_date:
            return (end - self.out_date).days
        return 0


# ========== 设备管理 ==========

class Equipment(db.Model):
    """设备台账表"""
    __tablename__ = 'equipment'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    code = db.Column(db.String(64), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    source_type = db.Column(db.String(16), default='self')  # self自有/rent租赁/labor劳务队自带
    category = db.Column(db.String(64), nullable=True)
    specification = db.Column(db.String(128), nullable=True)
    manufacturer = db.Column(db.String(128), nullable=True)
    # 自有设备字段
    purchase_date = db.Column(db.Date, nullable=True)
    original_value = db.Column(db.Numeric(18, 2), default=0)
    use_years = db.Column(db.Integer, default=0)
    residual_rate = db.Column(db.Numeric(5, 2), default=5)
    # 租赁设备字段
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    rent_type = db.Column(db.String(16), nullable=True)  # shift台班/monthly月租/yearly年租
    rent_unit_price = db.Column(db.Numeric(18, 2), default=0)
    rent_period = db.Column(db.Integer, nullable=True)  # 租赁期限（配合rent_type单位）
    entry_date = db.Column(db.Date, nullable=True)  # 进场日期（租赁/劳务队自带共用）
    expected_exit_date = db.Column(db.Date, nullable=True)  # 预计退场日期
    entry_exit_fee = db.Column(db.Numeric(18, 2), default=0)  # 进出场费
    deposit = db.Column(db.Numeric(18, 2), default=0)  # 押金
    # 劳务队自带字段
    labor_team = db.Column(db.String(128), nullable=True)  # 所属劳务队
    # 退场信息
    exit_date = db.Column(db.Date, nullable=True)  # 实际退场日期
    # 共用字段
    department = db.Column(db.String(64), nullable=True)
    responsible = db.Column(db.String(64), nullable=True)
    location = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(16), default='in_use')  # in_use/idle/repairing/exited/scrapped
    photo = db.Column(db.String(256), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    supplier = db.relationship('Supplier', foreign_keys=[supplier_id])

    @property
    def monthly_depreciation(self):
        if not self.original_value or not self.use_years or self.use_years <= 0:
            return 0
        residual = float(self.original_value or 0) * float(self.residual_rate or 0) / 100
        depreciable = float(self.original_value or 0) - residual
        return round(depreciable / (self.use_years * 12), 2)

    @property
    def accumulated_depreciation(self):
        """累计折旧"""
        if not self.purchase_date or not self.original_value:
            return 0
        from datetime import date
        months = (date.today().year - self.purchase_date.year) * 12 + (date.today().month - self.purchase_date.month)
        if months < 0:
            months = 0
        total_months = (self.use_years or 0) * 12
        if total_months > 0 and months >= total_months:
            months = total_months
        return round(self.monthly_depreciation * months, 2)

    @property
    def net_value(self):
        """设备净值"""
        if not self.original_value:
            return 0
        return round(float(self.original_value) - self.accumulated_depreciation, 2)

    @property
    def source_label(self):
        """设备来源中文标签"""
        labels = {'self': '自有设备', 'rent': '租赁设备', 'labor': '劳务队自带'}
        return labels.get(self.source_type, self.source_type or '未知')


class EquipmentStatusLog(db.Model):
    """设备状态变更日志表"""
    __tablename__ = 'equipment_status_log'
    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False, index=True)
    old_status = db.Column(db.String(16), nullable=True)
    new_status = db.Column(db.String(16), nullable=False)
    exit_date = db.Column(db.Date, nullable=True)  # 退场时记录的退场日期
    operator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    operator_name = db.Column(db.String(64), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    equipment = db.relationship('Equipment', backref='status_logs')
    operator = db.relationship('User', foreign_keys=[operator_id])


class EquipmentRentSettle(db.Model):
    """设备租赁结算表"""
    __tablename__ = 'equipment_rent_settle'
    id = db.Column(db.Integer, primary_key=True)
    settle_no = db.Column(db.String(64), nullable=False, unique=True, index=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False, index=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    settle_period = db.Column(db.String(16), nullable=True)  # 结算期间，如2025年7月
    settle_start_date = db.Column(db.Date, nullable=True)
    settle_end_date = db.Column(db.Date, nullable=True)
    rent_type = db.Column(db.String(16), nullable=True)  # shift台班/monthly月租/yearly年租
    unit_price = db.Column(db.Numeric(18, 2), default=0)
    quantity = db.Column(db.Numeric(18, 2), default=0)  # 数量/台班数/天数
    rent_amount = db.Column(db.Numeric(18, 2), default=0)  # 租赁费 = 单价 × 数量
    other_fee = db.Column(db.Numeric(18, 2), default=0)  # 其他费用
    total_amount = db.Column(db.Numeric(18, 2), default=0)  # 结算总金额
    status = db.Column(db.String(16), default='draft')  # draft草稿/confirmed已确认/invoiced已开票/paid已付款
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    equipment = db.relationship('Equipment', backref='rent_settles')
    supplier = db.relationship('Supplier')


class EquipmentMaintenance(db.Model):
    """设备维保记录表"""
    __tablename__ = 'equipment_maintenance'
    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False)
    maintain_date = db.Column(db.Date, nullable=False)
    maintain_type = db.Column(db.String(16), default='保养')  # 保养/维修/巡检
    content = db.Column(db.Text, nullable=True)
    cost = db.Column(db.Numeric(18, 2), default=0)
    vendor = db.Column(db.String(128), nullable=True)
    next_maintain_date = db.Column(db.Date, nullable=True)
    operator = db.Column(db.String(64), nullable=True)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    equipment = db.relationship('Equipment')


# ========== 设备巡检 ==========

class EquipmentInspection(db.Model):
    """设备巡检计划表"""
    __tablename__ = 'equipment_inspection'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    plan_name = db.Column(db.String(128), nullable=False)
    plan_type = db.Column(db.String(32), default='regular')  # regular定期/temporary临时
    cycle = db.Column(db.String(16), default='monthly')  # daily/weekly/monthly/quarterly/yearly
    inspect_items = db.Column(db.Text, nullable=True)  # 巡检项JSON
    equipment_category = db.Column(db.String(64), nullable=True)  # 适用设备类别，空为全部
    responsible_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    responsible_name = db.Column(db.String(64), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    next_inspect_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(16), default='active')  # active/inactive
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    project = db.relationship('Project')
    responsible = db.relationship('User', foreign_keys=[responsible_id])


class EquipmentInspectionTask(db.Model):
    """设备巡检任务表（按计划生成的待执行任务）"""
    __tablename__ = 'equipment_inspection_task'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    inspection_id = db.Column(db.Integer, db.ForeignKey('equipment_inspection.id'), nullable=False)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False)
    task_no = db.Column(db.String(64), nullable=False, index=True)
    plan_inspect_date = db.Column(db.Date, nullable=False)
    assignee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    assignee_name = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(16), default='pending')  # pending/done/skipped/overdue
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    inspection = db.relationship('EquipmentInspection', backref='tasks')
    equipment = db.relationship('Equipment')
    assignee = db.relationship('User', foreign_keys=[assignee_id])


class EquipmentInspectionRecord(db.Model):
    """设备巡检记录表"""
    __tablename__ = 'equipment_inspection_record'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('equipment_inspection_task.id'), nullable=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False)
    inspect_date = db.Column(db.Date, nullable=False)
    inspector_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    inspector_name = db.Column(db.String(64), nullable=True)
    overall_status = db.Column(db.String(16), default='normal')  # normal/abnormal
    items_result = db.Column(db.Text, nullable=True)  # 巡检项结果JSON
    meter_reading = db.Column(db.String(64), nullable=True)  # 仪表读数
    issue_desc = db.Column(db.Text, nullable=True)  # 异常描述
    auto_maintenance = db.Column(db.Boolean, default=False)  # 是否已自动生成维修工单
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    equipment = db.relationship('Equipment')
    inspector = db.relationship('User', foreign_keys=[inspector_id])


# ========== 物资二维码 ==========

class MaterialQrCode(db.Model):
    """物资二维码表"""
    __tablename__ = 'material_qrcode'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    qr_code = db.Column(db.String(256), nullable=False, unique=True)
    batch_no = db.Column(db.String(64), nullable=True)
    print_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)


# ========== 审计日志（增强版） ==========

class SysOperationLog(db.Model):
    """系统操作审计日志"""
    __tablename__ = 'sys_operation_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(64), nullable=True)
    module = db.Column(db.String(64), nullable=True)
    operation = db.Column(db.String(32), nullable=True)
    biz_type = db.Column(db.String(64), nullable=True)
    biz_id = db.Column(db.Integer, nullable=True)
    method = db.Column(db.String(16), nullable=True)
    params = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(256), nullable=True)
    operation_time = db.Column(db.DateTime, default=datetime.now)
    cost_time = db.Column(db.Integer, default=0)
    status = db.Column(db.String(16), default='success')
    error_msg = db.Column(db.Text, nullable=True)
    changes = db.Column(db.Text, nullable=True)


# ========== 登录日志 ==========

class LoginLog(db.Model):
    """登录日志"""
    __tablename__ = 'login_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    username = db.Column(db.String(64), nullable=True, index=True)
    login_time = db.Column(db.DateTime, default=datetime.now, index=True)
    logout_time = db.Column(db.DateTime, nullable=True)
    last_active_at = db.Column(db.DateTime, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    browser = db.Column(db.String(64), nullable=True)
    os = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(16), default='success')  # success/failed
    fail_reason = db.Column(db.String(128), nullable=True)
    session_id = db.Column(db.String(128), nullable=True)


# ========== 错误日志 ==========

class ErrorLog(db.Model):
    """错误日志"""
    __tablename__ = 'error_logs'
    id = db.Column(db.Integer, primary_key=True)
    error_time = db.Column(db.DateTime, default=datetime.now, index=True)
    error_type = db.Column(db.String(128), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    stack_trace = db.Column(db.Text, nullable=True)
    request_url = db.Column(db.String(512), nullable=True)
    request_method = db.Column(db.String(16), nullable=True)
    request_params = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(64), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    is_handled = db.Column(db.Boolean, default=False)
    handled_by = db.Column(db.String(64), nullable=True)
    handled_at = db.Column(db.DateTime, nullable=True)
    handle_note = db.Column(db.Text, nullable=True)


# ========== 附件管理 ==========

class Attachment(db.Model):
    """附件统一管理"""
    __tablename__ = 'attachments'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    module = db.Column(db.String(64), nullable=False, index=True)  # contract/stock_in/stock_out等
    biz_id = db.Column(db.Integer, nullable=True, index=True)
    original_name = db.Column(db.String(256), nullable=False)
    file_name = db.Column(db.String(128), nullable=False)  # UUID文件名
    file_path = db.Column(db.String(512), nullable=False)
    file_size = db.Column(db.Integer, default=0)  # 字节
    file_type = db.Column(db.String(32), nullable=True)  # extension
    mime_type = db.Column(db.String(128), nullable=True)
    uploaded_by = db.Column(db.String(64), nullable=True)
    uploaded_by_id = db.Column(db.Integer, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.now)
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

    project = db.relationship('Project')


# ========== 消息通知 ==========

class NotificationLog(db.Model):
    """消息推送记录"""
    __tablename__ = 'notification_log'
    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(32), nullable=False)  # dingtalk/wechat/feishu/email
    title = db.Column(db.String(256), nullable=True)
    content = db.Column(db.Text, nullable=True)
    recipients = db.Column(db.String(512), nullable=True)
    status = db.Column(db.String(16), default='pending')  # pending/success/failed
    error_msg = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Message(db.Model):
    """站内消息（移动端消息中心）"""
    __tablename__ = 'sys_message'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title = db.Column(db.String(128), nullable=False)
    content = db.Column(db.Text, nullable=True)
    msg_type = db.Column(db.String(16), default='info')  # info/approval/stock/notice
    biz_type = db.Column(db.String(32), nullable=True)  # stockin/stockout/contract/payment/requisition
    biz_id = db.Column(db.Integer, nullable=True)
    url = db.Column(db.String(256), nullable=True)
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', backref=db.backref('messages', cascade='all, delete-orphan'))


# ========== 数据变更记录 ==========

class DataChangeLog(db.Model):
    """数据变更明细"""
    __tablename__ = 'data_change_log'
    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(64), nullable=True)
    operation = db.Column(db.String(32), nullable=True)
    table_name = db.Column(db.String(64), nullable=False)
    record_id = db.Column(db.Integer, nullable=False)
    field_name = db.Column(db.String(64), nullable=False)
    field_label = db.Column(db.String(64), nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    changed_by = db.Column(db.String(64), nullable=True)
    changed_by_id = db.Column(db.Integer, nullable=True)
    changed_at = db.Column(db.DateTime, default=datetime.now)
    ip_address = db.Column(db.String(64), nullable=True)
    reason = db.Column(db.String(256), nullable=True)


# ========== 期末结账 ==========

class PeriodClose(db.Model):
    """期末结账记录"""
    __tablename__ = 'period_closes'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    period = db.Column(db.String(7), nullable=False)  # YYYY-MM
    status = db.Column(db.String(16), default='open')  # open / closed
    closed_at = db.Column(db.DateTime, nullable=True)
    closed_by = db.Column(db.String(64), nullable=True)
    reopen_at = db.Column(db.DateTime, nullable=True)
    reopen_by = db.Column(db.String(64), nullable=True)
    reopen_reason = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('project_id', 'period', name='uq_project_period'),
    )


class MovementSnapshot(db.Model):
    """物资动态月结快照"""
    __tablename__ = 'movement_snapshots'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    period = db.Column(db.String(7), nullable=False)  # YYYY-MM
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    begin_qty = db.Column(db.Numeric(18, 4), default=0)
    begin_amount = db.Column(db.Numeric(18, 2), default=0)
    in_qty = db.Column(db.Numeric(18, 4), default=0)
    in_amount = db.Column(db.Numeric(18, 2), default=0)
    out_qty = db.Column(db.Numeric(18, 4), default=0)
    out_amount = db.Column(db.Numeric(18, 2), default=0)
    end_qty = db.Column(db.Numeric(18, 4), default=0)
    end_amount = db.Column(db.Numeric(18, 2), default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)

    material = db.relationship('Material')

    __table_args__ = (
        db.UniqueConstraint('project_id', 'period', 'material_id', name='uq_proj_period_mat'),
    )


# ========== 增强阶段六：批次库存 ==========

class InventoryBatch(db.Model):
    """批次库存（按批次维度管理库存）"""
    __tablename__ = 'inventory_batch'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False, index=True)
    batch_no = db.Column(db.String(64), nullable=False)
    production_date = db.Column(db.Date, nullable=True)
    shelf_life_days = db.Column(db.Integer, nullable=True)
    expire_date = db.Column(db.Date, nullable=True, index=True)
    quantity = db.Column(db.Numeric(18, 4), default=0)
    unit_price = db.Column(db.Numeric(18, 4), default=0)
    stock_in_date = db.Column(db.Date, nullable=True)  # 入库日期（用于先进先出排序）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    material = db.relationship('Material', backref='inventory_batches')

    __table_args__ = (
        db.UniqueConstraint('project_id', 'material_id', 'batch_no', name='uq_proj_mat_batch'),
    )


# ========== 增强阶段六：物资报废 ==========

class MaterialScrap(db.Model):
    """物资报废单"""
    __tablename__ = 'material_scrap'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    code = db.Column(db.String(64), nullable=False)  # 报废单号 BF-xxx
    scrap_date = db.Column(db.Date, nullable=False)
    usage_unit_id = db.Column(db.Integer, db.ForeignKey('usage_units.id'), nullable=True)
    reason = db.Column(db.String(32), nullable=False)  # expired/damaged/unqualified/other
    remark = db.Column(db.Text, nullable=True)
    photo_path = db.Column(db.String(256), nullable=True)  # 照片路径
    applicant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    applicant_name = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(16), default='draft')  # draft/pending/approved/rejected/voided
    approval_status = db.Column(db.String(16), default='draft')  # draft/pending/passed/rejected
    approval_instance_id = db.Column(db.Integer, nullable=True)  # 审批实例ID
    total_quantity = db.Column(db.Numeric(18, 4), default=0)
    total_amount = db.Column(db.Numeric(18, 2), default=0)
    location_lat = db.Column(db.Float, nullable=True)  # 现场定位-纬度
    location_lng = db.Column(db.Float, nullable=True)  # 现场定位-经度
    location_accuracy = db.Column(db.Float, nullable=True)  # 定位精度（米）
    location_time = db.Column(db.DateTime, nullable=True)  # 定位时间
    created_at = db.Column(db.DateTime, default=datetime.now)

    items = db.relationship('MaterialScrapItem', backref='scrap', cascade='all, delete-orphan')
    usage_unit = db.relationship('UsageUnit', backref='scraps')
    applicant = db.relationship('User', backref='scraps')


class MaterialScrapItem(db.Model):
    """报废明细"""
    __tablename__ = 'material_scrap_item'
    id = db.Column(db.Integer, primary_key=True)
    scrap_id = db.Column(db.Integer, db.ForeignKey('material_scrap.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    batch_no = db.Column(db.String(64), nullable=True)
    quantity = db.Column(db.Numeric(18, 4), default=0)
    unit_price = db.Column(db.Numeric(18, 4), default=0)
    amount = db.Column(db.Numeric(18, 2), default=0)
    reason_detail = db.Column(db.String(256), nullable=True)  # 明细报废原因

    material = db.relationship('Material')


# ========== 增强阶段六：商砼小票 ==========

class ConcreteTicket(db.Model):
    """商砼小票登记"""
    __tablename__ = 'concrete_ticket'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    ticket_no = db.Column(db.String(64), nullable=False)  # 小票号（自动生成 CT-{pid}-{YYYYMMDD}-{seq}，可修改）
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    supplier_name = db.Column(db.String(128), nullable=True)
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=True)
    strength_grade = db.Column(db.String(32), nullable=True)  # 强度等级 C30/C40
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=True)  # 关联项目常用物资（商砼类）
    pour_part = db.Column(db.String(128), nullable=True)  # 浇筑部位
    work_number_id = db.Column(db.Integer, db.ForeignKey('work_numbers.id'), nullable=True)  # 关联工号
    vehicle_count = db.Column(db.Integer, default=1)  # 车次
    volume = db.Column(db.Numeric(18, 4), default=0)  # 方量 m³
    arrival_time = db.Column(db.DateTime, nullable=True)  # 到场时间
    vehicle_no = db.Column(db.String(32), nullable=True)  # 运输车号
    driver_name = db.Column(db.String(32), nullable=True)  # 司机姓名
    slump = db.Column(db.String(32), nullable=True)  # 坍落度
    temperature = db.Column(db.String(32), nullable=True)  # 温度
    photo_path = db.Column(db.String(256), nullable=True)  # 随车照片
    is_reconciled = db.Column(db.Boolean, default=False)  # 是否已对账
    is_transferred = db.Column(db.Boolean, default=False)  # 是否已转入库单
    location_lat = db.Column(db.Float, nullable=True)
    location_lng = db.Column(db.Float, nullable=True)
    location_accuracy = db.Column(db.Float, nullable=True)
    location_time = db.Column(db.DateTime, nullable=True)
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    project = db.relationship('Project')
    supplier = db.relationship('Supplier')
    contract = db.relationship('Contract')
    material = db.relationship('Material')
    work_number = db.relationship('WorkNumber')


# ========== 增强阶段六：分包扣款 ==========

class SubcontractDeduction(db.Model):
    """分包领料扣款台账"""
    __tablename__ = 'subcontract_deduction'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    usage_unit_id = db.Column(db.Integer, db.ForeignKey('usage_units.id'), nullable=False)
    period = db.Column(db.String(16), nullable=False)  # YYYY-MM
    total_quantity = db.Column(db.Numeric(18, 4), default=0)
    total_amount = db.Column(db.Numeric(18, 2), default=0)
    status = db.Column(db.String(16), default='draft')  # draft/confirmed/paid
    remark = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    usage_unit = db.relationship('UsageUnit')
    __table_args__ = (
        db.UniqueConstraint('project_id', 'usage_unit_id', 'period', name='uq_proj_unit_period'),
    )


# ========== 增强阶段六：钢材理论重量 ==========

class SteelSpecWeight(db.Model):
    """钢材规格理论重量表"""
    __tablename__ = 'steel_spec_weight'
    id = db.Column(db.Integer, primary_key=True)
    spec_type = db.Column(db.String(32), nullable=False)  # rebar(钢筋)/angle(角钢)/plate(钢板)/pipe(钢管)
    spec_name = db.Column(db.String(64), nullable=False)  # 规格 如 Φ16
    theoretical_weight = db.Column(db.Numeric(18, 4), nullable=False)  # 理论重量 kg/m 或 kg/㎡
    unit = db.Column(db.String(16), default='kg/m')
    remark = db.Column(db.String(128), nullable=True)


# ========== 增强阶段六：条码标签 ==========

class BarcodeLabel(db.Model):
    """条码标签打印记录"""
    __tablename__ = 'barcode_label'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=True)
    material_name = db.Column(db.String(128), nullable=True)
    material_code = db.Column(db.String(64), nullable=True)
    spec = db.Column(db.String(256), nullable=True)
    unit = db.Column(db.String(32), nullable=True)
    label_size = db.Column(db.String(32), default='40x30')  # 40x30 / 60x40
    quantity = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.now)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
