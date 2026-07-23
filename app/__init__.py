import json
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问此页面。'
login_manager.login_message_category = 'warning'


def _add_column_if_missing(table_name, col_name, col_def):
    """幂等添加列"""
    from sqlalchemy import text
    try:
        cols = db.session.execute(text(f"PRAGMA table_info('{table_name}')")).fetchall()
        col_names = [c[1] for c in cols]
        if col_name not in col_names:
            db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}"))
            db.session.commit()
            print(f"Added column: {table_name}.{col_name}")
    except Exception as e:
        print(f"Error adding {table_name}.{col_name}: {e}")
        db.session.rollback()


def _migrate_price_formula_fields():
    """迁移价格方案表字段：discount_type -> float_type, discount_value -> float_value"""
    from sqlalchemy import text
    try:
        cols = db.session.execute(text("PRAGMA table_info('price_formula')")).fetchall()
        col_names = [c[1] for c in cols]

        # 表不存在，跳过
        if not col_names:
            return

        # 添加新字段（如果不存在）
        if 'float_type' not in col_names:
            db.session.execute(text("ALTER TABLE price_formula ADD COLUMN float_type VARCHAR(16) DEFAULT 'none'"))
            db.session.commit()
            print("Added column: price_formula.float_type")

        if 'float_value' not in col_names:
            db.session.execute(text("ALTER TABLE price_formula ADD COLUMN float_value NUMERIC(18,4) DEFAULT 0"))
            db.session.commit()
            print("Added column: price_formula.float_value")

        # 如果有旧字段，迁移数据
        if 'discount_type' in col_names:
            db.session.execute(text("""
                UPDATE price_formula SET float_type = CASE
                    WHEN discount_type = '比例' THEN 'ratio'
                    WHEN discount_type = '金额' THEN 'amount'
                    ELSE 'none'
                END
                WHERE float_type = 'none' OR float_type IS NULL
            """))
            db.session.commit()
            print("Migrated discount_type -> float_type")

        if 'discount_value' in col_names:
            db.session.execute(text("""
                UPDATE price_formula SET float_value = discount_value
                WHERE float_value = 0 OR float_value IS NULL
            """))
            db.session.commit()
            print("Migrated discount_value -> float_value")

    except Exception as e:
        print(f"Error migrating price_formula fields: {e}")
        db.session.rollback()


def init_db_schema():
    """数据库表结构初始化/迁移"""
    _add_column_if_missing('users', 'last_login_at', 'DATETIME')
    _add_column_if_missing('stock_ins', 'is_reconciled', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('stock_outs', 'is_reconciled', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('stock_outs', 'team_id', 'INTEGER')
    _add_column_if_missing('categories', 'parent_id', 'INTEGER DEFAULT 0')
    _add_column_if_missing('categories', 'level', 'INTEGER DEFAULT 1')
    _add_column_if_missing('categories', 'category_code', 'VARCHAR(32)')
    # 审批状态字段（默认 passed 保持已有数据兼容）
    _add_column_if_missing('contracts', 'approval_status', "VARCHAR(16) DEFAULT 'passed'")
    _add_column_if_missing('stock_ins', 'approval_status', "VARCHAR(16) DEFAULT 'passed'")
    _add_column_if_missing('stock_outs', 'approval_status', "VARCHAR(16) DEFAULT 'passed'")
    _add_column_if_missing('reconciliations', 'approval_status', "VARCHAR(16) DEFAULT 'passed'")
    _add_column_if_missing('payments', 'approval_status', "VARCHAR(16) DEFAULT 'passed'")
    # 合同明细累计入库量
    _add_column_if_missing('contract_items', 'total_in_qty', 'NUMERIC(18,4) DEFAULT 0')
    # 入库明细超合同标记
    _add_column_if_missing('stock_in_items', 'is_over_contract', 'BOOLEAN DEFAULT 0')
    # 供应商资质有效期
    _add_column_if_missing('suppliers', 'license_expire_date', 'DATE')
    _add_column_if_missing('suppliers', 'certificate_expire_date', 'DATE')
    _add_column_if_missing('suppliers', 'opening_balance', 'NUMERIC(18,2) DEFAULT 0')
    # 用户数据权限字段
    _add_column_if_missing('users', 'department', 'VARCHAR(64)')
    _add_column_if_missing('users', 'email', 'VARCHAR(128)')
    _add_column_if_missing('users', 'phone', 'VARCHAR(32)')
    _add_column_if_missing('users', 'data_scope', "VARCHAR(16) DEFAULT 'project'")
    _add_column_if_missing('users', 'allowed_projects', 'TEXT')
    _add_column_if_missing('users', 'can_view_amount', 'BOOLEAN DEFAULT 1')
    _add_column_if_missing('users', 'notify_channels', 'TEXT')
    _add_column_if_missing('users', 'per_page', 'INTEGER DEFAULT 10')
    # 项目归档字段
    _add_column_if_missing('projects', 'is_archived', 'BOOLEAN DEFAULT 0')
    # 软删除字段（核心业务表）
    for tbl in ['contracts', 'stock_ins', 'stock_outs', 'suppliers', 'materials', 'payments', 'reconciliations', 'equipment', 'turnover_material']:
        _add_column_if_missing(tbl, 'is_deleted', 'BOOLEAN DEFAULT 0')
        _add_column_if_missing(tbl, 'deleted_at', 'DATETIME')

    # 审批流程分支字段
    _add_column_if_missing('approval_flow', 'has_branch', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('approval_node', 'pass_rule', "VARCHAR(16) DEFAULT 'ANY'")
    _add_column_if_missing('approval_node', 'branch_id', 'INTEGER')
    _add_column_if_missing('approval_instance', 'branch_id', 'INTEGER')
    _add_column_if_missing('approval_branch', 'condition_logic', "VARCHAR(16) DEFAULT 'AND'")
    
    # 审批流程分级字段
    _add_column_if_missing('approval_flow', 'scope', "VARCHAR(16) DEFAULT 'company'")
    _add_column_if_missing('approval_flow', 'project_ids', 'TEXT')
    _add_column_if_missing('approval_flow', 'is_default', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('approval_instance', 'project_id', 'INTEGER')

    # 用户表新增字段
    _add_column_if_missing('users', 'dept_id', 'INTEGER')
    _add_column_if_missing('users', 'role_id', 'INTEGER')

    # 设备表新增字段（设备来源分类 + 租赁 + 状态流转）
    _add_column_if_missing('equipment', 'source_type', "VARCHAR(16) DEFAULT 'self'")
    _add_column_if_missing('equipment', 'supplier_id', 'INTEGER')
    _add_column_if_missing('equipment', 'rent_type', 'VARCHAR(16)')
    _add_column_if_missing('equipment', 'rent_unit_price', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('equipment', 'rent_period', 'INTEGER')
    _add_column_if_missing('equipment', 'entry_date', 'DATE')
    _add_column_if_missing('equipment', 'expected_exit_date', 'DATE')
    _add_column_if_missing('equipment', 'entry_exit_fee', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('equipment', 'deposit', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('equipment', 'labor_team', 'VARCHAR(128)')
    _add_column_if_missing('equipment', 'exit_date', 'DATE')

    # 阶段一：入库业务逻辑调整 + 期初库存
    _add_column_if_missing('stock_in_items', 'price_status', "VARCHAR(16) DEFAULT 'unpriced'")
    _add_column_if_missing('inventory', 'estimated_amount', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('inventory', 'actual_amount', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('stock_ins', 'estimated_amount', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('stock_ins', 'actual_amount', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('stock_ins', 'is_initial', 'BOOLEAN DEFAULT 0')

    # 阶段二：对账浮动价计算
    _add_column_if_missing('reconciliations', 'price_formula_id', 'INTEGER')
    _add_column_if_missing('reconciliations', 'total_amount_without_tax', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('reconciliations', 'total_tax_amount', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('reconciliation_items', 'base_price', 'NUMERIC(18,4) DEFAULT 0')
    _add_column_if_missing('reconciliation_items', 'settlement_price', 'NUMERIC(18,4) DEFAULT 0')
    _add_column_if_missing('reconciliation_items', 'amount_without_tax', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('reconciliation_items', 'tax_amount', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('reconciliation_items', 'amount_with_tax', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('reconciliation_items', 'calc_detail', 'TEXT')

    # 增强阶段一：付款申请
    _add_column_if_missing('payments', 'source_application_id', 'INTEGER')
    _add_column_if_missing('payments', 'amount_without_tax', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('payments', 'tax_amount', 'NUMERIC(18,2) DEFAULT 0')

    # 增强阶段一：入库质检
    _add_column_if_missing('stock_ins', 'quality_status', "VARCHAR(16) DEFAULT 'draft'")
    _add_column_if_missing('stock_ins', 'status', "VARCHAR(16) DEFAULT 'approved'")
    _add_column_if_missing('stock_outs', 'status', "VARCHAR(16) DEFAULT 'approved'")
    _add_column_if_missing('stock_ins', 'quality_checker', 'VARCHAR(64)')
    _add_column_if_missing('stock_ins', 'quality_check_time', 'DATETIME')
    _add_column_if_missing('stock_ins', 'quality_remark', 'TEXT')

    # 增强阶段三：用户登录锁定字段
    _add_column_if_missing('users', 'failed_login_count', 'INTEGER DEFAULT 0')
    _add_column_if_missing('users', 'locked_until', 'DATETIME')
    _add_column_if_missing('users', 'last_login_ip', 'VARCHAR(64)')
    _add_column_if_missing('users', 'project_id', 'INTEGER')

    # 工号/产值相关字段
    _add_column_if_missing('work_numbers', 'parent_id', 'INTEGER')
    _add_column_if_missing('work_numbers', 'remark', 'VARCHAR(512)')

    # 物资报废相关字段
    _add_column_if_missing('material_scrap', 'status', "VARCHAR(16) DEFAULT 'draft'")

    # 增强阶段三：DataChangeLog 新字段
    _add_column_if_missing('data_change_log', 'module', 'VARCHAR(64)')
    _add_column_if_missing('data_change_log', 'operation', 'VARCHAR(32)')
    _add_column_if_missing('data_change_log', 'field_label', 'VARCHAR(64)')
    _add_column_if_missing('data_change_log', 'changed_by_id', 'INTEGER')
    _add_column_if_missing('data_change_log', 'ip_address', 'VARCHAR(64)')
    _add_column_if_missing('data_change_log', 'reason', 'VARCHAR(256)')

    # 增强阶段五：登录日志活跃时间
    _add_column_if_missing('login_logs', 'last_active_at', 'DATETIME')

    # 增强阶段六：批次管理
    _add_column_if_missing('categories', 'batch_management', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('stock_in_items', 'batch_no', 'VARCHAR(64)')
    _add_column_if_missing('stock_in_items', 'production_date', 'DATE')
    _add_column_if_missing('stock_in_items', 'shelf_life_days', 'INTEGER')
    _add_column_if_missing('stock_in_items', 'expire_date', 'DATE')
    _add_column_if_missing('stock_out_items', 'batch_no', 'VARCHAR(64)')
    # 增强阶段六：分包标记
    _add_column_if_missing('usage_units', 'is_subcontractor', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('usage_units', 'subcontract_contract', 'VARCHAR(128)')

    # 架构升级：组织架构树 + 多项目数据权限
    # sys_dept 增加部门类型、项目关联、负责人字段
    _add_column_if_missing('sys_dept', 'dept_type', "VARCHAR(16) DEFAULT 'dept'")
    _add_column_if_missing('sys_dept', 'project_id', 'INTEGER')
    _add_column_if_missing('sys_dept', 'leader', 'VARCHAR(64)')
    # sys_user_project 关联表由 db.create_all() 自动创建
    # 项目表增加模块配置字段
    _add_column_if_missing('projects', 'module_config', 'TEXT')
    # 项目表增加状态和扩展字段
    _add_column_if_missing('projects', 'status', "VARCHAR(16) DEFAULT 'active'")
    _add_column_if_missing('projects', 'actual_end_date', 'DATE')
    _add_column_if_missing('projects', 'building_area', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('projects', 'contract_amount', 'NUMERIC(18,2) DEFAULT 0')
    _add_column_if_missing('projects', 'project_type', 'VARCHAR(32)')
    # 业务表增加 dept_id 字段（用于数据权限过滤）
    for tbl in ['stock_ins', 'stock_outs', 'contracts', 'reconciliations',
                'payment_applications', 'payments', 'stock_check', 'purchase_requisition',
                'material_transfer', 'material_scrap', 'concrete_ticket',
                'equipment', 'turnover_material']:
        _add_column_if_missing(tbl, 'dept_id', 'INTEGER')

    # 主数据统一改造：主库表增加 source/status/create_dept 字段
    _add_column_if_missing('materials', 'source', "VARCHAR(16) DEFAULT 'project'")
    _add_column_if_missing('materials', 'status', "VARCHAR(16) DEFAULT 'active'")
    _add_column_if_missing('materials', 'create_dept', 'INTEGER')
    _add_column_if_missing('suppliers', 'source', "VARCHAR(16) DEFAULT 'project'")
    _add_column_if_missing('suppliers', 'status', "VARCHAR(16) DEFAULT 'qualified'")
    _add_column_if_missing('suppliers', 'create_dept', 'INTEGER')
    _add_column_if_missing('categories', 'source', "VARCHAR(16) DEFAULT 'project')")

    # 移动端增量：现场定位留痕 + 商砼字段扩展 + 报废审批实例关联
    for tbl in ['stock_ins', 'stock_outs', 'concrete_ticket', 'material_scrap', 'equipment']:
        _add_column_if_missing(tbl, 'location_lat', 'FLOAT')
        _add_column_if_missing(tbl, 'location_lng', 'FLOAT')
        _add_column_if_missing(tbl, 'location_accuracy', 'FLOAT')
        _add_column_if_missing(tbl, 'location_time', 'DATETIME')
    # 商砼小票完整字段迁移（兼容旧数据库）
    _add_column_if_missing('concrete_ticket', 'contract_id', 'INTEGER')
    _add_column_if_missing('concrete_ticket', 'strength_grade', 'VARCHAR(32)')
    _add_column_if_missing('concrete_ticket', 'material_id', 'INTEGER')
    _add_column_if_missing('concrete_ticket', 'pour_part', 'VARCHAR(128)')
    _add_column_if_missing('concrete_ticket', 'work_number_id', 'INTEGER')
    _add_column_if_missing('concrete_ticket', 'vehicle_count', 'INTEGER DEFAULT 1')
    _add_column_if_missing('concrete_ticket', 'volume', 'NUMERIC(18,4) DEFAULT 0')
    _add_column_if_missing('concrete_ticket', 'arrival_time', 'DATETIME')
    _add_column_if_missing('concrete_ticket', 'vehicle_no', 'VARCHAR(32)')
    _add_column_if_missing('concrete_ticket', 'driver_name', 'VARCHAR(32)')
    _add_column_if_missing('concrete_ticket', 'slump', 'VARCHAR(32)')
    _add_column_if_missing('concrete_ticket', 'temperature', 'VARCHAR(32)')
    _add_column_if_missing('concrete_ticket', 'photo_path', 'VARCHAR(256)')
    _add_column_if_missing('concrete_ticket', 'is_reconciled', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('concrete_ticket', 'is_transferred', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('concrete_ticket', 'remark', 'TEXT')
    _add_column_if_missing('concrete_ticket', 'created_at', 'DATETIME')
    _add_column_if_missing('material_scrap', 'approval_instance_id', 'INTEGER')

    # 公告表新增可见范围字段
    _add_column_if_missing('sys_announcement', 'visible_scope', "VARCHAR(16) DEFAULT 'all'")
    _add_column_if_missing('sys_announcement', 'visible_roles', 'TEXT')
    _add_column_if_missing('sys_announcement', 'visible_depts', 'TEXT')

    # 价格方案字段迁移（discount_type -> float_type, discount_value -> float_value）
    _migrate_price_formula_fields()

    db.create_all()
    print("Database tables created/updated")


def init_rbac_data():
    """初始化RBAC权限数据"""
    from app.models import SysDept, SysRole, SysMenu, SysRoleMenu, User, Project
    import json as _json
    import os as _os

    # 从menu_config.json同步菜单（每次启动都同步名称、图标、排序等属性）
    from flask import current_app
    config_path = _os.path.join(current_app.root_path, 'static', 'config', 'menu_config.json')
    if _os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            menu_data = _json.load(f)
        groups = menu_data.get('groups', [])

        # 检测是否有旧格式数据（目录没有menu_code），有则清空重建
        old_catalogs = SysMenu.query.filter_by(menu_type='catalog', parent_id=0).filter(
            (SysMenu.menu_code.is_(None)) | (SysMenu.menu_code == '')
        ).all()
        if old_catalogs:
            print(f"发现旧格式菜单数据({len(old_catalogs)}个目录)，清空重建...")
            SysRoleMenu.query.delete()
            SysMenu.query.delete()
            db.session.commit()

        catalog_sort = 1
        for group in groups:
            group_code = group.get('id', '')
            # 目录级：用id作为menu_code匹配
            catalog = SysMenu.query.filter_by(menu_code=group_code, menu_type='catalog').first()
            catalog_remark = _json.dumps({
                'require_admin': group.get('require_admin', False),
                'divider_before': group.get('divider_before', False),
            }, ensure_ascii=False)
            if not catalog:
                catalog = SysMenu(
                    menu_name=group.get('title', ''),
                    menu_code=group_code,
                    menu_type='catalog',
                    icon=group.get('icon', ''),
                    sort=catalog_sort,
                    parent_id=0,
                    status=True,
                    permission=group.get('module', '') or None,
                    remark=catalog_remark
                )
                db.session.add(catalog)
                db.session.flush()
            else:
                # 已存在则只同步配置类属性（名称以用户在菜单管理页面的修改为准）
                changed = False
                if (not catalog.icon) and group.get('icon', ''):
                    catalog.icon = group.get('icon', '')
                    changed = True
                if catalog.sort != catalog_sort:
                    catalog.sort = catalog_sort
                    changed = True
                group_module = group.get('module', '')
                if (not catalog.permission) and group_module:
                    catalog.permission = group_module or None
                    changed = True
                if (not catalog.remark) and catalog_remark:
                    catalog.remark = catalog_remark
                    changed = True
                if changed:
                    db.session.flush()
            catalog_sort += 1
            # 菜单项：用endpoint作为menu_code匹配
            for item_sort, item in enumerate(group.get('items', []), 1):
                item_code = item.get('endpoint', '')
                if not item_code:
                    continue
                menu = SysMenu.query.filter_by(menu_code=item_code, menu_type='menu').first()
                item_remark = _json.dumps({
                    'section': item.get('section', ''),
                    'active': item.get('active', ''),
                }, ensure_ascii=False)
                if not menu:
                    menu = SysMenu(
                        menu_name=item.get('title', ''),
                        menu_code=item_code,
                        menu_type='menu',
                        icon=item.get('icon', ''),
                        path=item_code,
                        sort=item_sort,
                        parent_id=catalog.id,
                        status=True,
                        permission=item.get('module', '') or None,
                        remark=item_remark
                    )
                    db.session.add(menu)
                    db.session.flush()
                else:
                    # 已存在则只同步配置类属性（名称以用户在菜单管理页面的修改为准）
                    changed = False
                    if (not menu.icon) and item.get('icon', ''):
                        menu.icon = item.get('icon', '')
                        changed = True
                    if menu.sort != item_sort:
                        menu.sort = item_sort
                        changed = True
                    if menu.parent_id != catalog.id:
                        menu.parent_id = catalog.id
                        changed = True
                    item_module = item.get('module', '')
                    if (not menu.permission) and item_module:
                        menu.permission = item_module or None
                        changed = True
                    if (not menu.remark) and item_remark:
                        menu.remark = item_remark
                        changed = True
                    if (not menu.path) and item_code:
                        menu.path = item_code
                        changed = True
                    if changed:
                        db.session.flush()
        db.session.commit()

    # 初始化部门（升级为树形组织架构）
    depts_data = [
        {'code': 'HQ', 'name': '总公司', 'parent_id': 0, 'dept_type': 'company', 'sort': 1},
        {'code': 'MATERIAL', 'name': '物资部', 'parent_id': 1, 'dept_type': 'dept', 'sort': 1},
        {'code': 'FINANCE', 'name': '财务部', 'parent_id': 1, 'dept_type': 'dept', 'sort': 2},
        {'code': 'ENGINEERING', 'name': '工程部', 'parent_id': 1, 'dept_type': 'dept', 'sort': 3},
    ]

    for i, data in enumerate(depts_data):
        if not SysDept.query.filter_by(dept_code=data['code']).first():
            dept = SysDept(
                dept_code=data['code'],
                dept_name=data['name'],
                parent_id=data['parent_id'],
                dept_type=data.get('dept_type', 'dept'),
                sort=data['sort']
            )
            db.session.add(dept)
            db.session.flush()
            data['id'] = dept.id
            depts_data[i] = data

    # 为现有项目创建对应的项目部部门
    for project in Project.query.all():
        dept_code = f'PRJ_{project.id}'
        if not SysDept.query.filter_by(dept_code=dept_code).first():
            project_dept = SysDept(
                dept_code=dept_code,
                dept_name=project.name,
                parent_id=1,  # 挂在总公司下
                dept_type='project',
                project_id=project.id,
                sort=10 + project.id
            )
            db.session.add(project_dept)
            db.session.flush()

    # 初始化角色（升级数据权限范围）
    roles_data = [
        {'code': 'super_admin', 'name': '超级管理员', 'data_scope': 'all', 'sort': 1},
        {'code': 'material_admin', 'name': '公司物资部长', 'data_scope': 'all', 'sort': 2},
        {'code': 'project_manager', 'name': '项目经理', 'data_scope': 'dept_and_sub', 'sort': 3},
        {'code': 'project_admin', 'name': '项目管理员', 'data_scope': 'dept_and_sub', 'sort': 4},
        {'code': 'material_staff', 'name': '项目物资员', 'data_scope': 'dept', 'sort': 5},
        {'code': 'finance_user', 'name': '财务人员', 'data_scope': 'dept', 'sort': 6},
        {'code': 'viewer', 'name': '普通查看员', 'data_scope': 'dept', 'sort': 7},
    ]

    for i, data in enumerate(roles_data):
        if not SysRole.query.filter_by(role_code=data['code']).first():
            role = SysRole(
                role_code=data['code'],
                role_name=data['name'],
                data_scope=data['data_scope'],
                sort=data['sort']
            )
            db.session.add(role)
            db.session.flush()
            data['id'] = role.id
            roles_data[i] = data
    
    
    # 超级管理员拥有所有权限
    super_admin_role = SysRole.query.filter_by(role_code='super_admin').first()
    if super_admin_role:
        existing_perms = set([rm.menu_id for rm in SysRoleMenu.query.filter_by(role_id=super_admin_role.id).all()])
        all_menu_ids = [m.id for m in SysMenu.query.all()]
        for menu_id in all_menu_ids:
            if menu_id not in existing_perms:
                rp = SysRoleMenu(role_id=super_admin_role.id, menu_id=menu_id)
                db.session.add(rp)

    # 项目管理员角色权限初始化
    project_admin_role = SysRole.query.filter_by(role_code='project_admin').first()
    if project_admin_role:
        existing_perms = set([rm.menu_id for rm in SysRoleMenu.query.filter_by(role_id=project_admin_role.id).all()])
        if not existing_perms:
            # 项目管理员可访问的菜单 code 列表
            project_admin_menu_codes = [
                # 工作台
                'main.index',
                'approval.my_approvals',
                # 基础数据
                'project.index',
                'supplier.index',
                'category.index',
                'material.index',
                'unit.index',
                'work_number.index',
                # 采购合同
                'purchase_requisition.index',
                'contract.index',
                'contract.invoices',
                'contract.payments',
                'payment_application.index',
                'reconciliation.index',
                'price_formula.index',
                # 库存管理
                'stock_in.index',
                'stock_out.index',
                'inventory.index',
                'stock_check.index',
                'material_transfer.index',
                'batch.index',
                'batch.expiry_alerts',
                'scrap.index',
                'period_close.index',
                # 周转材管理
                'turnover_material.index',
                'turnover_material.record_index',
                'turnover_material.rental_bill',
                # 设备管理
                'equipment.index',
                'equipment.maintenance',
                'equipment.depreciation',
                # 行业工具
                'concrete.index',
                'concrete.reconcile',
                'steel.index',
                'steel.specs',
                'barcode.index',
                'barcode.batch_print',
                # 统计报表
                'report.stock_in_report',
                'report.stock_out_report',
                'report.material_movement',
                'report.receive_issue',
                'report.ledger',
                'report.work_number_cost',
                'report.supplier_ledger',
                'subcontract.index',
                'concrete.stats',
                'report.advanced',
                # 系统管理（仅只读/项目级配置）
                'dict_mgr.index',
                'approval.flows',
                'batch.category_config',
            ]
            for code in project_admin_menu_codes:
                menu = SysMenu.query.filter_by(menu_code=code).first()
                if menu:
                    rp = SysRoleMenu(role_id=project_admin_role.id, menu_id=menu.id)
                    db.session.add(rp)
    
    # 更新默认admin用户关联角色和部门
    admin_user = User.query.filter_by(username='admin').first()
    if admin_user:
        super_admin_role = SysRole.query.filter_by(role_code='super_admin').first()
        hq_dept = SysDept.query.filter_by(dept_code='HQ').first()
        if super_admin_role:
            admin_user.role_id = super_admin_role.id
        if hq_dept:
            admin_user.dept_id = hq_dept.id

    # 为所有现有用户创建用户项目关联（兼容迁移）
    from app.models import SysUserProject
    for user in User.query.all():
        # 跳过已有项目关联的用户
        if user.user_projects:
            continue
        # 取第一个项目作为主项目
        first_project = Project.query.first()
        if first_project:
            up = SysUserProject(
                user_id=user.id,
                project_id=first_project.id,
                is_main=True
            )
            db.session.add(up)

    db.session.commit()
    print("RBAC data initialized")


def init_steel_specs():
    """初始化钢材规格理论重量数据"""
    from app.models import SteelSpecWeight
    if SteelSpecWeight.query.count() > 0:
        return
    specs = [
        # 钢筋 HRB400
        ('rebar', 'Φ6', 0.222, 'kg/m', 'HRB400'),
        ('rebar', 'Φ8', 0.395, 'kg/m', 'HRB400'),
        ('rebar', 'Φ10', 0.617, 'kg/m', 'HRB400'),
        ('rebar', 'Φ12', 0.888, 'kg/m', 'HRB400'),
        ('rebar', 'Φ14', 1.208, 'kg/m', 'HRB400'),
        ('rebar', 'Φ16', 1.578, 'kg/m', 'HRB400'),
        ('rebar', 'Φ18', 1.998, 'kg/m', 'HRB400'),
        ('rebar', 'Φ20', 2.466, 'kg/m', 'HRB400'),
        ('rebar', 'Φ22', 2.984, 'kg/m', 'HRB400'),
        ('rebar', 'Φ25', 3.853, 'kg/m', 'HRB400'),
        ('rebar', 'Φ28', 4.834, 'kg/m', 'HRB400'),
        ('rebar', 'Φ32', 6.313, 'kg/m', 'HRB400'),
        # 钢板
        ('plate', '3mm', 23.55, 'kg/㎡', '钢板3mm'),
        ('plate', '4mm', 31.40, 'kg/㎡', '钢板4mm'),
        ('plate', '5mm', 39.25, 'kg/㎡', '钢板5mm'),
        ('plate', '6mm', 47.10, 'kg/㎡', '钢板6mm'),
        ('plate', '8mm', 62.80, 'kg/㎡', '钢板8mm'),
        ('plate', '10mm', 78.50, 'kg/㎡', '钢板10mm'),
        ('plate', '12mm', 94.20, 'kg/㎡', '钢板12mm'),
        # 角钢
        ('angle', 'L25×3', 1.124, 'kg/m', '等边角钢'),
        ('angle', 'L30×3', 1.373, 'kg/m', '等边角钢'),
        ('angle', 'L40×3', 1.852, 'kg/m', '等边角钢'),
        ('angle', 'L40×4', 2.422, 'kg/m', '等边角钢'),
        ('angle', 'L50×5', 3.770, 'kg/m', '等边角钢'),
        ('angle', 'L63×6', 5.721, 'kg/m', '等边角钢'),
        # 钢管
        ('pipe', 'Φ48×3.0', 3.35, 'kg/m', '脚手架管'),
        ('pipe', 'Φ48×3.5', 3.84, 'kg/m', '脚手架管'),
        ('pipe', 'Φ57×3.5', 4.62, 'kg/m', '焊接钢管'),
        ('pipe', 'Φ76×4.0', 7.10, 'kg/m', '焊接钢管'),
        ('pipe', 'Φ89×4.0', 8.38, 'kg/m', '焊接钢管'),
        ('pipe', 'Φ114×4.0', 10.85, 'kg/m', '焊接钢管'),
    ]
    for spec_type, spec_name, weight, unit, remark in specs:
        s = SteelSpecWeight(spec_type=spec_type, spec_name=spec_name,
                           theoretical_weight=weight, unit=unit, remark=remark)
        db.session.add(s)
    db.session.commit()
    print("Steel specs initialized")


def init_default_users():
    """初始化默认管理员账号（首次启动时）"""
    from app.models import User
    if User.query.count() > 0:
        return

    from werkzeug.security import generate_password_hash
    from flask import current_app

    default_pwd = current_app.config.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')
    pwd_hash = generate_password_hash(default_pwd, method='pbkdf2:sha256')

    users = [
        User(username='admin', password_hash=pwd_hash, role='admin',
             name='系统管理员', can_view_amount=True, per_page=10,
             data_scope='project'),
        User(username='editor', password_hash=pwd_hash, role='editor',
             name='数据录入员', can_view_amount=True, per_page=10,
             data_scope='project'),
        User(username='viewer', password_hash=pwd_hash, role='viewer',
             name='查看员', can_view_amount=False, per_page=10,
             data_scope='project'),
    ]
    for u in users:
        db.session.add(u)
    db.session.commit()
    print("Default users initialized (admin/editor/viewer)")


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # 配置业务日志(全链路日志,用于偶现问题定位)
    import logging
    biz_handler = logging.FileHandler('logs/business.log', encoding='utf-8')
    biz_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
    ))
    biz_logger = logging.getLogger('business')
    biz_logger.setLevel(logging.INFO)
    biz_logger.addHandler(biz_handler)

    db.init_app(app)
    login_manager.init_app(app)

    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    from app.project import bp as project_bp
    app.register_blueprint(project_bp, url_prefix='/project')

    from app.supplier import bp as supplier_bp
    app.register_blueprint(supplier_bp, url_prefix='/supplier')

    from app.category import bp as category_bp
    app.register_blueprint(category_bp, url_prefix='/category')

    from app.material import bp as material_bp
    app.register_blueprint(material_bp, url_prefix='/material')

    from app.master import master_bp
    app.register_blueprint(master_bp)

    from app.unit import bp as unit_bp
    app.register_blueprint(unit_bp, url_prefix='/unit')

    from app.work_number import bp as work_number_bp
    app.register_blueprint(work_number_bp, url_prefix='/work_number')

    from app.contract import bp as contract_bp
    app.register_blueprint(contract_bp, url_prefix='/contract')

    from app.stock_in import bp as stock_in_bp
    app.register_blueprint(stock_in_bp, url_prefix='/stock_in')

    from app.stock_out import bp as stock_out_bp
    app.register_blueprint(stock_out_bp, url_prefix='/stock_out')

    from app.inventory import bp as inventory_bp
    app.register_blueprint(inventory_bp, url_prefix='/inventory')

    from app.stock_check import bp as stock_check_bp
    app.register_blueprint(stock_check_bp, url_prefix='/stock_check')

    from app.purchase_requisition import bp as purchase_requisition_bp
    app.register_blueprint(purchase_requisition_bp, url_prefix='/purchase_requisition')

    from app.material_transfer import bp as material_transfer_bp
    app.register_blueprint(material_transfer_bp, url_prefix='/material_transfer')

    from app.turnover_material import bp as turnover_material_bp
    app.register_blueprint(turnover_material_bp, url_prefix='/turnover_material')

    from app.equipment import bp as equipment_bp
    app.register_blueprint(equipment_bp, url_prefix='/equipment')

    from app.reconciliation import bp as reconciliation_bp
    app.register_blueprint(reconciliation_bp, url_prefix='/reconciliation')

    from app.price_formula import bp as price_formula_bp
    app.register_blueprint(price_formula_bp, url_prefix='/price_formula')

    from app.payment_application import bp as payment_application_bp
    app.register_blueprint(payment_application_bp, url_prefix='/payment_application')

    from app.period_close import bp as period_close_bp
    app.register_blueprint(period_close_bp, url_prefix='/period_close')

    from app.report import bp as report_bp
    app.register_blueprint(report_bp, url_prefix='/report')

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    from app.help import bp as help_bp
    app.register_blueprint(help_bp, url_prefix='/help')

    from app.dict_mgr import bp as dict_bp
    app.register_blueprint(dict_bp, url_prefix='/dict_mgr')

    from app.approval import bp as approval_bp
    app.register_blueprint(approval_bp, url_prefix='/approval')

    from app.profile import bp as profile_bp
    app.register_blueprint(profile_bp, url_prefix='/profile')

    from app.system import bp as system_bp
    app.register_blueprint(system_bp, url_prefix='/system')

    from app.ai import bp as ai_bp
    app.register_blueprint(ai_bp, url_prefix='/ai')

    from app.steel import bp as steel_bp
    app.register_blueprint(steel_bp, url_prefix='/steel')

    from app.scrap import bp as scrap_bp
    app.register_blueprint(scrap_bp, url_prefix='/scrap')

    from app.concrete import bp as concrete_bp
    app.register_blueprint(concrete_bp, url_prefix='/concrete')

    from app.batch import bp as batch_bp
    app.register_blueprint(batch_bp, url_prefix='/batch')

    from app.subcontract import bp as subcontract_bp
    app.register_blueprint(subcontract_bp, url_prefix='/subcontract')

    from app.barcode import bp as barcode_bp
    app.register_blueprint(barcode_bp, url_prefix='/barcode')

    from app.mobile import bp as mobile_bp
    app.register_blueprint(mobile_bp, url_prefix='/mobile')

    from app.org_sync import bp as org_sync_bp
    app.register_blueprint(org_sync_bp, url_prefix='/org_sync')

    @app.context_processor
    def inject_projects():
        from flask_login import current_user
        from flask import session
        from app.models import Project
        projects = []
        switcher_projects = []
        can_view_all_projects = False
        approval_pending_count = 0
        current_project_modules = {}
        is_all_projects_mode = False
        main_project_id = None
        can_switch_project = True
        if current_user.is_authenticated:
            # 按用户数据权限过滤可见项目
            can_view_all_projects = (current_user.get_data_scope() == 'all' or current_user.is_admin())
            if can_view_all_projects:
                projects = current_user.get_visible_projects()
            else:
                projects = current_user.get_visible_projects()
            # 顶部切换器只显示在建且未归档的项目
            switcher_projects = [p for p in projects if p.status == 'active' and not p.is_archived]
            # 主项目ID
            try:
                main_project_obj = current_user.get_main_project()
                if main_project_obj:
                    main_project_id = main_project_obj.id
            except Exception:
                pass
            # 项目级账号只显示当前项目名称，不可切换
            if not can_view_all_projects and len(switcher_projects) <= 1:
                can_switch_project = False
            # 判断是否为全部项目模式
            is_all_projects_mode = (session.get('current_project_id') is None)
            try:
                from app.approval.service import get_pending_count
                approval_pending_count = get_pending_count(current_user.id)
            except Exception:
                pass
            # 注入当前项目模块配置
            project_id = session.get('current_project_id')
            if project_id:
                project = Project.query.get(project_id)
                if project:
                    current_project_modules = project.get_module_config()
        return dict(all_projects=projects,
                    switcher_projects=switcher_projects,
                    can_view_all_projects=can_view_all_projects,
                    can_switch_project=can_switch_project,
                    is_all_projects_mode=is_all_projects_mode,
                    main_project_id=main_project_id,
                    current_project_id=session.get('current_project_id'),
                    current_project_name=session.get('current_project_name'),
                    current_project_modules=current_project_modules,
                    approval_pending_count=approval_pending_count)

    # 注册模板全局函数: has_perm 用于按钮级权限校验, is_module_enabled 用于模块开关判断
    @app.context_processor
    def inject_permission_helper():
        from flask_login import current_user
        from flask import session
        from app.models import Project
        def has_perm(permission):
            if not current_user.is_authenticated:
                return False
            try:
                return current_user.has_permission(permission)
            except Exception:
                return False
        def is_module_enabled(module_key):
            if not current_user.is_authenticated:
                return False
            try:
                project_id = session.get('current_project_id')
                if not project_id:
                    return True
                project = Project.query.get(project_id)
                if not project:
                    return True
                return project.is_module_enabled(module_key)
            except Exception:
                return True
        return dict(has_perm=has_perm, is_module_enabled=is_module_enabled)

    # 注册模板全局函数
    from app.utils import num_to_chinese, get_dict_items, get_config, format_file_size, is_module_enabled, can_edit_in_current_mode
    app.jinja_env.globals['num_to_chinese'] = num_to_chinese
    app.jinja_env.globals['get_dict_items'] = get_dict_items
    app.jinja_env.globals['get_config'] = get_config
    app.jinja_env.globals['format_file_size'] = format_file_size
    app.jinja_env.globals['is_module_enabled'] = is_module_enabled
    app.jinja_env.globals['can_edit_in_current_mode'] = can_edit_in_current_mode

    # 注册模板过滤器
    def fmt_number(value, decimals=2):
        """数字千分位格式化"""
        if value is None or value == '':
            return ''
        try:
            v = float(value)
            return f"{v:,.{decimals}f}"
        except (ValueError, TypeError):
            return str(value)

    def fmt_amount(value):
        """金额格式化：千分位+2位小数"""
        return fmt_number(value, 2)

    app.jinja_env.filters['fmt_number'] = fmt_number
    app.jinja_env.filters['fmt_amount'] = fmt_amount
    app.jinja_env.globals['fmt_number'] = fmt_number
    app.jinja_env.globals['fmt_amount'] = fmt_amount

    # 加载菜单配置
    menu_config_path = os.path.join(app.root_path, 'static', 'config', 'menu_config.json')
    with open(menu_config_path, 'r', encoding='utf-8') as f:
        _menu_config = json.load(f)

    def _is_item_module_enabled(item, project_module_config):
        """检查菜单项的模块是否启用"""
        module_key = item.get('module')
        if not module_key:
            return True
        if module_key in project_module_config:
            return project_module_config[module_key]
        enabled = get_config(module_key, 'true')
        return str(enabled).lower() == 'true'

    def get_menu_groups():
        """根据当前用户权限、项目模块开关和菜单状态返回可见菜单分组"""
        from flask_login import current_user
        from app.models import SysMenu, SysRoleMenu, Project
        from flask import session
        import json as _json
        # 获取当前项目的模块配置
        project_module_config = {}
        allowed_endpoints = set()
        try:
            # 非管理员需按角色权限过滤菜单
            if current_user.is_authenticated and not current_user.is_admin():
                role_menus = db.session.query(SysMenu.menu_code).join(
                    SysRoleMenu, SysRoleMenu.menu_id == SysMenu.id
                ).filter(
                    SysRoleMenu.role_id == current_user.role_id,
                    SysMenu.menu_code.isnot(None)
                ).all()
                allowed_endpoints = {row[0] for row in role_menus if row[0]}
            # 获取当前项目模块配置
            project_id = session.get('current_project_id')
            if project_id:
                project = Project.query.get(project_id)
                if project:
                    project_module_config = project.get_module_config()
        except Exception:
            pass

        result = []
        try:
            # 从数据库读取所有启用的菜单
            catalogs = SysMenu.query.filter_by(
                parent_id=0, menu_type='catalog', status=True
            ).order_by(SysMenu.sort).all()

            for catalog in catalogs:
                # 解析目录扩展属性
                catalog_extra = {}
                if catalog.remark:
                    try:
                        catalog_extra = _json.loads(catalog.remark)
                    except Exception:
                        pass
                module_key = catalog.permission if catalog.permission else None

                # 项目模块开关过滤（分组级别）
                if module_key:
                    if module_key in project_module_config:
                        if not project_module_config[module_key]:
                            continue
                    else:
                        enabled = get_config(module_key, 'true')
                        if str(enabled).lower() != 'true':
                            continue

                # 管理员权限过滤
                if catalog_extra.get('require_admin') and (not current_user.is_authenticated or not current_user.is_admin()):
                    continue

                # 查询该分组下的菜单项
                items_db = SysMenu.query.filter_by(
                    parent_id=catalog.id, menu_type='menu', status=True
                ).order_by(SysMenu.sort).all()

                items = []
                for menu in items_db:
                    endpoint = menu.menu_code
                    # 模块开关过滤（菜单项级别）
                    if menu.permission and menu.permission.startswith('module_'):
                        item_module = menu.permission
                        if item_module in project_module_config:
                            if not project_module_config[item_module]:
                                continue
                        else:
                            enabled = get_config(item_module, 'true')
                            if str(enabled).lower() != 'true':
                                continue
                    # 非管理员权限过滤
                    if current_user.is_authenticated and not current_user.is_admin():
                        if endpoint and allowed_endpoints and endpoint not in allowed_endpoints:
                            continue
                    # 解析菜单项扩展属性
                    item_extra = {}
                    if menu.remark:
                        try:
                            item_extra = _json.loads(menu.remark)
                        except Exception:
                            pass
                    items.append({
                        'title': menu.menu_name,
                        'endpoint': endpoint,
                        'active': item_extra.get('active', endpoint or ''),
                        'section': item_extra.get('section', ''),
                        'icon': menu.icon or '',
                        'module': menu.permission if menu.permission and menu.permission.startswith('module_') else None,
                    })

                if not items:
                    continue
                result.append({
                    'id': catalog.menu_code or f'group_{catalog.id}',
                    'title': catalog.menu_name,
                    'icon': catalog.icon or '',
                    'module': module_key,
                    'require_admin': catalog_extra.get('require_admin', False),
                    'divider_before': catalog_extra.get('divider_before', False),
                    'items': items,
                })
        except Exception:
            # 数据库读取失败时回退到JSON配置
            try:
                for group in _menu_config.get('groups', []):
                    module_key = group.get('module')
                    if module_key:
                        if module_key in project_module_config:
                            if not project_module_config[module_key]:
                                continue
                        else:
                            enabled = get_config(module_key, 'true')
                            if str(enabled).lower() != 'true':
                                continue
                    if group.get('require_admin') and (not current_user.is_authenticated or not current_user.is_admin()):
                        continue
                    filtered_items = []
                    for item in group.get('items', []):
                        endpoint = item.get('endpoint')
                        if current_user.is_authenticated and not current_user.is_admin():
                            if endpoint and allowed_endpoints and endpoint not in allowed_endpoints:
                                continue
                        if not _is_item_module_enabled(item, project_module_config):
                            continue
                        filtered_items.append(item)
                    if filtered_items:
                        result.append({**group, 'items': filtered_items})
            except Exception:
                pass
        return result

    app.jinja_env.globals['get_menu_groups'] = get_menu_groups
    app.jinja_env.globals['_menu_config'] = _menu_config

    def _is_system_page():
        """判断当前页面是否是系统管理页面"""
        from flask import request
        ep = request.endpoint or ''
        sys_prefixes = [
            'admin.', 'system.', 'dict_mgr.', 'org_sync.',
            'ai.config', 'ai.logs', 'approval.flows',
            'batch.category_config', 'period_close.'
        ]
        for prefix in sys_prefixes:
            if ep.startswith(prefix):
                return True
        return False

    app.jinja_env.globals['is_system_page'] = _is_system_page
    app.jinja_env.globals['get_sys_menu_groups'] = lambda: __import__('app.system.rbac_routes', fromlist=['get_sys_menu_data']).get_sys_menu_data()
    app.jinja_env.globals['get_sys_menu_title'] = lambda ep: __import__('app.system.rbac_routes', fromlist=['get_sys_menu_title']).get_sys_menu_title(ep)

    # 数据库迁移与初始化
    with app.app_context():
        init_db_schema()
        # 自动执行数据库迁移
        from app.migration import run_migrations
        run_migrations()
        from app.utils import init_system_config, init_dict_data
        init_system_config()
        init_dict_data()
        from app.approval.service import init_default_flows
        init_default_flows()
        init_rbac_data()
        init_steel_specs()
        # 主数据统一改造：建立项目常用关联
        from app.utils import init_master_data_unification
        init_master_data_unification()
        # 初始化默认管理员账号
        init_default_users()

    # 拦截禁用菜单的访问 & 模块开关拦截 & 更新用户活跃时间 & 初始化向导
    @app.before_request
    def check_menu_status():
        """检查请求的端点是否对应已禁用的菜单或已关闭的模块，并更新用户最后活跃时间"""
        from flask import request, abort, session, redirect, url_for, flash
        from flask_login import current_user
        from app.models import SysMenu, LoginLog, Project
        from app import db as _db
        from datetime import datetime, timedelta

        # 移动端 UA 自动跳转：手机访问主域名自动跳转到 /mobile
        # 仅在根路径且未在移动端路由时触发
        endpoint = request.endpoint
        if endpoint in (None, 'main.index', 'static') and \
                request.path in ('/', '/index') and \
                not request.path.startswith('/mobile'):
            ua = (request.headers.get('User-Agent') or '').lower()
            mobile_keywords = ('mobile', 'android', 'iphone', 'ipad', 'ipod',
                               'windows phone', 'blackberry', 'opera mini')
            if any(kw in ua for kw in mobile_keywords):
                # 用户已登录则进移动端首页，否则进移动端登录页
                if current_user.is_authenticated:
                    return redirect(url_for('mobile.portal_home'))
                return redirect(url_for('mobile.login'))

        # 检查是否需要初始化向导
        if endpoint:
            # 向导相关页面和静态资源跳过
            allowed_for_init = ('main.wizard', 'main.wizard_submit', 'main.wizard_done', 'static')
            is_wizard = endpoint.startswith(('main.wizard',)) or endpoint == 'static'
            if is_wizard or endpoint in allowed_for_init:
                pass
            else:
                try:
                    from app.models import User
                    if User.query.count() == 0:
                        return redirect(url_for('main.wizard'))
                except Exception:
                    pass

        # 更新用户最后活跃时间（60秒更新一次，减轻数据库压力）
        if current_user.is_authenticated:
            last_update = session.get('_last_active_update')
            now = datetime.now()
            if not last_update or (now - datetime.fromisoformat(last_update)).total_seconds() > 60:
                session['_last_active_update'] = now.isoformat()
                # 更新LoginLog中最新的成功登录记录
                try:
                    latest_login = LoginLog.query.filter_by(
                        user_id=current_user.id, status='success'
                    ).order_by(LoginLog.login_time.desc()).first()
                    if latest_login and not latest_login.logout_time:
                        latest_login.last_active_at = now
                        _db.session.commit()
                except Exception:
                    _db.session.rollback()

            # 强制修改密码拦截：must_change_password=True 的用户只能访问修改密码页、登出、静态资源
            if current_user.must_change_password and endpoint:
                allowed_endpoints = (
                    'auth.change_password', 'auth.logout', 'static',
                    'main.set_theme',
                )
                if endpoint not in allowed_endpoints and not endpoint.startswith('static'):
                    return redirect(url_for('auth.change_password', forced='1'))

            # 登录后自动设置默认项目（主项目），不默认进入汇总视图
            if 'current_project_id' not in session:
                try:
                    main_project = current_user.get_main_project()
                    if main_project:
                        session['current_project_id'] = main_project.id
                        session['current_project_name'] = main_project.name
                    elif current_user.get_data_scope() == 'all' or current_user.is_admin():
                        # 管理员无主项目时默认进入汇总视图
                        session['current_project_id'] = None
                        session['current_project_name'] = '全部项目'
                except Exception:
                    pass

        if not endpoint:
            return
        # 静态资源、认证等不过滤
        if endpoint.startswith(('static', 'auth.', 'main.set_project', 'main.index', 'main.wizard',
                                'profile.', 'admin.', 'system.', 'dict_mgr.', 'help.')):
            return
        try:
            # 检查菜单是否禁用
            menu = SysMenu.query.filter_by(menu_code=endpoint, status=False).first()
            if menu:
                abort(403)
        except Exception:
            pass

        # 检查项目模块开关
        try:
            if current_user.is_authenticated:
                project_id = session.get('current_project_id')
                if project_id:
                    project = Project.query.get(project_id)
                    if project:
                        # 遍历菜单配置查找当前端点对应的模块
                        module_to_check = None
                        for group in _menu_config.get('groups', []):
                            # 检查分组级别的module
                            group_module = group.get('module')
                            for item in group.get('items', []):
                                if item.get('endpoint') == endpoint or (
                                    item.get('active') and item.get('active', '').endswith('.') and
                                    endpoint.startswith(item['active'][:-1])
                                ):
                                    # 优先使用菜单项的module，否则使用分组的module
                                    item_module = item.get('module')
                                    module_to_check = item_module if item_module else group_module
                                    break
                            if module_to_check:
                                break
                        if module_to_check and not project.is_module_enabled(module_to_check):
                            flash('该功能模块未在当前项目中启用', 'warning')
                            return redirect(url_for('main.index'))
        except Exception:
            pass

    # 错误日志自动捕获
    @app.errorhandler(Exception)
    def handle_exception(e):
        """捕获所有异常并记录错误日志"""
        import traceback
        from datetime import datetime
        from flask import request, current_app
        from flask_login import current_user
        from app.models import ErrorLog
        from app import db as _db
        import json

        try:
            err = ErrorLog(
                error_time=datetime.now(),
                error_type=type(e).__name__,
                error_message=str(e),
                stack_trace=traceback.format_exc(),
                request_url=request.url[:512] if request else '',
                request_method=request.method if request else '',
                request_params=json.dumps(request.form.to_dict(), ensure_ascii=False) if request and request.form else None,
                user_id=current_user.id if current_user.is_authenticated else None,
                username=current_user.username if current_user.is_authenticated else None,
                ip_address=request.remote_addr if request else None,
                user_agent=request.headers.get('User-Agent', '')[:512] if request else None,
            )
            _db.session.add(err)
            _db.session.commit()
        except Exception:
            _db.session.rollback()

        # 重新抛出异常，保持原有错误处理
        if current_app.debug:
            raise e
        from flask import render_template
        return render_template('errors/500.html', error=str(e)), 500

    return app
