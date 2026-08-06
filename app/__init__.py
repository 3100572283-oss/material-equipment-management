import json
import os
from flask import Flask
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问此页面。'
login_manager.login_message_category = 'warning'


def _add_column_if_missing(table_name, col_name, col_def):
    """幂等添加列（兼容SQLite和MySQL）"""
    from sqlalchemy import text, inspect, inspect as sa_inspect
    try:
        inspector = sa_inspect(db.engine)
        if table_name not in inspector.get_table_names():
            return
        columns = inspector.get_columns(table_name)
        col_names = [c['name'] for c in columns]
        if col_name not in col_names:
            mysql_def = col_def.replace('BOOLEAN', 'TINYINT(1)')
            db.session.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{col_name}` {mysql_def}"))
            db.session.commit()
            print(f"Added column: {table_name}.{col_name}")
    except Exception as e:
        print(f"Error adding {table_name}.{col_name}: {e}")
        db.session.rollback()


def _migrate_price_formula_fields():
    """迁移价格方案表字段：discount_type -> float_type, discount_value -> float_value"""
    from sqlalchemy import text, inspect
    try:
        inspector = inspect(db.engine)
        if 'price_formula' not in inspector.get_table_names():
            return
        col_names = [c['name'] for c in inspector.get_columns('price_formula')]

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


def _migrate_sys_role_menu_constraint():
    """迁移 sys_role_menu 表约束：从 (role_id, menu_id) 改为 (role_id, menu_id, operation)"""
    from sqlalchemy import text, inspect
    try:
        # 此迁移仅适用于SQLite旧数据库
        if db.engine.dialect.name != 'sqlite':
            return
        # 检查当前约束
        inspector = inspect(db.engine)
        result = None
        if 'sys_role_menu' in inspector.get_table_names():
            # MySQL/PostgreSQL: 使用 inspector 检查约束
            pass  # 约束迁移仅在 SQLite 时执行，MySQL 跳过
        if result:
            sql = result[0]
            # 如果旧约束存在，需要重建表
            if 'CONSTRAINT uq_role_menu UNIQUE (role_id, menu_id)' in sql:
                print("Migrating sys_role_menu constraint...")
                # 备份数据
                db.session.execute(text("""
                    CREATE TABLE sys_role_menu_backup AS SELECT * FROM sys_role_menu
                """))
                db.session.commit()

                # 删除旧表
                db.session.execute(text("DROP TABLE sys_role_menu"))
                db.session.commit()

                # 创建新表（使用正确的约束）
                db.session.execute(text("""
                    CREATE TABLE sys_role_menu (
                        id INTEGER NOT NULL,
                        role_id INTEGER NOT NULL,
                        menu_id INTEGER NOT NULL,
                        operation VARCHAR(16) DEFAULT 'view',
                        PRIMARY KEY (id),
                        CONSTRAINT uq_role_menu_op UNIQUE (role_id, menu_id, operation),
                        FOREIGN KEY(role_id) REFERENCES sys_role (id),
                        FOREIGN KEY(menu_id) REFERENCES sys_menu (id)
                    )
                """))
                db.session.commit()

                # 恢复数据（旧数据每个 menu_id 只有一条，默认设为 view）
                db.session.execute(text("""
                    INSERT INTO sys_role_menu (id, role_id, menu_id, operation)
                    SELECT id, role_id, menu_id, 'view' FROM sys_role_menu_backup
                """))
                db.session.commit()

                # 删除备份表
                db.session.execute(text("DROP TABLE sys_role_menu_backup"))
                db.session.commit()
                print("sys_role_menu constraint migrated successfully")
    except Exception as e:
        print(f"Error migrating sys_role_menu constraint: {e}")
        db.session.rollback()


def init_db_schema():
    """数据库表结构初始化/迁移"""
    # 迁移 sys_role_menu 约束
    _migrate_sys_role_menu_constraint()

    _add_column_if_missing('users', 'last_login_at', 'DATETIME')
    _add_column_if_missing('stock_ins', 'is_reconciled', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('stock_outs', 'is_reconciled', 'BOOLEAN DEFAULT 0')
    _add_column_if_missing('stock_outs', 'team_id', 'INTEGER')
    _add_column_if_missing('categories', 'parent_id', 'INTEGER')
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
    # M0 权限中台：AuthUser 兼容旧 users.dept_id（指向旧 sys_dept.id，供 Project.dept_id 查询）
    _add_column_if_missing('auth_core_user', 'dept_id', 'INTEGER')
    # 软删除字段（核心业务表 + 扩展业务表）
    for tbl in ['contracts', 'stock_ins', 'stock_outs', 'suppliers', 'materials', 'payments', 'reconciliations', 'equipment', 'turnover_material',
                'concrete_ticket', 'material_transfer', 'material_scrap', 'purchase_requisition', 'stock_check',
                'equipment_maintenance', 'equipment_rent_settle', 'equipment_inspection', 'turnover_record']:
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

    # RBAC权限体系：sys_menu 新增字段
    _add_column_if_missing('sys_menu', 'module_key', 'VARCHAR(32)')
    _add_column_if_missing('sys_menu', 'permission', 'VARCHAR(128)')

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
    _add_column_if_missing('reconciliation_items', 'capital_fee_days', 'INTEGER')
    _add_column_if_missing('sys_message', 'read_at', 'DATETIME')

    # 架构重构：双树分离 - projects 表增加 dept_id 行政归属 + remark
    _add_column_if_missing('projects', 'dept_id', 'INTEGER')
    _add_column_if_missing('projects', 'remark', 'VARCHAR(512)')

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
    _add_column_if_missing('users', 'status', "VARCHAR(16) DEFAULT 'active'")

    # 工号/产值相关字段
    _add_column_if_missing('work_numbers', 'parent_id', 'INTEGER')
    _add_column_if_missing('work_numbers', 'remark', 'VARCHAR(512)')

    # 物资报废相关字段
    _add_column_if_missing('material_scrap', 'status', "VARCHAR(16) DEFAULT 'draft'")

    # 权限系统增强：角色菜单权限表增加操作类型字段
    _add_column_if_missing('sys_role_menu', 'operation', "VARCHAR(16) DEFAULT 'view'")

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

    # 统一数据权限配置表迁移
    _migrate_role_data_scope_table()

    # AI中台增强：日志表新增字段 + 场景Prompt初始化
    _migrate_ai_scene_prompt()

    print("Database tables created/updated")


def _migrate_role_data_scope_table():
    """迁移/创建统一数据权限配置表 sys_role_data_scope
    整合原 sys_role.data_scope 和 sys_role_dept 数据
    """
    from sqlalchemy import text, inspect
    try:
        # 检查表是否存在
        inspector = inspect(db.engine)
        table_exists = 'sys_role_data_scope' in inspector.get_table_names()

        if not table_exists:
            print("Creating sys_role_data_scope table...")
            db.session.execute(text("""
                CREATE TABLE sys_role_data_scope (
                    id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    data_scope VARCHAR(16) DEFAULT 'all',
                    custom_depts TEXT,
                    updated_at DATETIME,
                    PRIMARY KEY (id),
                    CONSTRAINT uq_role_data_scope UNIQUE (role_id),
                    FOREIGN KEY(role_id) REFERENCES sys_role (id)
                )
            """))
            db.session.commit()
            print("sys_role_data_scope table created")

        # 数据迁移：把 sys_role.data_scope + sys_role_dept 整合到新表
        roles = db.session.execute(text("SELECT id, data_scope FROM sys_role")).fetchall()
        for role_id, data_scope in roles:
            existing = db.session.execute(
                text("SELECT id FROM sys_role_data_scope WHERE role_id=:rid"),
                {"rid": role_id}
            ).fetchone()
            if not existing:
                # 取自定义部门
                custom_dept_ids = db.session.execute(
                    text("SELECT dept_id FROM sys_role_dept WHERE role_id=:rid"),
                    {"rid": role_id}
                ).fetchall()
                custom_depts = [str(r[0]) for r in custom_dept_ids]
                # all 角色无自定义部门，其他保留
                if data_scope == 'custom' and custom_depts:
                    ds_value = 'custom'
                    cd_json = ','.join(custom_depts)
                else:
                    ds_value = data_scope if data_scope else 'all'
                    cd_json = None
                db.session.execute(
                    text("INSERT INTO sys_role_data_scope (role_id, data_scope, custom_depts) VALUES (:rid, :ds, :cd)"),
                    {"rid": role_id, "ds": ds_value, "cd": cd_json}
                )
        db.session.commit()
    except Exception as e:
        print(f"Error migrating sys_role_data_scope: {e}")
        db.session.rollback()


def _migrate_ai_scene_prompt():
    """AI中台增强迁移
    1. ai_call_log 表新增字段（dept_id/scene_code/total_tokens/cost_amount/ip_address）
    2. 初始化 ai_scene_prompt 场景配置默认数据
    """
    from app.models import AIScenePrompt

    # ---- 字段迁移（SQLite兼容性）
    _add_column_if_missing('ai_call_log', 'dept_id', 'INTEGER')
    _add_column_if_missing('ai_call_log', 'scene_code', 'VARCHAR(64)')
    _add_column_if_missing('ai_call_log', 'total_tokens', 'INTEGER DEFAULT 0')
    _add_column_if_missing('ai_call_log', 'cost_amount', 'NUMERIC(12,6) DEFAULT 0')
    _add_column_if_missing('ai_call_log', 'ip_address', 'VARCHAR(64)')

    # ---- 初始化默认场景Prompt
    default_scenes = [
        {
            'scene_code': 'text:chat',
            'scene_name': '智能对话',
            'scene_type': 'text',
            'system_prompt': '你是物资设备管理系统的智能助手，熟悉建筑物资全流程业务。请根据提供的数据回答用户问题。规则：1.没有相关数据时明确告知；2.回答简洁明了，使用中文；3.金额使用千分位格式；4.只回答当前项目的数据；5.问题不明确时礼貌询问补充。',
            'output_format': '自然语言回答',
            'permission_code': 'ai:chat:use',
            'sort_order': 1,
            'remark': '全局AI助手对话能力',
        },
        {
            'scene_code': 'vision:invoice',
            'scene_name': '发票识别',
            'scene_type': 'vision',
            'system_prompt': '请识别这张发票图片，提取发票信息。',
            'output_format': '{"invoice_type":"发票类型","invoice_code":"发票代码","invoice_number":"发票号码","invoice_date":"开票日期(YYYY-MM-DD)","buyer_name":"购买方名称","buyer_tax_id":"购买方税号","seller_name":"销售方名称","seller_tax_id":"销售方税号","amount": 金额(不含税,数字)","tax_amount":"税额(数字)","total_amount":"价税合计(数字)","tax_rate":"税率(百分比数字)","remarks":"备注"}。注意：只输出JSON，不要其他说明文字；金额只输出数字；无法识别的字段填null。',
            'permission_code': 'ai:invoice:recognize',
            'sort_order': 10,
            'remark': '采购合同-发票台账-新增发票识别',
        },
        {
            'scene_code': 'vision:license',
            'scene_name': '营业执照识别',
            'scene_type': 'vision',
            'system_prompt': '请识别这张营业执照图片，提取企业注册信息。',
            'output_format': '{"company_name":"企业名称","credit_code":"统一社会信用代码","legal_representative":"法定代表人","registered_capital":"注册资本","establish_date":"成立日期(YYYY-MM-DD)","business_scope":"经营范围","address":"住所","business_term":"营业期限"}。注意：只输出JSON，不要其他说明文字；日期统一YYYY-MM-DD格式；无法识别的字段填null。',
            'permission_code': 'ai:supplier:recognize_license',
            'sort_order': 20,
            'remark': '供应商管理-新增供应商营业执照识别',
        },
        {
            'scene_code': 'vision:concrete',
            'scene_name': '商砼小票识别',
            'scene_type': 'vision',
            'system_prompt': '请识别这张商砼（混凝土）送货小票图片，提取小票信息。',
            'output_format': '{"ticket_no":"小票号/流水号","supplier":"供应单位名称（搅拌站）","strength_grade":"砼标号/强度等级(如C30、C40)","pour_part":"浇筑部位","vehicle_no":"运输车号（车牌号）","driver_name":"司机姓名","volume":"方量/发货数量（数字，单位m³）","vehicle_count":"车次（数字，默认1）","slump":"坍落度（如180±20）","arrival_time":"到场时间（YYYY-MM-DD HH:MM格式）","remark":"备注"}。注意：只输出JSON，不要其他说明文字；方量和车次只输出数字；砼标号只输出如C30/C35/C40标识；无法识别的字段填null。',
            'permission_code': 'ai:concrete:recognize',
            'sort_order': 30,
            'remark': '行业工具-商砼小票-新增小票识别',
        },
        {
            'scene_code': 'vision:ocr',
            'scene_name': '通用文字提取',
            'scene_type': 'vision',
            'system_prompt': '请提取这张图片中的所有文字内容，按原文格式输出。如果是表格，请保持表格结构。',
            'output_format': '纯文本，按原文排版',
            'permission_code': 'ai:ocr:extract',
            'sort_order': 40,
            'remark': '所有多行输入框的拍照提取文字功能',
        },
        {
            'scene_code': 'vision:receipt',
            'scene_name': '送货单/收料小票识别',
            'scene_type': 'vision',
            'system_prompt': '请识别这张收料小票/送货单图片，提取物资明细信息。',
            'output_format': '{"supplier":"供应商名称","receipt_date":"收料日期(YYYY-MM-DD)","items":[{"material_name":"物资名称","specification":"规格型号","quantity":数量(数字),"unit":"单位","unit_price":"单价（数字）","amount": 金额（数字）}],"total_amount":"合计金额","remarks":"备注"}。注意：只输出JSON，不要其他说明文字；数量和金额只输出数字；有多行明细全部识别；无法识别的字段填null。',
            'permission_code': 'ai:receipt:recognize',
            'sort_order': 50,
            'remark': '入库单/送货单批量识别',
        },
        {
            'scene_code': 'text:parse_input',
            'scene_name': '智能录入解析',
            'scene_type': 'text',
            'system_prompt': '你是物资设备管理系统的智能录入助手。请解析用户输入的自然语言，提取关键信息。',
            'output_format': '{"type":"stock_in/stock_out/purchase_requisition","supplier":"供应商名称","usage_unit":"领料单位名称","items":[{"material":"物资名称","specification":"规格","quantity":数量(数字),"unit_price":单价(数字)}],"date":"日期(YYYY-MM-DD)","demand_date":"需求日期(采购申请用)"}。注意：数量和单价必须是数字；无法识别的字段留空或null；类型不明确则type为null。',
            'permission_code': 'ai:input:parse',
            'sort_order': 60,
            'remark': '语音/文字快速制单',
        },
        {
            'scene_code': 'text:report_analysis',
            'scene_name': '报表智能分析',
            'scene_type': 'text',
            'system_prompt': '你是物资设备管理系统的报表分析助手。请分析给定的数据并生成专业分析报告。',
            'output_format': '分析内容包括：1.本期核心数据摘要（总金额、总数量）；2.排名Top3的物资及占比；3.异常波动提醒（激增或骤减的物资）；4.管理建议。语言简洁专业，使用中文。',
            'permission_code': 'ai:report:analyze',
            'sort_order': 70,
            'remark': '各类统计报表的AI分析',
        },
        {
            'scene_code': 'text:approval_opinion',
            'scene_name': '审批意见生成',
            'scene_type': 'text',
            'system_prompt': '你是物资设备管理系统的审批助手。请根据单据数据生成审批意见。',
            'output_format': '如果action是approve，生成同意意见；如果action是reject，生成驳回意见（需说明原因）。意见简洁专业，不超过30字。',
            'permission_code': 'ai:approval:opinion',
            'sort_order': 80,
            'remark': '审批单据时的AI意见辅助',
        },
        {
            'scene_code': 'text:doc_summary',
            'scene_name': '单据智能摘要',
            'scene_type': 'text',
            'system_prompt': '你是物资设备管理系统的单据摘要助手。请根据单据数据生成一句话核心摘要。',
            'output_format': '生成一句话摘要，不超过50字；包含关键信息：金额、状态、进度等；语言简洁明了。',
            'permission_code': 'ai:summary:generate',
            'sort_order': 90,
            'remark': '各类业务单据摘要生成',
        },
        {
            'scene_code': 'speech:to_text',
            'scene_name': '语音转文字',
            'scene_type': 'speech',
            'system_prompt': '将语音音频转写为文字。',
            'output_format': '纯文本转写结果，自动加标点符号，识别口语中的数字自动转为规范书面语。',
            'permission_code': 'ai:speech:to_text',
            'sort_order': 100,
            'remark': '移动端语音输入、语音助手',
        },
        {
            'scene_code': 'structured:generate',
            'scene_name': '结构化文档生成',
            'scene_type': 'structured',
            'system_prompt': '你是物资设备管理系统的文档生成助手。请根据业务数据生成结构化文档。',
            'output_format': '根据指定文档类型（验收单/维保计划/分析报告等）生成规范的结构化内容，使用中文，格式清晰。',
            'permission_code': 'ai:structured:generate',
            'sort_order': 110,
            'remark': '验收单、维保计划、分析报告等结构化生成',
        },
    ]

    try:
        existing_codes = {sp.scene_code for sp in AIScenePrompt.query.all()}
        added = 0
        for scene in default_scenes:
            if scene['scene_code'] not in existing_codes:
                sp = AIScenePrompt(
                    scene_code=scene['scene_code'],
                    scene_name=scene['scene_name'],
                    scene_type=scene['scene_type'],
                    system_prompt=scene['system_prompt'],
                    output_format=scene['output_format'],
                    permission_code=scene['permission_code'],
                    is_enabled=True,
                    sort_order=scene['sort_order'],
                    remark=scene['remark'],
                )
                db.session.add(sp)
                added += 1
        if added > 0:
            db.session.commit()
            print(f"Initialized {added} AI scene prompts")
    except Exception as e:
        print(f"Error initializing AI scene prompts: {e}")
        db.session.rollback()


def _generate_permission(menu_code, operation='view'):
    """生成标准权限标识：模块:功能:操作"""
    if not menu_code:
        return None
    # 提取模块名和功能名
    parts = menu_code.split('.')
    if len(parts) >= 2:
        module = parts[0]
        func = parts[1]
        return f"{module}:{func}:{operation}"
    return f"{menu_code}:{operation}"


def init_rbac_data():
    """初始化RBAC权限数据 - 增强版"""
    from app.models import SysDept, SysRole, SysMenu, SysRoleMenu, SysModule, User, Project
    import json as _json
    import os as _os

    # ===== 1. 初始化模块注册表（与 Project._DEFAULT_MODULES 保持一致）=====
    modules_data = [
        {'key': 'module_approval', 'name': '审批流程', 'is_required': False, 'default_enabled': True, 'sort': 1},
        {'key': 'module_batch', 'name': '批次保质期管理', 'is_required': False, 'default_enabled': False, 'sort': 2},
        {'key': 'module_scrap', 'name': '物资报废', 'is_required': False, 'default_enabled': True, 'sort': 3},
        {'key': 'module_period_close', 'name': '期末结账', 'is_required': False, 'default_enabled': False, 'sort': 4},
        {'key': 'module_turnover', 'name': '周转材管理', 'is_required': False, 'default_enabled': False, 'sort': 5},
        {'key': 'module_equipment', 'name': '设备管理', 'is_required': False, 'default_enabled': True, 'sort': 6},
        {'key': 'module_industry_tools', 'name': '行业工具', 'is_required': False, 'default_enabled': True, 'sort': 7},
        {'key': 'module_subcontract', 'name': '分包扣款', 'is_required': False, 'default_enabled': False, 'sort': 8},
        {'key': 'module_ai', 'name': 'AI助手功能', 'is_required': False, 'default_enabled': False, 'sort': 9},
        {'key': 'module_quality_check', 'name': '入库质检流程', 'is_required': False, 'default_enabled': False, 'sort': 10},
    ]
    for data in modules_data:
        if not SysModule.query.filter_by(module_key=data['key']).first():
            module = SysModule(
                module_key=data['key'],
                module_name=data['name'],
                is_required=data['is_required'],
                default_enabled=data['default_enabled'],
                sort=data['sort']
            )
            db.session.add(module)
    db.session.commit()

    # ===== 2. 从menu_config.json同步菜单并生成权限标识 =====
    from flask import current_app
    config_path = _os.path.join(current_app.root_path, 'static', 'config', 'menu_config.json')
    if _os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            menu_data = _json.load(f)
        groups = menu_data.get('groups', [])

        # 检测是否有旧格式数据（目录没有menu_code），有则清空重建
        old_catalogs = SysMenu.query.filter_by(menu_type='catalog', parent_id=None).filter(
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
            group_module = group.get('module', '')

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
                    parent_id=None,
                    status=True,
                    module_key=group_module or None,
                    remark=catalog_remark
                )
                db.session.add(catalog)
                db.session.flush()
            else:
                # 同步更新
                changed = False
                if (not catalog.icon) and group.get('icon', ''):
                    catalog.icon = group.get('icon', '')
                    changed = True
                if catalog.sort != catalog_sort:
                    catalog.sort = catalog_sort
                    changed = True
                if (not catalog.module_key) and group_module:
                    catalog.module_key = group_module or None
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
                item_module = item.get('module', '')
                menu = SysMenu.query.filter_by(menu_code=item_code, menu_type='menu').first()
                item_remark = _json.dumps({
                    'section': item.get('section', ''),
                    'active': item.get('active', ''),
                }, ensure_ascii=False)

                # 自动生成权限标识
                auto_permission = _generate_permission(item_code, 'view')

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
                        permission=auto_permission,
                        module_key=item_module or group_module or None,
                        remark=item_remark
                    )
                    db.session.add(menu)
                    db.session.flush()
                else:
                    # 同步更新
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
                    if not menu.permission or (menu.permission and menu.permission.startswith('module_')):
                        menu.permission = auto_permission
                        changed = True
                    if (not menu.module_key) and (item_module or group_module):
                        menu.module_key = item_module or group_module or None
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

    # ===== 2.5 模块联动：自动同步模块状态到 sys_module =====
    # 遍历所有菜单，提取出实际使用的 module_key，确保 sys_module 表覆盖
    used_modules = set()
    for m in SysMenu.query.filter(SysMenu.module_key.isnot(None)).all():
        if m.module_key:
            used_modules.add(m.module_key)
    # 从目录（catalog）的 permission 字段提取
    for c in SysMenu.query.filter_by(menu_type='catalog').all():
        if c.permission and c.permission.startswith('module_'):
            used_modules.add(c.permission)
    for mk in used_modules:
        if not SysModule.query.filter_by(module_key=mk).first():
            # 默认从modules_data查模块名
            name_map = {d['key']: d['name'] for d in modules_data}
            new_mod = SysModule(
                module_key=mk,
                module_name=name_map.get(mk, mk.replace('module_', '').upper()),
                is_required=False,
                default_enabled=True,
                sort=99
            )
            db.session.add(new_mod)
    db.session.commit()

    # ===== 3. 初始化部门（升级为树形组织架构）=====
    depts_data = [
        {'code': 'HQ', 'name': '总公司', 'parent_id': None, 'dept_type': 'company', 'sort': 1},
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

    # ===== 4. 初始化角色 =====
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

    # ===== 5. 超级管理员拥有所有菜单的所有操作权限 =====
    super_admin_role = SysRole.query.filter_by(role_code='super_admin').first()
    if super_admin_role:
        existing = set((rm.menu_id, rm.operation) for rm in SysRoleMenu.query.filter_by(role_id=super_admin_role.id).all())
        all_menus = SysMenu.query.all()
        all_operations = ['view', 'create', 'edit', 'delete', 'import', 'export', 'approve', 'print']
        for menu in all_menus:
            for op in all_operations:
                if (menu.id, op) not in existing:
                    rp = SysRoleMenu(role_id=super_admin_role.id, menu_id=menu.id, operation=op)
                    db.session.add(rp)

    # ===== 6. 更新默认admin用户关联角色和部门 =====
    admin_user = User.query.filter_by(username='admin').first()
    if admin_user:
        super_admin_role = SysRole.query.filter_by(role_code='super_admin').first()
        hq_dept = SysDept.query.filter_by(dept_code='HQ').first()
        if super_admin_role:
            admin_user.role_id = super_admin_role.id
        if hq_dept:
            admin_user.dept_id = hq_dept.id

    # ===== 7. 为所有现有用户创建用户项目关联（兼容迁移）=====
    from app.models import SysUserProject
    for user in User.query.all():
        if user.user_projects:
            continue
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
    # 检查每个类型是否已有数据，只添加缺失的类型
    existing_types = db.session.query(SteelSpecWeight.spec_type).distinct().all()
    existing_type_set = {t[0] for t in existing_types}

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
        # 等边角钢
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
        # 槽钢
        ('channel', '5#', 5.438, 'kg/m', '槽钢'),
        ('channel', '6.3#', 6.634, 'kg/m', '槽钢'),
        ('channel', '8#', 8.045, 'kg/m', '槽钢'),
        ('channel', '10#', 10.007, 'kg/m', '槽钢'),
        ('channel', '12#', 12.059, 'kg/m', '槽钢'),
        ('channel', '14#a', 14.535, 'kg/m', '槽钢'),
        ('channel', '16#a', 17.240, 'kg/m', '槽钢'),
        ('channel', '18#a', 20.174, 'kg/m', '槽钢'),
        ('channel', '20#a', 22.637, 'kg/m', '槽钢'),
        ('channel', '22#a', 24.999, 'kg/m', '槽钢'),
        ('channel', '25#a', 27.410, 'kg/m', '槽钢'),
        ('channel', '28#a', 31.427, 'kg/m', '槽钢'),
        ('channel', '32#a', 38.083, 'kg/m', '槽钢'),
        ('channel', '36#a', 41.209, 'kg/m', '槽钢'),
        ('channel', '40#a', 46.878, 'kg/m', '槽钢'),
        # 工字钢
        ('ibeam', '10#', 11.261, 'kg/m', '工字钢'),
        ('ibeam', '12#', 13.987, 'kg/m', '工字钢'),
        ('ibeam', '14#', 16.890, 'kg/m', '工字钢'),
        ('ibeam', '16#', 20.513, 'kg/m', '工字钢'),
        ('ibeam', '18#', 24.143, 'kg/m', '工字钢'),
        ('ibeam', '20#a', 27.929, 'kg/m', '工字钢'),
        ('ibeam', '22#a', 33.070, 'kg/m', '工字钢'),
        ('ibeam', '25#a', 38.105, 'kg/m', '工字钢'),
        ('ibeam', '28#a', 43.492, 'kg/m', '工字钢'),
        ('ibeam', '32#a', 52.717, 'kg/m', '工字钢'),
        ('ibeam', '36#a', 60.037, 'kg/m', '工字钢'),
        ('ibeam', '40#a', 67.598, 'kg/m', '工字钢'),
        ('ibeam', '45#a', 80.420, 'kg/m', '工字钢'),
        ('ibeam', '50#a', 93.654, 'kg/m', '工字钢'),
        ('ibeam', '56#a', 106.316, 'kg/m', '工字钢'),
        ('ibeam', '63#a', 121.407, 'kg/m', '工字钢'),
        # H型钢
        ('hbeam', 'H200×200×8×12', 50.5, 'kg/m', 'H型钢'),
        ('hbeam', 'H250×250×9×14', 72.4, 'kg/m', 'H型钢'),
        ('hbeam', 'H300×150×6.5×9', 37.3, 'kg/m', 'H型钢'),
        ('hbeam', 'H350×175×7×11', 50.0, 'kg/m', 'H型钢'),
        ('hbeam', 'H400×200×8×13', 66.0, 'kg/m', 'H型钢'),
        ('hbeam', 'H500×200×10×16', 89.6, 'kg/m', 'H型钢'),
        ('hbeam', 'H600×200×11×17', 103.0, 'kg/m', 'H型钢'),
        ('hbeam', 'H700×300×13×24', 185.0, 'kg/m', 'H型钢'),
        ('hbeam', 'H800×300×14×26', 210.0, 'kg/m', 'H型钢'),
        # 扁钢
        ('flat', '25×3', 0.59, 'kg/m', '扁钢'),
        ('flat', '25×4', 0.79, 'kg/m', '扁钢'),
        ('flat', '30×3', 0.71, 'kg/m', '扁钢'),
        ('flat', '30×4', 0.94, 'kg/m', '扁钢'),
        ('flat', '40×3', 0.94, 'kg/m', '扁钢'),
        ('flat', '40×4', 1.26, 'kg/m', '扁钢'),
        ('flat', '50×5', 1.96, 'kg/m', '扁钢'),
        ('flat', '60×6', 2.83, 'kg/m', '扁钢'),
        ('flat', '80×8', 5.02, 'kg/m', '扁钢'),
        ('flat', '100×10', 7.85, 'kg/m', '扁钢'),
        # 方钢
        ('square', '10×10', 0.79, 'kg/m', '方钢'),
        ('square', '12×12', 1.13, 'kg/m', '方钢'),
        ('square', '16×16', 2.01, 'kg/m', '方钢'),
        ('square', '20×20', 3.14, 'kg/m', '方钢'),
        ('square', '25×25', 4.91, 'kg/m', '方钢'),
        ('square', '30×30', 7.07, 'kg/m', '方钢'),
        ('square', '40×40', 12.56, 'kg/m', '方钢'),
        ('square', '50×50', 19.63, 'kg/m', '方钢'),
        # 不等边角钢
        ('angle_unequal', 'L25×16×3', 0.912, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L32×20×3', 1.171, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L40×25×3', 1.484, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L45×28×3', 1.687, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L50×32×3', 1.900, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L56×36×4', 2.818, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L63×40×5', 3.920, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L70×45×5', 4.391, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L75×50×5', 4.808, 'kg/m', '不等边角钢'),
        ('angle_unequal', 'L80×50×5', 5.005, 'kg/m', '不等边角钢'),
        # 圆钢
        ('round', 'Φ6', 0.222, 'kg/m', '圆钢'),
        ('round', 'Φ8', 0.395, 'kg/m', '圆钢'),
        ('round', 'Φ10', 0.617, 'kg/m', '圆钢'),
        ('round', 'Φ12', 0.888, 'kg/m', '圆钢'),
        ('round', 'Φ14', 1.208, 'kg/m', '圆钢'),
        ('round', 'Φ16', 1.578, 'kg/m', '圆钢'),
        ('round', 'Φ18', 1.998, 'kg/m', '圆钢'),
        ('round', 'Φ20', 2.466, 'kg/m', '圆钢'),
        ('round', 'Φ25', 3.853, 'kg/m', '圆钢'),
        ('round', 'Φ30', 5.549, 'kg/m', '圆钢'),
        ('round', 'Φ40', 9.865, 'kg/m', '圆钢'),
        ('round', 'Φ50', 15.413, 'kg/m', '圆钢'),
        # 方管/矩形管
        ('square_tube', '25×25×2.0', 1.45, 'kg/m', '方管'),
        ('square_tube', '30×30×2.0', 1.78, 'kg/m', '方管'),
        ('square_tube', '40×40×2.0', 2.42, 'kg/m', '方管'),
        ('square_tube', '50×50×2.5', 3.81, 'kg/m', '方管'),
        ('square_tube', '60×40×2.5', 3.81, 'kg/m', '矩形管'),
        ('square_tube', '60×60×3.0', 5.37, 'kg/m', '方管'),
        ('square_tube', '80×40×3.0', 5.19, 'kg/m', '矩形管'),
        ('square_tube', '80×80×4.0', 9.33, 'kg/m', '方管'),
        ('square_tube', '100×50×4.0', 7.43, 'kg/m', '矩形管'),
        ('square_tube', '100×100×5.0', 14.91, 'kg/m', '方管'),
        # 建材体积重量（密度 t/m³）
        ('building', '砂石', 1.5, 't/m³', '密度1.5吨/方'),
        ('building', '混凝土', 2.4, 't/m³', '密度2.4吨/方'),
        ('building', '水泥', 1.3, 't/m³', '密度1.3吨/方'),
    ]

    added = 0
    for spec_type, spec_name, weight, unit, remark in specs:
        if spec_type in existing_type_set:
            continue
        s = SteelSpecWeight(spec_type=spec_type, spec_name=spec_name,
                           theoretical_weight=weight, unit=unit, remark=remark)
        db.session.add(s)
        added += 1

    if added > 0:
        db.session.commit()
        print(f"Steel specs initialized ({added} new specs added)")


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
    Migrate(app, db)
    login_manager.init_app(app)
    
    # P1-7: 启用CSRF保护
    csrf.init_app(app)

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

    from app.purchase_order import bp as purchase_order_bp
    app.register_blueprint(purchase_order_bp, url_prefix='/purchase_order')

    from app.material_return import bp as material_return_bp
    app.register_blueprint(material_return_bp, url_prefix='/material_return')

    from app.tools import bp as tools_bp
    app.register_blueprint(tools_bp, url_prefix="/tools")

    from app.message import bp as message_bp
    app.register_blueprint(message_bp, url_prefix='/message')

    # P1-7: 豁免移动端和AI路由的CSRF保护（这些路由使用AJAX/API调用）
    from app.mobile import bp as _mobile_bp_for_csrf
    from app.ai import bp as _ai_bp_for_csrf
    from app.auth import bp as _auth_bp_for_csrf
    csrf.exempt(_mobile_bp_for_csrf)
    csrf.exempt(_ai_bp_for_csrf)
    csrf.exempt(_auth_bp_for_csrf)  # 登录/登出是入口，豁免CSRF

    # M0 权限中台（绞杀者模式）：独立蓝图，最小侵入接入
    from app.auth_core import bp as auth_core_bp
    app.register_blueprint(auth_core_bp, url_prefix='/auth_core')

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
        if current_user and hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
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
                # M0 全量替换：登录身份已是 auth_core.AuthUser，has_permission 直接委托 AuthGateway
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
        """根据当前用户权限、项目模块开关和菜单状态返回可见菜单分组
        使用统一的 PermissionService 获取菜单树，确保前后端权限判断一致
        """
        from flask_login import current_user
        from app.services.permission_service import permission_service
        from app.models import Project
        from flask import session
        import json as _json

        project_module_config = {}
        result = []

        try:
            project_id = session.get('current_project_id')
            if project_id:
                project = Project.query.get(project_id)
                if project:
                    project_module_config = project.get_module_config()

            if not current_user.is_authenticated:
                return result

            menu_tree = permission_service.get_user_menu_tree(current_user)

            for catalog in menu_tree:
                module_key = catalog.get('moduleKey')

                if module_key:
                    if module_key in project_module_config:
                        if not project_module_config[module_key]:
                            continue
                    else:
                        enabled = get_config(module_key, 'true')
                        if str(enabled).lower() != 'true':
                            continue

                catalog_extra = {}
                if catalog.get('code') == 'system' and (not current_user.is_admin()):
                    continue

                items = []
                for menu in catalog.get('children', []):
                    endpoint = menu.get('code')
                    item_module = menu.get('moduleKey')

                    if item_module:
                        if item_module in project_module_config:
                            if not project_module_config[item_module]:
                                continue
                        else:
                            enabled = get_config(item_module, 'true')
                            if str(enabled).lower() != 'true':
                                continue

                    item_extra = {}
                    try:
                        if menu.get('permission'):
                            item_extra['active'] = menu['permission']
                    except Exception:
                        pass

                    items.append({
                        'title': menu.get('name', ''),
                        'endpoint': endpoint,
                        'active': item_extra.get('active', endpoint or ''),
                        'section': '',
                        'icon': menu.get('icon', ''),
                        'module': item_module,
                    })

                if not items:
                    continue

                catalog_extra = {}
                result.append({
                    'id': catalog.get('code') or f'group_{catalog.get("id")}',
                    'title': catalog.get('name', ''),
                    'icon': catalog.get('icon', ''),
                    'module': module_key,
                    'require_admin': catalog_extra.get('require_admin', False),
                    'divider_before': catalog_extra.get('divider_before', False),
                    'items': items,
                })
        except Exception as e:
            print(f"get_menu_groups error: {e}")
            pass
        return result

    app.jinja_env.globals['get_menu_groups'] = get_menu_groups
    app.jinja_env.globals['_menu_config'] = _menu_config

    def has_button_permission(menu_code, action):
        """检查当前用户是否拥有指定菜单的按钮权限
        统一调用 PermissionService，确保前后端权限判断一致
        
        Args:
            menu_code: 菜单code标识
            action: 按钮操作，如 'view', 'create', 'edit', 'delete', 'export', 'import', 'approve', 'print'
        
        Returns:
            bool: 是否有权限
        """
        from flask_login import current_user
        from app.services.permission_service import permission_service
        
        if not current_user.is_authenticated:
            return False
        
        buttons = permission_service.get_menu_button_permissions(current_user, menu_code)
        return action in buttons

    app.jinja_env.globals['has_button_permission'] = has_button_permission

    def _is_system_page():
        """判断当前页面是否是系统管理页面"""
        from flask import request
        ep = request.endpoint or ''
        sys_prefixes = [
            'admin.', 'system.', 'dict_mgr.', 'org_sync.',
            'ai.config', 'ai.logs', 'ai.scene', 'ai.statistics', 'approval.flows',
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
        # M0 权限中台：组织树骨架 + 铁建岗位角色模板 + 超级管理员种子
        from app.auth_core.init_data import init_auth_core_data
        init_auth_core_data()
        # 主数据统一改造：建立项目常用关联
        from app.utils import init_master_data_unification
        init_master_data_unification()
        # 初始化默认管理员账号
        init_default_users()

    # 拦截禁用菜单的访问 & 模块开关拦截 & 更新用户活跃时间 & 初始化向导
    @app.before_request
    def redirect_legacy_rbac():
        """M0 全量替换：旧 rbac/用户管理页已废弃，切流后重定向到 auth_core 统一入口。

        仅当 AUTH_CORE_ENABLED=true 时生效；flag 关闭时旧页面恢复（回滚安全）。
        保留 /system/api 与 /admin/api 不被拦截，避免误伤其它内部调用。
        """
        from flask import request, redirect, url_for
        from app.auth_core.gateway import _enabled
        if not _enabled():
            return
        path = request.path
        if path.startswith('/system/'):
            if path.startswith('/system/api/'):
                return
            if (path.startswith('/system/roles') or path.startswith('/system/menus')
                    or path.startswith('/system/modules')):
                return redirect(url_for('auth_core.page_roles'))
            return redirect(url_for('auth_core.page_orgs'))
        if path.startswith('/admin/users'):
            if path.startswith('/admin/api/'):
                return
            return redirect(url_for('auth_core.page_users'))

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
                if current_user and hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
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

        # P1-5: 项目数据隔离强制验证 — 验证当前用户是否有权访问选中的项目
        if current_user and hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
            _pid = session.get('current_project_id')
            if _pid:
                try:
                    if not current_user.can_access_project(_pid):
                        session.pop('current_project_id', None)
                        if request.path.startswith('/api/') or request.is_json:
                            return jsonify({'code': 403, 'message': '无权限访问该项目'}), 403
                        flash('您无权限访问该项目，已自动切换。', 'warning')
                        return redirect(url_for('main.index'))
                except Exception:
                    pass  # 权限检查异常时不阻断请求，避免影响系统可用性

        # 更新用户最后活跃时间（60秒更新一次，减轻数据库压力）
        if current_user and hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
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
                                'profile.', 'admin.', 'system.', 'dict_mgr.', 'help.', 'ai.')):
            return
        try:
            # 检查菜单是否禁用
            menu = SysMenu.query.filter_by(menu_code=endpoint, status=False).first()
            if menu:
                abort(403)
        except Exception:
            pass

        # 检查项目模块开关（使用数据库module_key）
        try:
            if current_user and hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
                project_id = session.get('current_project_id')
                # 从数据库查询当前端点对应的菜单及其模块标识
                menu = SysMenu.query.filter_by(menu_code=endpoint, menu_type='menu').first()
                if menu and menu.module_key:
                    module_to_check = menu.module_key

                    # 系统级模块开关检查
                    from app.models import SysModule
                    sys_module = SysModule.query.filter_by(module_key=module_to_check).first()
                    if sys_module and not sys_module.status:
                        flash('该功能模块已被系统关闭', 'warning')
                        return redirect(url_for('main.index'))

                    # 项目级模块开关检查
                    if project_id:
                        project = Project.query.get(project_id)
                        if project and not project.is_module_enabled(module_to_check):
                            flash('该功能模块未在当前项目中启用', 'warning')
                            return redirect(url_for('main.index'))
        except Exception:
            pass

        # ===== 功能权限拦截（统一权限中心） =====
        from werkzeug.exceptions import HTTPException
        try:
            if current_user.is_authenticated and not current_user.is_admin():
                # 查找当前端点对应的菜单（按menu_code匹配）
                menu = SysMenu.query.filter_by(menu_code=endpoint, menu_type='menu').first()
                if menu and menu.permission:
                    # 解析权限标识
                    perm = menu.permission
                    # 检查方法类型
                    if request.method != 'GET':
                        op_map = {
                            'POST': 'create', 'PUT': 'edit', 'PATCH': 'edit',
                            'DELETE': 'delete',
                        }
                        required_op = op_map.get(request.method)
                        if required_op:
                            perm_base = perm.rsplit(':', 1)[0] if ':' in perm else perm
                            check_perm = f"{perm_base}:{required_op}"
                            if not current_user.has_permission(check_perm):
                                if request.path.startswith('/api/') or request.is_json:
                                    from flask import jsonify
                                    return jsonify({'code': 403, 'message': f'无权限：{check_perm}'}), 403
                                from flask import abort
                                abort(403)
                    else:
                        # GET方法需要view权限
                        if not current_user.has_permission(perm):
                            if request.path.startswith('/api/') or request.is_json:
                                from flask import jsonify
                                return jsonify({'code': 403, 'message': f'无权限：{perm}'}), 403
                            from flask import abort
                            abort(403)
        except HTTPException:
            # 重新抛出HTTP异常(abort触发)
            raise
        except Exception:
            # 其他异常静默失败，不影响正常访问
            import sys as _sys, traceback as _tb
            _tb.print_exc(file=_sys.stderr)
            pass

    # 错误日志自动捕获 - 仅处理非HTTPException的异常
    # 全局 IntegrityError 处理：外键约束冲突时显示友好提示
    from sqlalchemy.exc import IntegrityError

    @app.errorhandler(IntegrityError)
    def handle_integrity_error(e):
        from flask import request, flash, redirect, render_template
        try:
            db.session.rollback()
        except Exception:
            pass
        
        # 记录错误日志
        try:
            from app.models import ErrorLog
            from datetime import datetime
            import traceback, json
            err = ErrorLog(
                error_time=datetime.now(),
                error_type='IntegrityError',
                error_message=str(e.orig) if hasattr(e, 'orig') else str(e),
                stack_trace=traceback.format_exc(),
                request_url=request.url[:512] if request else '',
                request_method=request.method if request else '',
                request_params=json.dumps(request.form.to_dict(), ensure_ascii=False) if request and request.form else None,
                user_id=current_user.id if current_user.is_authenticated else None,
                username=current_user.username if current_user.is_authenticated else None,
                ip_address=request.remote_addr if request else None,
                user_agent=request.headers.get('User-Agent', '')[:512] if request else None,
            )
            db.session.add(err)
            db.session.commit()
        except Exception:
            db.session.rollback()

        # 如果是AJAX请求返回JSON
        if request and request.is_json or (request and request.headers.get('X-Requested-With') == 'XMLHttpRequest'):
            return jsonify({'success': False, 'message': '操作失败：数据存在关联引用，无法删除或修改。请先处理相关联的数据。'}), 400

        # 普通请求：flash提示并重定向回来源页
        flash('操作失败：该数据存在关联引用，无法删除。请先处理相关联的数据后再操作。', 'danger')
        return redirect(request.referrer or url_for('main.index'))

    @app.errorhandler(Exception)
    def handle_exception(e):
        """捕获所有异常并记录错误日志（HTTPException由专门handler处理）"""
        from werkzeug.exceptions import HTTPException
        # HTTPException直接重新抛出让Flask用专门的handler处理
        if isinstance(e, HTTPException):
            return e

        import traceback
        from datetime import datetime
        from flask import request, current_app, render_template
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
        return render_template('errors/500.html', error=str(e)), 500

    # 显式注册 403/404 handler，返回中文提示页面
    from werkzeug.exceptions import Forbidden, NotFound

    @app.errorhandler(403)
    def handle_403(e):
        from flask import render_template
        try:
            return render_template('errors/404.html', error='您没有权限访问该页面'), 403
        except Exception:
            return "您没有权限访问该页面", 403

    @app.errorhandler(404)
    def handle_404(e):
        from flask import render_template
        try:
            return render_template('errors/404.html'), 404
        except Exception:
            return "页面不存在", 404

    return app
