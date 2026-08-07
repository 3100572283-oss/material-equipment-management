# -*- coding: utf-8 -*-
"""M5 票据三流合一 —— 数据模型

三流定义（建筑工程行业税务合规口径）：
- 票流：发票（invoices）——销售方就供货/服务向本单位开具的增值税发票
- 资金流：付款（payments）——本单位向销售方支付的款项
- 货流：合同履约确认金额（reconciliations，已审批对账额，含税）——已确认供货/工程量

以「项目 × 供应商 × 合同」为核对单元，比对三流金额一致性，并标记主体（税号）一致性。
"""

from datetime import datetime

from app import db


class InvoiceThreeFlowCheck(db.Model):
    """三流合一核对结果（项目 × 供应商 × 合同 一行）"""

    __tablename__ = 'invoice_three_flow_check'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False, index=True)
    contract_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=False, index=True)

    # 三流金额（含税，单位元）
    invoice_total = db.Column(db.Numeric(18, 2), default=0)   # 票流
    payment_total = db.Column(db.Numeric(18, 2), default=0)    # 资金流
    goods_total = db.Column(db.Numeric(18, 2), default=0)      # 货流（已审批对账额）
    contract_amount = db.Column(db.Numeric(18, 2), default=0)  # 合同额（含税，基线参考）

    # 三向差异（含税）
    variance_inv_pay = db.Column(db.Numeric(18, 2), default=0)    # 票流 - 资金流
    variance_pay_goods = db.Column(db.Numeric(18, 2), default=0)  # 资金流 - 货流
    variance_inv_goods = db.Column(db.Numeric(18, 2), default=0)  # 票流 - 货流

    # 一致性判定
    consistency_level = db.Column(db.String(8), default='red')  # green / yellow / red
    mismatch_types = db.Column(db.String(128), nullable=True)   # 逗号分隔: inv_pay,pay_goods,inv_goods,goods_missing,entity_mismatch
    entity_mismatch = db.Column(db.Boolean, default=False)      # 票/资/货 主体（税号）不一致

    # 明细计数
    invoice_count = db.Column(db.Integer, default=0)
    payment_count = db.Column(db.Integer, default=0)
    reconciliation_count = db.Column(db.Integer, default=0)

    # 入账（三流一致后确认计入成本）
    booked = db.Column(db.Boolean, default=False)
    booked_at = db.Column(db.DateTime, nullable=True)
    booked_by = db.Column(db.String(64), nullable=True)

    last_checked_at = db.Column(db.DateTime, default=datetime.now)
    remark = db.Column(db.Text, nullable=True)

    supplier = db.relationship('Supplier', backref=db.backref('three_flow_checks', lazy='dynamic'))
    contract = db.relationship('Contract', backref=db.backref('three_flow_checks', lazy='dynamic'))

    def __repr__(self):
        return '<InvoiceThreeFlowCheck p%s s%s c%s %s>' % (
            self.project_id, self.supplier_id, self.contract_id, self.consistency_level)
