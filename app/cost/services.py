# app/cost/services.py
"""M4 全面预算管控服务（strangler 模式：仅挂钩，绝不侵入旧业务）。

设计要点（对齐 M4 设计文档 §3.3 / §5）：
- 开支记账：在 StockOut 审批通过 / EquipmentRentSettle 确认结算 /
  SubcontractDeduction 确认扣款 处调用 `record_spend`（经 `safe_record_spend` 包装）。
- 非阻塞（默认）：即使超预算也如实记账，仅在返回中标记 status=exceed / blocked。
  硬拦截（"无预算不开支"）由配置 BUDGET_STRICT_MODE 控制，默认关闭以保生产稳定；
  开启后旧路由可据返回值拦截（record_spend 已返回 blocked 标志，钩子处预留接入）。
- 幂等：同一 (ref_type, ref_id) 仅记一次，避免重复审批/重复确认导致重复记账。
- 预算调整单：审批通过后回写 budget_control.annual_amount（提级审批机制）。
"""
from datetime import datetime
from flask import current_app
from flask_login import current_user

from app import db
from app.cost.models import (BudgetControl, BudgetControlDetail, BudgetAdjustment)


# 预算科目中文名（与 cost_categories.cost_type 对齐）
CATEGORY_LABELS = {
    'material': '材料费',
    'equipment': '设备费',
    'subcontract': '分包费',
    'other': '其他直接费',
}

# 超预算容差：允许超过年度预算的比例（0 = 不允许任何超出）。超出此比例即视为硬超支。
OVERSPEND_TOLERANCE = 0.0


class BudgetBlocked(Exception):
    """严格模式下预算不足时抛出（由调用方决定是否拦截主流程）。"""


class BudgetService:
    """全面预算管控领域服务。"""

    @staticmethod
    def strict_mode():
        return bool(current_app.config.get('BUDGET_STRICT_MODE', False))

    # ---------------------------------------------------------- 控制行
    @staticmethod
    def get_or_create_control(project_id, category_code, fiscal_year=None):
        """取（或懒创建）某项目某科目某年度的预算控制行。

        懒创建时 annual_amount=0，使"未设预算却有开支"在管控看板上呈现为超支(红)，
        既不阻断既有业务，又如实暴露风险。
        """
        fy = fiscal_year or datetime.now().year
        ctrl = BudgetControl.query.filter_by(
            project_id=project_id, category_code=category_code, fiscal_year=fy
        ).first()
        if ctrl is None:
            ctrl = BudgetControl(
                project_id=project_id,
                category_code=category_code,
                fiscal_year=fy,
                annual_amount=0,
                used_amount=0,
                frozen_amount=0,
                warn_threshold=0.8,
                status=True,
            )
            db.session.add(ctrl)
            db.session.flush()
        return ctrl

    # ---------------------------------------------------------- 开支记账
    @staticmethod
    def record_spend(project_id, category_code, amount, ref_type, ref_id, operator=None):
        """记录一笔开支并记账到预算控制（事务内 flush，不自行 commit）。

        返回 dict：{'allowed', 'status'(skip/dup/ok/warn/exceed), 'blocked', ...}
        - amount<=0     → skip
        - 同 (ref_type,ref_id) 已记 → dup（幂等）
        - 否则记账：used_amount += amount，插入 budget_control_detail。
        - 超(年度×(1+容差)) → status='exceed', blocked=True（strict 模式供拦截判断）
        - 达预警线(>=warn_threshold) → status='warn'
        """
        amount = float(amount or 0)
        if amount <= 0:
            return {'allowed': True, 'status': 'skip', 'reason': 'amount<=0'}

        # 幂等：已记过该笔，跳过
        existed = BudgetControlDetail.query.filter_by(ref_type=ref_type, ref_id=ref_id).first()
        if existed:
            return {'allowed': True, 'status': 'dup', 'detail_id': existed.id}

        ctrl = BudgetService.get_or_create_control(project_id, category_code)
        annual = float(ctrl.annual_amount or 0)
        used_after = float(ctrl.used_amount or 0) + amount

        if annual > 0:
            rate = round(used_after / annual, 4)
        else:
            rate = 1.0

        if annual <= 0 or rate > 1.0 + OVERSPEND_TOLERANCE:
            status = 'exceed'
            blocked = annual > 0  # 有预算但超了 → 触发提级；无预算亦属超支
        elif rate >= float(ctrl.warn_threshold or 0.8):
            status = 'warn'
            blocked = False
        else:
            status = 'ok'
            blocked = False

        detail = BudgetControlDetail(
            control_id=ctrl.id, ref_type=ref_type, ref_id=ref_id,
            amount=amount, operator=operator,
        )
        db.session.add(detail)
        ctrl.used_amount = used_after
        db.session.flush()

        return {
            'allowed': True, 'status': status, 'blocked': blocked,
            'detail_id': detail.id, 'used_after': round(used_after, 2),
            'annual': round(annual, 2), 'rate': rate,
            'category': category_code, 'operator': operator,
        }

    # ---------------------------------------------------------- 预算调整单
    @staticmethod
    def request_adjustment(control_id, new_amount, reason, applicant_id=None):
        """发起预算调整单（pending，待审批）。"""
        ctrl = BudgetControl.query.get_or_404(control_id)
        adj = BudgetAdjustment(
            project_id=ctrl.project_id,
            control_id=ctrl.id,
            category_code=ctrl.category_code,
            old_amount=float(ctrl.annual_amount or 0),
            new_amount=float(new_amount or 0),
            reason=reason,
            approval_status='pending',
            applicant_id=applicant_id,
        )
        db.session.add(adj)
        db.session.flush()
        return adj

    @staticmethod
    def approve_adjustment(adj_id):
        """审批通过：回写 budget_control.annual_amount。"""
        adj = BudgetAdjustment.query.get_or_404(adj_id)
        if adj.approval_status not in ('pending', 'draft'):
            raise ValueError('调整单当前状态不可审批：%s' % adj.approval_status)
        ctrl = BudgetControl.query.get(adj.control_id)
        if ctrl:
            ctrl.annual_amount = adj.new_amount
        adj.approval_status = 'approved'
        db.session.flush()
        return adj

    @staticmethod
    def reject_adjustment(adj_id):
        adj = BudgetAdjustment.query.get_or_404(adj_id)
        if adj.approval_status not in ('pending', 'draft'):
            raise ValueError('调整单当前状态不可驳回：%s' % adj.approval_status)
        adj.approval_status = 'rejected'
        db.session.flush()
        return adj

    # ---------------------------------------------------------- 管控看板
    @staticmethod
    def control_board(project_id, fiscal_year=None):
        """汇总某项目某年度的预算管控看板数据。"""
        fy = fiscal_year or datetime.now().year
        ctrls = BudgetControl.query.filter_by(project_id=project_id, fiscal_year=fy).all()
        rows = []
        for c in ctrls:
            annual = float(c.annual_amount or 0)
            used = float(c.used_amount or 0)
            frozen = float(c.frozen_amount or 0)
            available = round(annual - used - frozen, 2)
            if annual > 0:
                rate = round((used + frozen) / annual, 4)
            else:
                rate = 1.0
            if annual <= 0:
                status = 'exceed'
            elif rate > 1.0:
                status = 'exceed'
            elif rate >= float(c.warn_threshold or 0.8):
                status = 'warn'
            else:
                status = 'ok'
            rows.append({
                'ctrl': c,
                'label': CATEGORY_LABELS.get(c.category_code, c.category_code),
                'category_code': c.category_code,
                'annual': annual,
                'used': used,
                'frozen': frozen,
                'available': available,
                'rate': rate,
                'status': status,
                'details': c.details.order_by(BudgetControlDetail.created_at.desc()).limit(20).all(),
            })
        return rows


def safe_record_spend(project_id, category_code, amount, ref_type, ref_id, operator=None):
    """包装版记账：任何异常都不向外抛出，保证不阻断主业务流程。

    返回 record_spend 的 dict；异常时返回 {'allowed': True, 'status': 'error'}。
    """
    try:
        if operator is None and current_user.is_authenticated:
            operator = getattr(current_user, 'username', None) or getattr(current_user, 'name', None)
        return BudgetService.record_spend(
            project_id, category_code, amount, ref_type, ref_id, operator=operator)
    except Exception as e:
        try:
            current_app.logger.warning('[BudgetService] 开支记账失败(不影响主流程): %s' % e)
        except Exception:
            pass
        return {'allowed': True, 'status': 'error', 'reason': str(e)}
