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
    _add_column_if_missing('stock_ins', 'quality_checker', 'VARCHAR(64)')
    _add_column_if_missing('stock_ins', 'quality_check_time', 'DATETIME')
    _add_column_if_missing('stock_ins', 'quality_remark', 'TEXT')

    # 增强阶段三：用户登录锁定字段
    _add_column_if_missing('users', 'failed_login_count', 'INTEGER DEFAULT 0')
    _add_column_if_missing('users', 'locked_until', 'DATETIME')
    _add_column_if_missing('users', 'last_login_ip', 'VARCHAR(64)')

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

    db.create_all()
    print("Database tables created/updated")


def init_rbac_data():
    """初始化RBAC权限数据"""
    from app.models import SysDept, SysRole, SysMenu, SysRoleMenu, User
    
    # 初始化部门
    depts_data = [
        {'code': 'HQ', 'name': '总公司', 'parent_id': 0, 'sort': 1},
        {'code': 'MATERIAL', 'name': '物资部', 'parent_id': 1, 'sort': 1},
        {'code': 'FINANCE', 'name': '财务部', 'parent_id': 1, 'sort': 2},
        {'code': 'ENGINEERING', 'name': '工程部', 'parent_id': 1, 'sort': 3},
    ]
    
    for i, data in enumerate(depts_data):
        if not SysDept.query.filter_by(dept_code=data['code']).first():
            dept = SysDept(
                dept_code=data['code'],
                dept_name=data['name'],
                parent_id=data['parent_id'],
                sort=data['sort']
            )
            db.session.add(dept)
            db.session.flush()
            data['id'] = dept.id
            depts_data[i] = data
    
    # 初始化角色
    roles_data = [
        {'code': 'super_admin', 'name': '超级管理员', 'data_scope': 'all', 'sort': 1},
        {'code': 'material_admin', 'name': '物资管理员', 'data_scope': 'dept', 'sort': 2},
        {'code': 'finance_user', 'name': '财务人员', 'data_scope': 'dept', 'sort': 3},
        {'code': 'viewer', 'name': '普通查看员', 'data_scope': 'dept', 'sort': 4},
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
    
    # 初始化菜单（menu_code 对应 menu_config.json 中的 endpoint）
    # 使用 parent_name 建立父子关系，确保数据一致性
    menus_data = [
        # 1. 工作台
        {'name': '工作台', 'type': 'catalog', 'icon': 'bi-speedometer2', 'sort': 0, 'code': ''},
        {'name': '首页', 'type': 'menu', 'path': '/', 'icon': 'bi-house', 'sort': 1, 'parent_name': '工作台', 'code': 'main.index'},
        {'name': '我的审批', 'type': 'menu', 'path': '/approval/my', 'icon': 'bi-clipboard-check', 'sort': 2, 'parent_name': '工作台', 'code': 'approval.my_approvals'},
        # 2. 基础数据
        {'name': '基础数据', 'type': 'catalog', 'icon': 'bi-database', 'sort': 1, 'code': ''},
        {'name': '项目管理', 'type': 'menu', 'path': '/project', 'icon': 'bi-geo-alt', 'sort': 1, 'parent_name': '基础数据', 'code': 'project.index'},
        {'name': '供应商管理', 'type': 'menu', 'path': '/supplier', 'icon': 'bi-people', 'sort': 2, 'parent_name': '基础数据', 'code': 'supplier.index'},
        {'name': '物资分类', 'type': 'menu', 'path': '/category', 'icon': 'bi-list', 'sort': 3, 'parent_name': '基础数据', 'code': 'category.index'},
        {'name': '常用材料表', 'type': 'menu', 'path': '/material', 'icon': 'bi-box', 'sort': 4, 'parent_name': '基础数据', 'code': 'material.index'},
        {'name': '用料单位', 'type': 'menu', 'path': '/unit', 'icon': 'bi-building', 'sort': 5, 'parent_name': '基础数据', 'code': 'unit.index'},
        {'name': '工号管理', 'type': 'menu', 'path': '/work_number', 'icon': 'bi-hash', 'sort': 6, 'parent_name': '基础数据', 'code': 'work_number.index'},
        # 3. 采购合同
        {'name': '采购合同', 'type': 'catalog', 'icon': 'bi-file-earmark-text', 'sort': 2, 'code': ''},
        {'name': '采购申请', 'type': 'menu', 'path': '/purchase', 'icon': 'bi-list-task', 'sort': 1, 'parent_name': '采购合同', 'code': 'purchase_requisition.index'},
        {'name': '合同台账', 'type': 'menu', 'path': '/contract', 'icon': 'bi-file-text', 'sort': 2, 'parent_name': '采购合同', 'code': 'contract.index'},
        {'name': '发票台账', 'type': 'menu', 'path': '/contract/invoices', 'icon': 'bi-receipt', 'sort': 3, 'parent_name': '采购合同', 'code': 'contract.invoices'},
        {'name': '付款台账', 'type': 'menu', 'path': '/contract/payments', 'icon': 'bi-credit-card', 'sort': 4, 'parent_name': '采购合同', 'code': 'contract.payments'},
        {'name': '付款申请', 'type': 'menu', 'path': '/payment_application', 'icon': 'bi-cash-stack', 'sort': 4.5, 'parent_name': '采购合同', 'code': 'payment_application.index'},
        {'name': '对账管理', 'type': 'menu', 'path': '/reconciliation', 'icon': 'bi-calculator', 'sort': 5, 'parent_name': '采购合同', 'code': 'reconciliation.index'},
        {'name': '价格方案', 'type': 'menu', 'path': '/price_formula', 'icon': 'bi-tag', 'sort': 6, 'parent_name': '采购合同', 'code': 'price_formula.index'},
        # 4. 库存管理
        {'name': '库存管理', 'type': 'catalog', 'icon': 'bi-box-seam', 'sort': 3, 'code': ''},
        {'name': '入库管理', 'type': 'menu', 'path': '/stock/in', 'icon': 'bi-arrow-down-circle', 'sort': 1, 'parent_name': '库存管理', 'code': 'stock_in.index'},
        {'name': '出库管理', 'type': 'menu', 'path': '/stock/out', 'icon': 'bi-arrow-up-circle', 'sort': 2, 'parent_name': '库存管理', 'code': 'stock_out.index'},
        {'name': '库存查询', 'type': 'menu', 'path': '/inventory', 'icon': 'bi-search', 'sort': 3, 'parent_name': '库存管理', 'code': 'inventory.index'},
        {'name': '库存盘点', 'type': 'menu', 'path': '/stock_check', 'icon': 'bi-clipboard-check', 'sort': 4, 'parent_name': '库存管理', 'code': 'stock_check.index'},
        {'name': '项目调拨', 'type': 'menu', 'path': '/transfer', 'icon': 'bi-arrow-left-right', 'sort': 5, 'parent_name': '库存管理', 'code': 'material_transfer.index'},
        # 5. 周转材管理
        {'name': '周转材管理', 'type': 'catalog', 'icon': 'bi-arrow-repeat', 'sort': 4, 'code': ''},
        {'name': '周转材台账', 'type': 'menu', 'path': '/turnover', 'icon': 'bi-box-seam', 'sort': 1, 'parent_name': '周转材管理', 'code': 'turnover_material.index'},
        {'name': '领用归还', 'type': 'menu', 'path': '/turnover/record', 'icon': 'bi-arrow-left-right', 'sort': 2, 'parent_name': '周转材管理', 'code': 'turnover_material.record_index'},
        {'name': '租赁费结算', 'type': 'menu', 'path': '/turnover/fee', 'icon': 'bi-calculator', 'sort': 3, 'parent_name': '周转材管理', 'code': 'turnover_material.rental_bill'},
        # 6. 设备管理
        {'name': '设备管理', 'type': 'catalog', 'icon': 'bi-gear', 'sort': 5, 'code': ''},
        {'name': '设备台账', 'type': 'menu', 'path': '/equipment', 'icon': 'bi-machine', 'sort': 1, 'parent_name': '设备管理', 'code': 'equipment.index'},
        {'name': '维保记录', 'type': 'menu', 'path': '/equipment/maintenance', 'icon': 'bi-tools', 'sort': 2, 'parent_name': '设备管理', 'code': 'equipment.maintenance'},
        {'name': '设备折旧', 'type': 'menu', 'path': '/equipment/depreciation', 'icon': 'bi-graph-down', 'sort': 3, 'parent_name': '设备管理', 'code': 'equipment.depreciation'},
        # 7. 统计报表
        {'name': '统计报表', 'type': 'catalog', 'icon': 'bi-bar-chart-line', 'sort': 6, 'code': ''},
        {'name': '入库统计', 'type': 'menu', 'path': '/report/in', 'icon': 'bi-graph-up', 'sort': 1, 'parent_name': '统计报表', 'code': 'report.stock_in_report'},
        {'name': '出库统计', 'type': 'menu', 'path': '/report/out', 'icon': 'bi-graph-down', 'sort': 2, 'parent_name': '统计报表', 'code': 'report.stock_out_report'},
        {'name': '物资动态表', 'type': 'menu', 'path': '/report/dynamic', 'icon': 'bi-table', 'sort': 3, 'parent_name': '统计报表', 'code': 'report.material_movement'},
        {'name': '供应商往来台账', 'type': 'menu', 'path': '/supplier/ledger', 'icon': 'bi-journal-text', 'sort': 4, 'parent_name': '统计报表', 'code': 'supplier.ledger'},
        {'name': '高级分析', 'type': 'menu', 'path': '/report/advanced', 'icon': 'bi-clipboard-data', 'sort': 5, 'parent_name': '统计报表', 'code': 'report.advanced'},
        # 8. 系统管理
        {'name': '系统管理', 'type': 'catalog', 'icon': 'bi-gear-wide-connected', 'sort': 7, 'code': ''},
        {'name': '用户管理', 'type': 'menu', 'path': '/admin/users', 'icon': 'bi-person', 'sort': 1, 'parent_name': '系统管理', 'code': 'admin.users'},
        {'name': '部门管理', 'type': 'menu', 'path': '/system/depts', 'icon': 'bi-building', 'sort': 2, 'parent_name': '系统管理', 'code': 'system.depts'},
        {'name': '角色管理', 'type': 'menu', 'path': '/system/roles', 'icon': 'bi-shield', 'sort': 3, 'parent_name': '系统管理', 'code': 'system.roles'},
        {'name': '菜单管理', 'type': 'menu', 'path': '/system/menus', 'icon': 'bi-menu-button-wide', 'sort': 4, 'parent_name': '系统管理', 'code': 'system.menus'},
        {'name': '基础信息管理', 'type': 'menu', 'path': '/dict', 'icon': 'bi-book', 'sort': 5, 'parent_name': '系统管理', 'code': 'dict_mgr.index'},
        {'name': '审批流程管理', 'type': 'menu', 'path': '/approval/flows', 'icon': 'bi-flowchart', 'sort': 6, 'parent_name': '系统管理', 'code': 'approval.flows'},
        {'name': '操作日志', 'type': 'menu', 'path': '/admin/audit_logs', 'icon': 'bi-file-earmark-text', 'sort': 7, 'parent_name': '系统管理', 'code': 'admin.audit_logs'},
        {'name': '登录日志', 'type': 'menu', 'path': '/admin/login_logs', 'icon': 'bi-box-arrow-in-right', 'sort': 8, 'parent_name': '系统管理', 'code': 'admin.login_logs'},
        {'name': '在线用户', 'type': 'menu', 'path': '/admin/online_users', 'icon': 'bi-people-fill', 'sort': 9, 'parent_name': '系统管理', 'code': 'admin.online_users'},
        {'name': '公告管理', 'type': 'menu', 'path': '/admin/announcements', 'icon': 'bi-megaphone', 'sort': 10, 'parent_name': '系统管理', 'code': 'admin.announcements'},
        {'name': '数据库迁移', 'type': 'menu', 'path': '/admin/db_migrations', 'icon': 'bi-database-gear', 'sort': 11, 'parent_name': '系统管理', 'code': 'admin.db_migrations'},
        {'name': '数据回收站', 'type': 'menu', 'path': '/admin/recycle_bin', 'icon': 'bi-trash', 'sort': 12, 'parent_name': '系统管理', 'code': 'admin.recycle_bin'},
        {'name': '项目归档', 'type': 'menu', 'path': '/admin/archive', 'icon': 'bi-archive', 'sort': 13, 'parent_name': '系统管理', 'code': 'admin.archive_index'},
        {'name': '通知配置', 'type': 'menu', 'path': '/admin/notify_config', 'icon': 'bi-bell', 'sort': 14, 'parent_name': '系统管理', 'code': 'admin.notify_config'},
        {'name': '数据备份', 'type': 'menu', 'path': '/admin/backup', 'icon': 'bi-database-down', 'sort': 15, 'parent_name': '系统管理', 'code': 'admin.backup_index'},
        {'name': '系统配置', 'type': 'menu', 'path': '/admin/config', 'icon': 'bi-sliders', 'sort': 16, 'parent_name': '系统管理', 'code': 'admin.config_list'},
        {'name': 'AI配置', 'type': 'menu', 'path': '/ai/config', 'icon': 'bi-robot', 'sort': 17, 'parent_name': '系统管理', 'code': 'ai.config'},
        {'name': 'AI调用日志', 'type': 'menu', 'path': '/ai/logs', 'icon': 'bi-clock-history', 'sort': 18, 'parent_name': '系统管理', 'code': 'ai.logs'},
        # 9. 帮助中心
        {'name': '帮助中心', 'type': 'catalog', 'icon': 'bi-question-circle', 'sort': 8, 'code': ''},
        {'name': '使用说明', 'type': 'menu', 'path': '/help', 'icon': 'bi-book-open', 'sort': 1, 'parent_name': '帮助中心', 'code': 'help.index'},
    ]

    # 检查菜单结构是否匹配，不匹配则重建
    catalog_names = [m['name'] for m in menus_data if m['type'] == 'catalog']
    existing_catalogs = [m.menu_name for m in SysMenu.query.filter_by(menu_type='catalog', parent_id=0).all()]
    need_rebuild = set(catalog_names) != set(existing_catalogs)

    if need_rebuild:
        # 清空菜单和角色菜单关联表
        SysRoleMenu.query.delete()
        SysMenu.query.delete()
        db.session.flush()
        # 重新插入，先建立名称到ID的映射
        name_to_id = {}
        for i, data in enumerate(menus_data):
            if 'parent_name' in data:
                parent_id = name_to_id.get(data['parent_name'], 0)
            else:
                parent_id = 0
            menu = SysMenu(
                menu_name=data['name'],
                menu_code=data.get('code') or None,
                menu_type=data['type'],
                parent_id=parent_id,
                path=data.get('path'),
                icon=data.get('icon'),
                sort=data['sort']
            )
            db.session.add(menu)
            db.session.flush()
            data['id'] = menu.id
            name_to_id[data['name']] = menu.id
            menus_data[i] = data
    else:
        # 结构匹配，检查并补充缺失的子菜单
        for i, data in enumerate(menus_data):
            if 'parent_name' in data:
                parent = SysMenu.query.filter_by(menu_name=data['parent_name'], menu_type='catalog').first()
                parent_id = parent.id if parent else 0
            else:
                parent_id = 0

            existing = SysMenu.query.filter_by(menu_name=data['name'], parent_id=parent_id).first()
            if existing:
                if not existing.menu_code and data.get('code'):
                    existing.menu_code = data['code']
                if not existing.icon and data.get('icon'):
                    existing.icon = data['icon']
                if not existing.path and data.get('path'):
                    existing.path = data['path']
                data['id'] = existing.id
            else:
                menu = SysMenu(
                    menu_name=data['name'],
                    menu_code=data.get('code') or None,
                    menu_type=data['type'],
                    parent_id=parent_id,
                    path=data.get('path'),
                    icon=data.get('icon'),
                    sort=data['sort']
                )
                db.session.add(menu)
                db.session.flush()
                data['id'] = menu.id
            menus_data[i] = data
    
    # 超级管理员拥有所有权限
    super_admin_role = SysRole.query.filter_by(role_code='super_admin').first()
    if super_admin_role:
        existing_perms = set([rm.menu_id for rm in SysRoleMenu.query.filter_by(role_id=super_admin_role.id).all()])
        all_menu_ids = [m.id for m in SysMenu.query.all()]
        for menu_id in all_menu_ids:
            if menu_id not in existing_perms:
                rp = SysRoleMenu(role_id=super_admin_role.id, menu_id=menu_id)
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
        from app.models import Project
        from flask_login import current_user
        from flask import session
        projects = []
        approval_pending_count = 0
        if current_user.is_authenticated:
            projects = Project.query.filter_by(is_archived=False).order_by(Project.created_at.desc()).all()
            try:
                from app.approval.service import get_pending_count
                approval_pending_count = get_pending_count(current_user.id)
            except Exception:
                pass
        return dict(all_projects=projects, current_project_id=session.get('current_project_id'),
                    approval_pending_count=approval_pending_count)

    # 注册模板全局函数: has_perm 用于按钮级权限校验
    @app.context_processor
    def inject_permission_helper():
        from flask_login import current_user
        def has_perm(permission):
            if not current_user.is_authenticated:
                return False
            try:
                return current_user.has_permission(permission)
            except Exception:
                return False
        return dict(has_perm=has_perm)

    # 注册模板全局函数
    from app.utils import num_to_chinese, get_dict_items, get_config, format_file_size
    app.jinja_env.globals['num_to_chinese'] = num_to_chinese
    app.jinja_env.globals['get_dict_items'] = get_dict_items
    app.jinja_env.globals['get_config'] = get_config
    app.jinja_env.globals['format_file_size'] = format_file_size

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

    def get_menu_groups():
        """根据当前用户权限、模块开关和菜单状态返回可见菜单分组"""
        from flask_login import current_user
        from app.models import SysMenu, SysRoleMenu
        # 查询所有禁用的菜单 code
        disabled_endpoints = set()
        # 查询当前用户有权限的菜单 code 集合
        allowed_endpoints = set()
        try:
            for m in SysMenu.query.filter_by(status=False).all():
                if m.menu_code:
                    disabled_endpoints.add(m.menu_code)
            # 非管理员需按角色权限过滤菜单
            if current_user.is_authenticated and not current_user.is_admin():
                # 查询角色关联的菜单 code
                role_menus = db.session.query(SysMenu.menu_code).join(
                    SysRoleMenu, SysRoleMenu.menu_id == SysMenu.id
                ).filter(
                    SysRoleMenu.role_id == current_user.role_id,
                    SysMenu.menu_code.isnot(None)
                ).all()
                allowed_endpoints = {row[0] for row in role_menus if row[0]}
        except Exception:
            pass

        result = []
        for group in _menu_config.get('groups', []):
            # 模块开关过滤
            module_key = group.get('module')
            if module_key:
                enabled = get_config(module_key, 'true')
                if str(enabled).lower() != 'true':
                    continue
            # 管理员权限过滤
            if group.get('require_admin') and (not current_user.is_authenticated or not current_user.is_admin()):
                continue
            # 过滤菜单项：排除禁用菜单,并按角色权限过滤
            items = []
            for item in group.get('items', []):
                endpoint = item.get('endpoint')
                if endpoint in disabled_endpoints:
                    continue
                # 非管理员需检查菜单权限
                if current_user.is_authenticated and not current_user.is_admin():
                    if endpoint and allowed_endpoints and endpoint not in allowed_endpoints:
                        continue
                items.append(item)
            if not items:
                continue
            result.append({**group, 'items': items})
        return result

    app.jinja_env.globals['get_menu_groups'] = get_menu_groups
    app.jinja_env.globals['_menu_config'] = _menu_config

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

    # 拦截禁用菜单的访问 & 更新用户活跃时间 & 初始化向导
    @app.before_request
    def check_menu_status():
        """检查请求的端点是否对应已禁用的菜单，并更新用户最后活跃时间"""
        from flask import request, abort, session, redirect, url_for
        from flask_login import current_user
        from app.models import SysMenu, LoginLog
        from app import db as _db
        from datetime import datetime, timedelta

        # 检查是否需要初始化向导
        endpoint = request.endpoint
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
            now = datetime.utcnow()
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

        if not endpoint:
            return
        # 静态资源、认证等不过滤
        if endpoint.startswith(('static', 'auth.', 'main.set_project', 'main.index', 'main.wizard')):
            return
        try:
            menu = SysMenu.query.filter_by(menu_code=endpoint, status=False).first()
            if menu:
                abort(403)
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
                error_time=datetime.utcnow(),
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
