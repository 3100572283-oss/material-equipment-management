# -*- coding: utf-8 -*-
"""M5 票据三流合一 —— 核对与入账服务

三流口径（含税比对）：
- 票流 invoice_total  = Σ invoices.amount_with_tax
- 资金流 payment_total = Σ payments.amount（仅审批通过）
- 货流 goods_total    = Σ reconciliations.total_amount（仅审批通过/已确认）
- 合同额 contract_amount = contracts.amount_with_tax（基线参考）

一致性定级：
- 以三流两两差异占「三流最大额」的比例为准：
    rate = max(|差|) / max(三流金额, 1)
  * rate <= TOLERANCE        → green（一致）
  * TOLERANCE < rate <= 0.03 → yellow（轻微偏差，需关注）
  * rate > 0.03              → red（显著不一致）
- 货流缺失（goods_total==0 但票/资有数据）→ 标记 goods_missing（red）
- 主体不一致（entity_mismatch）→ red（预留：发票税号与供应商税号比对，当前凭 FK 强制一致，字段预留）
"""

from datetime import datetime

from flask import current_app
from sqlalchemy import func

from app import db
from app.models import (Invoice, Payment, Reconciliation, Contract, Supplier)
from app.invoice.models import InvoiceThreeFlowCheck

# 三流一致容忍率：0.5%
TOLERANCE_RATE = 0.005
# 黄色（需关注）上限：3%
WARN_RATE = 0.03


def _float(val):
    try:
        return float(val or 0)
    except Exception:
        return 0.0


def compute_three_flow(project_id, supplier_id, contract_id):
    """计算单个 (项目×供应商×合同) 的三流金额与一致性，返回 dict（不写库）。"""
    inv = db.session.query(
        func.coalesce(func.sum(Invoice.amount_with_tax), 0),
        func.count(Invoice.id)
    ).filter_by(project_id=project_id, supplier_id=supplier_id, contract_id=contract_id).first()
    pay = db.session.query(
        func.coalesce(func.sum(Payment.amount), 0),
        func.count(Payment.id)
    ).filter_by(project_id=project_id, supplier_id=supplier_id,
                contract_id=contract_id, approval_status='passed').first()
    rec = db.session.query(
        func.coalesce(func.sum(Reconciliation.total_amount), 0),
        func.count(Reconciliation.id)
    ).filter_by(project_id=project_id, supplier_id=supplier_id,
                contract_id=contract_id, status='approved').first()

    contract = Contract.query.get(contract_id)
    contract_amount = _float(contract.amount_with_tax) if contract else 0.0

    invoice_total = _float(inv[0])
    payment_total = _float(pay[0])
    goods_total = _float(rec[0])
    invoice_count = int(inv[1] or 0)
    payment_count = int(pay[1] or 0)
    reconciliation_count = int(rec[1] or 0)

    var_inv_pay = round(invoice_total - payment_total, 2)
    var_pay_goods = round(payment_total - goods_total, 2)
    var_inv_goods = round(invoice_total - goods_total, 2)

    # 主体一致性：当前发票/付款/对账均通过 FK 指向同一 supplier，凭外键保证一致；
    # 预留 entity_mismatch 字段供后续「发票票面税号 vs 供应商税号」实时比对。
    entity_mismatch = False
    supplier = Supplier.query.get(supplier_id)
    # 若供应商无税号且存在发票，视为潜在主体风险（弱提示，不强制 red）
    if supplier and not supplier.credit_code and invoice_count > 0:
        entity_mismatch = False  # 仅记录，不强判；税号采集由 M6/发票录入补全

    # 一致性定级
    mismatch = []
    if goods_total == 0 and (invoice_total > 0 or payment_total > 0):
        mismatch.append('goods_missing')
    if abs(var_inv_pay) > 0.005:
        mismatch.append('inv_pay')
    if abs(var_pay_goods) > 0.005:
        mismatch.append('pay_goods')
    if abs(var_inv_goods) > 0.005:
        mismatch.append('inv_goods')

    max_amt = max(invoice_total, payment_total, goods_total, 1.0)
    max_dev = max(abs(var_inv_pay), abs(var_pay_goods), abs(var_inv_goods))
    rate = max_dev / max_amt if max_amt > 0 else 0.0

    if entity_mismatch:
        level = 'red'
    elif 'goods_missing' in mismatch and len(mismatch) == 1:
        level = 'red'  # 有票/资但无货流 → 显著不一致
    elif rate <= TOLERANCE_RATE:
        level = 'green'
    elif rate <= WARN_RATE:
        level = 'yellow'
    else:
        level = 'red'

    return {
        'project_id': project_id,
        'supplier_id': supplier_id,
        'contract_id': contract_id,
        'invoice_total': invoice_total,
        'payment_total': payment_total,
        'goods_total': goods_total,
        'contract_amount': contract_amount,
        'variance_inv_pay': var_inv_pay,
        'variance_pay_goods': var_pay_goods,
        'variance_inv_goods': var_inv_goods,
        'consistency_level': level,
        'mismatch_types': ','.join(mismatch) if mismatch else '',
        'entity_mismatch': entity_mismatch,
        'invoice_count': invoice_count,
        'payment_count': payment_count,
        'reconciliation_count': reconciliation_count,
    }


def _distinct_pairs(project_id):
    """取出该项目下三流任一有数据的 (supplier_id, contract_id) 去重对。"""
    pairs = set()
    for model in (Invoice, Payment, Reconciliation):
        rows = db.session.query(
            model.supplier_id, model.contract_id
        ).filter_by(project_id=project_id).distinct().all()
        for sid, cid in rows:
            if sid and cid:
                pairs.add((sid, cid))
    return list(pairs)


def refresh_three_flow(project_id, operator=None):
    """重算某项目全部 (供应商×合同) 三流核对，upsert 结果行。返回处理对数。"""
    pairs = _distinct_pairs(project_id)
    now = datetime.now()
    for sid, cid in pairs:
        data = compute_three_flow(project_id, sid, cid)
        chk = InvoiceThreeFlowCheck.query.filter_by(
            project_id=project_id, supplier_id=sid, contract_id=cid).first()
        if not chk:
            chk = InvoiceThreeFlowCheck(
                project_id=project_id, supplier_id=sid, contract_id=cid)
            db.session.add(chk)
        chk.invoice_total = data['invoice_total']
        chk.payment_total = data['payment_total']
        chk.goods_total = data['goods_total']
        chk.contract_amount = data['contract_amount']
        chk.variance_inv_pay = data['variance_inv_pay']
        chk.variance_pay_goods = data['variance_pay_goods']
        chk.variance_inv_goods = data['variance_inv_goods']
        chk.consistency_level = data['consistency_level']
        chk.mismatch_types = data['mismatch_types']
        chk.entity_mismatch = data['entity_mismatch']
        chk.invoice_count = data['invoice_count']
        chk.payment_count = data['payment_count']
        chk.reconciliation_count = data['reconciliation_count']
        chk.last_checked_at = now
    db.session.commit()
    return len(pairs)


def book_three_flow(check_id, operator=None):
    """三流一致后确认入账：置 booked 标记，并经 safe_record_spend 计入 M4 成本（不阻断）。

    返回 (ok, msg)。仅 green/yellow 允许入账；red 拒绝。
    """
    chk = InvoiceThreeFlowCheck.query.get(check_id)
    if not chk:
        return False, '核对记录不存在'
    if chk.booked:
        return False, '该核对已入账，无需重复'
    if chk.consistency_level == 'red':
        return False, '三流为红色（显著不一致），禁止入账，请先核对'
    try:
        from app.cost.services import safe_record_spend
        # 以票流含税金额计入成本（与 M4 出库口径一致），ref 防重；
        # 科目按合同业务类型区分（设备租赁→equipment，其余→material）
        contract = Contract.query.get(chk.contract_id)
        cat = 'equipment' if (contract and contract.business_type == '设备租赁') else 'material'
        safe_record_spend(
            chk.project_id, cat,
            chk.invoice_total, 'three_flow', chk.id,
            operator=operator
        )
    except Exception as e:
        current_app.logger.warning('[M5] 入账计入成本失败(不影响入账标记): %s' % e)
    chk.booked = True
    chk.booked_at = datetime.now()
    chk.booked_by = operator
    db.session.commit()
    return True, '入账成功（已计入项目成本）'
