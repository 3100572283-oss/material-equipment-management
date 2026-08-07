# -*- coding: utf-8 -*-
"""独立数据可视化大屏 —— 跨模块聚合服务。

整合成本核算(M4) / 分包商核验(M6) / 票据三流合一(M5) / 物资设备(V7.3 主数据)
的关键运营指标，供大屏终端按项目（或全局）聚合展示。只读、不写。
"""
from datetime import datetime
from sqlalchemy import func

from app import db
from app.models import (Project, Equipment, Material, Supplier,
                        Invoice, Payment, Reconciliation)
from app.cost.models import CostProfitAnalysis, BudgetControl
from app.subcontractor.models import SubcontractorProfile
from app.invoice.models import InvoiceThreeFlowCheck
from app.cost.services import CATEGORY_LABELS


def _cost_block(project_id, period):
    """成本聚合（支持单项目或全局 None），复用成本模型。"""
    q = CostProfitAnalysis.query.filter_by(period_month=period)
    if project_id:
        q = q.filter_by(project_id=project_id)
    rows = q.all()

    budget_total = sum(float(r.budget_amount or 0) for r in rows)
    actual_total = sum(float(r.actual_amount or 0) for r in rows)
    variance_total = round(budget_total - actual_total, 2)
    variance_rate = round(variance_total / budget_total, 4) if budget_total > 0 else 0.0

    level_counts = {'green': 0, 'yellow': 0, 'red': 0}
    for r in rows:
        level_counts[r.warn_level] = level_counts.get(r.warn_level, 0) + 1

    ctrl_q = BudgetControl.query
    if project_id:
        ctrl_q = ctrl_q.filter_by(project_id=project_id)
    ctrls = ctrl_q.all()
    used = sum(float(c.used_amount or 0) for c in ctrls)
    annual = sum(float(c.annual_amount or 0) for c in ctrls)
    exec_rate = round(used / annual, 4) if annual > 0 else 0.0

    categories = [{
        'category': r.category_code,
        'label': CATEGORY_LABELS.get(r.category_code, r.category_code),
        'budget': float(r.budget_amount or 0),
        'actual': float(r.actual_amount or 0),
        'variance': float(r.variance or 0),
        'rate': float(r.variance_rate or 0),
        'level': r.warn_level,
    } for r in rows]

    controls = []
    for c in ctrls:
        a = float(c.annual_amount or 0)
        u = float(c.used_amount or 0)
        rate = round(u / a, 4) if a > 0 else 1.0
        status = 'exceed' if (a <= 0 or rate > 1.0) else (
            'warn' if rate >= float(c.warn_threshold or 0.8) else 'ok')
        controls.append({
            'category': c.category_code,
            'label': CATEGORY_LABELS.get(c.category_code, c.category_code),
            'annual': round(a, 2), 'used': round(u, 2),
            'rate': rate, 'status': status,
        })

    return {
        'period': period,
        'budget_total': round(budget_total, 2),
        'actual_total': round(actual_total, 2),
        'variance_total': variance_total,
        'variance_rate': variance_rate,
        'warn_levels': level_counts,
        'budget_annual': round(annual, 2),
        'budget_used': round(used, 2),
        'budget_exec_rate': exec_rate,
        'categories': categories,
        'controls': controls,
    }


def aggregate_bigscreen(project_id=None, period_month=None):
    """跨模块大屏聚合。project_id=None 表示全局（仅超管可读）。"""
    period = period_month or datetime.now().strftime('%Y-%m')
    cost = _cost_block(project_id, period)

    # —— 分包商核验 ——
    sc_q = SubcontractorProfile.query
    if project_id:
        sc_q = sc_q.filter_by(project_id=project_id)
    sc_total = sc_q.count()
    sc_pending = sc_q.filter_by(admit_status='pending').count()
    sc_approved = sc_q.filter_by(admit_status='approved').count()
    sc_rejected = sc_q.filter_by(admit_status='rejected').count()
    sc_hit = sc_q.filter_by(blacklist_hit=True).count()

    # —— 票据三流合一 ——
    tf_q = InvoiceThreeFlowCheck.query
    if project_id:
        tf_q = tf_q.filter_by(project_id=project_id)
    tf_total = tf_q.count()
    tf_green = tf_q.filter_by(consistency_level='green').count()
    tf_yellow = tf_q.filter_by(consistency_level='yellow').count()
    tf_red = tf_q.filter_by(consistency_level='red').count()
    tf_booked = tf_q.filter_by(booked=True).count()

    # —— 设备 ——
    eq_status_q = db.session.query(Equipment.status, func.count(Equipment.id))
    eq_total_q = Equipment.query
    if project_id:
        eq_status_q = eq_status_q.filter(Equipment.project_id == project_id)
        eq_total_q = eq_total_q.filter_by(project_id=project_id)
    eq_by_status = {s: c for s, c in eq_status_q.group_by(Equipment.status).all()}
    eq_total = eq_total_q.count()

    # —— 物资 / 供应商 ——
    mat_q = Material.query.filter_by(is_deleted=False)
    sup_q = Supplier.query.filter_by(is_deleted=False)
    if project_id:
        mat_q = mat_q.filter_by(project_id=project_id)
        sup_q = sup_q.filter_by(project_id=project_id)
    mat_total = mat_q.count()
    mat_active = mat_q.filter_by(status='active').count()
    sup_total = sup_q.count()

    # —— 票 / 资 / 货 单据计数 ——
    if project_id:
        inv_count = Invoice.query.filter_by(project_id=project_id).count()
        pay_count = Payment.query.filter_by(project_id=project_id,
                                            approval_status='passed').count()
        rec_count = Reconciliation.query.filter_by(project_id=project_id,
                                                  status='approved').count()
    else:
        inv_count = Invoice.query.count()
        pay_count = Payment.query.filter_by(approval_status='passed').count()
        rec_count = Reconciliation.query.filter_by(status='approved').count()

    # —— 项目信息 ——
    proj = Project.query.get(project_id) if project_id else None
    project_name = proj.name if proj else '全部项目'

    # —— 待办（红黄科目 + 待审批准入 + 待入账红票）——
    todos = []
    for c in cost['categories']:
        if c['level'] in ('red', 'yellow'):
            todos.append({
                'type': 'cost', 'level': c['level'],
                'text': '%s 盈亏%s：计划 %.0f / 实际 %.0f' % (
                    c['label'], '红' if c['level'] == 'red' else '黄',
                    c['budget'], c['actual']),
            })
    if sc_pending:
        todos.append({'type': 'subcontractor', 'level': 'yellow',
                      'text': '分包商准入待审批 %d 项' % sc_pending})
    if tf_red:
        todos.append({'type': 'invoice', 'level': 'red',
                      'text': '票据三流不一致（红）%d 项待处理' % tf_red})

    return {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'project_id': project_id,
        'project_name': project_name,
        'period': period,
        'cost': cost,
        'subcontractor': {
            'total': sc_total, 'pending': sc_pending, 'approved': sc_approved,
            'rejected': sc_rejected, 'blacklist_hit': sc_hit,
        },
        'invoice': {
            'total': tf_total, 'green': tf_green, 'yellow': tf_yellow,
            'red': tf_red, 'booked': tf_booked,
        },
        'equipment': {'total': eq_total, 'by_status': eq_by_status},
        'material': {'total': mat_total, 'active': mat_active},
        'supplier': {'total': sup_total},
        'documents': {'invoice': inv_count, 'payment': pay_count,
                      'reconciliation': rec_count},
        'todos': todos,
    }
