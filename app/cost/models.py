# app/cost/models.py
"""M4 成本核算模块数据模型（V7.3 扩展，独立命名空间 cost_*）。

设计依据：阶段二实施计划 + 本地仓库真实表（contracts/contract_items/stock_outs/
reconciliations/equipment_rent_settles/subcontract_deductions/payments/projects）。
所有新表带 project_id，接入 M0 data_scope 行级隔离（沿用物资/设备既有做法）。
仅定义模型，不引用旧权限代码；业务调用经 AuthGateway。
"""
from datetime import datetime
from decimal import Decimal
from app import db


class CostCategory(db.Model):
    """成本科目（对标责任成本科目树：材料费/设备费/分包费/其他直接费…）"""
    __tablename__ = 'cost_categories'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    code = db.Column(db.String(64), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    parent_code = db.Column(db.String(64), nullable=True)
    cost_type = db.Column(db.String(32), default='material')  # material/equipment/subcontract/other
    is_leaf = db.Column(db.Boolean, default=True)
    status = db.Column(db.Boolean, default=True)
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (db.UniqueConstraint('project_id', 'code', name='uq_cost_cat_proj_code'),)


class ResponsibilityCostBudget(db.Model):
    """责任成本预算（按 WBS/工号口径的责任预算分解）"""
    __tablename__ = 'responsibility_cost_budget'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    wbs_code = db.Column(db.String(64), nullable=False)        # 工号/WBS 编码（对齐 work_numbers）
    wbs_name = db.Column(db.String(128), nullable=False)
    category_code = db.Column(db.String(64), nullable=True)    # 关联 cost_categories.code
    budget_amount = db.Column(db.Numeric(18, 2), default=0)    # 责任预算（不含税）
    owner_role = db.Column(db.String(64), nullable=True)       # 责任人岗位(对齐 auth_core_role.role_code)
    source = db.Column(db.String(16), default='manual')        # contract_auto/manual
    status = db.Column(db.String(16), default='active')        # active/locked/closed
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    items = db.relationship('ResponsibilityCostBudgetItem', backref='budget',
                            lazy='dynamic', cascade='all, delete-orphan')


class ResponsibilityCostBudgetItem(db.Model):
    """责任成本预算明细行（由合同/清单自动生成或手工录入）"""
    __tablename__ = 'responsibility_cost_budget_item'
    id = db.Column(db.Integer, primary_key=True)
    budget_id = db.Column(db.Integer, db.ForeignKey('responsibility_cost_budget.id'), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=True)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=True)
    quantity = db.Column(db.Numeric(18, 4), default=0)
    unit_price = db.Column(db.Numeric(18, 4), default=0)
    amount = db.Column(db.Numeric(18, 2), default=0)
    remark = db.Column(db.String(256), nullable=True)


class BudgetControl(db.Model):
    """全面预算管控（按科目+年度控制开支）"""
    __tablename__ = 'budget_control'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    category_code = db.Column(db.String(64), nullable=False)
    fiscal_year = db.Column(db.Integer, nullable=False)
    annual_amount = db.Column(db.Numeric(18, 2), default=0)    # 年度预算
    used_amount = db.Column(db.Numeric(18, 2), default=0)     # 已用
    frozen_amount = db.Column(db.Numeric(18, 2), default=0)   # 审批中冻结
    approver_role = db.Column(db.String(64), nullable=True)   # 超预算提级审批角色
    warn_threshold = db.Column(db.Numeric(5, 2), default=0.8) # 预警线（默认 80%）
    status = db.Column(db.Boolean, default=True)
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    details = db.relationship('BudgetControlDetail', backref='control',
                              lazy='dynamic', cascade='all, delete-orphan')

    __table_args__ = (db.UniqueConstraint('project_id', 'category_code', 'fiscal_year',
                                          name='uq_budget_proj_cat_year'),)

    @property
    def available_amount(self):
        return float(self.annual_amount or 0) - float(self.used_amount or 0) - float(self.frozen_amount or 0)

    @property
    def usage_rate(self):
        annual = float(self.annual_amount or 0)
        if annual <= 0:
            return 1.0
        return round((float(self.used_amount or 0) + float(self.frozen_amount or 0)) / annual, 4)


class BudgetControlDetail(db.Model):
    """预算管控明细（每次开支记账）"""
    __tablename__ = 'budget_control_detail'
    id = db.Column(db.Integer, primary_key=True)
    control_id = db.Column(db.Integer, db.ForeignKey('budget_control.id'), nullable=False)
    ref_type = db.Column(db.String(32), nullable=True)   # stock_out/equipment_settle/subcontract
    ref_id = db.Column(db.Integer, nullable=True)
    amount = db.Column(db.Numeric(18, 2), default=0)
    operator = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class CostProfitAnalysis(db.Model):
    """盈亏穿透分析（物化表，APScheduler 按月刷新）"""
    __tablename__ = 'cost_profit_analysis'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    period_month = db.Column(db.String(7), nullable=False)     # YYYY-MM
    wbs_code = db.Column(db.String(64), nullable=True)
    category_code = db.Column(db.String(64), nullable=True)
    budget_amount = db.Column(db.Numeric(18, 2), default=0)
    actual_amount = db.Column(db.Numeric(18, 2), default=0)
    variance = db.Column(db.Numeric(18, 2), default=0)         # 节超 = 预算 - 实际
    variance_rate = db.Column(db.Numeric(8, 4), default=0)
    warn_level = db.Column(db.String(8), default='green')      # green/yellow/red
    owner_role = db.Column(db.String(64), nullable=True)
    pushed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (db.UniqueConstraint('project_id', 'period_month', 'wbs_code', 'category_code',
                                          name='uq_cost_profit_uniq'),)


class BudgetAdjustment(db.Model):
    """预算调整单（审批后回写 budget_control.annual_amount）"""
    __tablename__ = 'budget_adjustment'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    control_id = db.Column(db.Integer, db.ForeignKey('budget_control.id'), nullable=False)
    category_code = db.Column(db.String(64), nullable=True)
    old_amount = db.Column(db.Numeric(18, 2), default=0)
    new_amount = db.Column(db.Numeric(18, 2), default=0)
    reason = db.Column(db.String(256), nullable=True)
    approval_status = db.Column(db.String(16), default='draft')  # draft/pending/approved/rejected
    applicant_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
