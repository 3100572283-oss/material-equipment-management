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
from sqlalchemy import func

from app import db
from app.cost.models import (BudgetControl, BudgetControlDetail, BudgetAdjustment,
                             CostProfitAnalysis, ResponsibilityCostBudget)


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


# ======================================================================
# P3 盈亏穿透预警（cost_profit_analysis 物化 + 红/黄/绿三级 + 推送）
# ======================================================================

# 参与盈亏分析的四大成本科目（与设计文档 §3.4 对齐）
PROFIT_CATEGORIES = ['material', 'equipment', 'subcontract', 'other']

# 预警阈值：偏差率 = (计划 - 实际) / 计划
#   偏差率 < -5%  → 红（实际超计划 5% 以上）
#   -5% ≤ 偏差率 < 0 → 黄（实际略超计划）
#   偏差率 >= 0   → 绿（未超计划）
WARN_RED_RATE = -0.05


def _actual_cost_by_category(project_id):
    """按项目聚合四大科目实际成本（元）。

    - material  : 物资出库 FIFO 金额（JOIN StockOut 按 project_id 隔离）
    - equipment : 设备租赁结算总额（经 Equipment.project_id 隔离）
    - subcontract: 分包领料扣款总额（直接 project_id）
    - other     : 对账结算不含税合计（直接 project_id）
    """
    from app.models import (StockOut, StockOutItem, Equipment,
                            EquipmentRentSettle, SubcontractDeduction, Reconciliation)

    mat = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
        StockOut, StockOut.id == StockOutItem.stock_out_id
    ).filter(StockOut.project_id == project_id).scalar() or 0

    eq = db.session.query(func.coalesce(func.sum(EquipmentRentSettle.total_amount), 0)).join(
        Equipment, Equipment.id == EquipmentRentSettle.equipment_id
    ).filter(Equipment.project_id == project_id).scalar() or 0

    sub = db.session.query(func.coalesce(func.sum(SubcontractDeduction.total_amount), 0)).filter(
        SubcontractDeduction.project_id == project_id
    ).scalar() or 0

    oth = db.session.query(func.coalesce(func.sum(Reconciliation.total_amount_without_tax), 0)).filter(
        Reconciliation.project_id == project_id
    ).scalar() or 0

    return {
        'material': float(mat),
        'equipment': float(eq),
        'subcontract': float(sub),
        'other': float(oth),
    }


def _warn_level(variance_rate):
    """偏差率 → 预警级别。"""
    if variance_rate < WARN_RED_RATE:
        return 'red'
    if variance_rate < 0:
        return 'yellow'
    return 'green'


def refresh_profit_analysis(project_id=None, period_month=None):
    """刷新盈亏分析物化表（按月、按项目）。

    每个项目每个科目生成一行（wbs_code='*' 表示科目级汇总），聚合：
      计划 = SUM(responsibility_cost_budget.budget_amount WHERE category_code)
      实际 = _actual_cost_by_category 对应科目
      节超 = 计划 - 实际；偏差率 = 节超 / 计划；预警级别 = _warn_level
    幂等：同 (project, period, '*', category) 更新而非新增。
    返回刷新的行数。
    """
    from app.models import Project

    period = period_month or datetime.now().strftime('%Y-%m')
    projects = [Project.query.get(project_id)] if project_id else Project.query.all()
    count = 0
    for proj in projects:
        if not proj:
            continue
        pid = proj.id
        actual = _actual_cost_by_category(pid)

        # 计划成本按科目汇总 + 记录主要责任人岗位
        budget_by_cat = {c: 0.0 for c in PROFIT_CATEGORIES}
        owner_by_cat = {}
        for b in ResponsibilityCostBudget.query.filter_by(project_id=pid).all():
            c = b.category_code or 'other'
            if c not in budget_by_cat:
                budget_by_cat[c] = 0.0
            budget_by_cat[c] += float(b.budget_amount or 0)
            if b.owner_role and c not in owner_by_cat:
                owner_by_cat[c] = b.owner_role

        for c in PROFIT_CATEGORIES:
            bud = round(budget_by_cat.get(c, 0.0), 2)
            act = round(actual.get(c, 0.0), 2)
            variance = round(bud - act, 2)
            rate = round(variance / bud, 4) if bud > 0 else (0.0 if act == 0 else -1.0)
            level = _warn_level(rate)

            row = CostProfitAnalysis.query.filter_by(
                project_id=pid, period_month=period, wbs_code='*', category_code=c
            ).first()
            if row is None:
                row = CostProfitAnalysis(
                    project_id=pid, period_month=period, wbs_code='*', category_code=c)
                db.session.add(row)
            row.budget_amount = bud
            row.actual_amount = act
            row.variance = variance
            row.variance_rate = rate
            row.warn_level = level
            row.owner_role = owner_by_cat.get(c)
            row.pushed_at = None
            count += 1
    db.session.commit()
    return count


def push_profit_warnings(project_id=None, period_month=None):
    """对红/黄级盈亏分析行，按 owner_role 推送站内预警消息。

    返回推送条数。异常被吞掉（不影响主流程）。
    """
    from app.notification_service import send_message, MSG_TYPE_WARNING
    from app.auth_core.models import AuthRole, AuthUserRole

    try:
        period = period_month or datetime.now().strftime('%Y-%m')
        q = CostProfitAnalysis.query.filter_by(period_month=period)
        if project_id:
            q = q.filter_by(project_id=project_id)
        rows = q.filter(CostProfitAnalysis.warn_level.in_(['red', 'yellow'])).all()

        sent = 0
        for r in rows:
            if not r.owner_role:
                continue
            role = AuthRole.query.filter_by(role_code=r.owner_role).first()
            if not role:
                continue
            user_ids = [ur.user_id for ur in AuthUserRole.query.filter_by(role_id=role.id).all()]
            if not user_ids:
                continue
            cat_label = CATEGORY_LABELS.get(r.category_code, r.category_code)
            level_cn = '红' if r.warn_level == 'red' else '黄'
            title = '盈亏预警（%s）' % level_cn
            content = ('项目 %d 的%s在 %s 盈亏预警：计划 %.2f / 实际 %.2f / 节超 %.2f（偏差 %.1f%%）。'
                       % (r.project_id, cat_label, r.period_month,
                          float(r.budget_amount or 0), float(r.actual_amount or 0),
                          float(r.variance or 0), float(r.variance_rate or 0) * 100))
            for uid in user_ids:
                send_message(user_id=uid, msg_type=MSG_TYPE_WARNING, title=title,
                             content=content, biz_type='cost_profit', url='/cost/profit/')
                sent += 1
            r.pushed_at = datetime.now()
        db.session.commit()
        return sent
    except Exception as e:
        try:
            current_app.logger.warning('[ProfitAnalysis] 预警推送失败(不影响主流程): %s' % e)
        except Exception:
            pass
        return 0


def dashboard_aggregate(project_id, period_month=None):
    """大屏/看板聚合接口数据（P4）。

    返回 dict：计划/实际/节超/偏差率、预警三级分布、预算执行率、科目明细、管控看板。
    """
    from app.cost.models import CostProfitAnalysis  # 本模块已导入，保险起见

    period = period_month or datetime.now().strftime('%Y-%m')
    rows = CostProfitAnalysis.query.filter_by(project_id=project_id, period_month=period).all()

    budget_total = sum(float(r.budget_amount or 0) for r in rows)
    actual_total = sum(float(r.actual_amount or 0) for r in rows)
    variance_total = round(budget_total - actual_total, 2)
    variance_rate = round(variance_total / budget_total, 4) if budget_total > 0 else 0.0

    level_counts = {'green': 0, 'yellow': 0, 'red': 0}
    for r in rows:
        level_counts[r.warn_level] = level_counts.get(r.warn_level, 0) + 1

    board = BudgetService.control_board(project_id)
    used = sum(r['used'] for r in board)
    annual = sum(r['annual'] for r in board)
    exec_rate = round(used / annual, 4) if annual > 0 else 0.0

    return {
        'project_id': project_id,
        'period': period,
        'budget_total': round(budget_total, 2),
        'actual_total': round(actual_total, 2),
        'variance_total': variance_total,
        'variance_rate': variance_rate,
        'warn_levels': level_counts,
        'budget_annual': round(annual, 2),
        'budget_used': round(used, 2),
        'budget_exec_rate': exec_rate,
        'categories': [
            {
                'category': r.category_code,
                'label': CATEGORY_LABELS.get(r.category_code, r.category_code),
                'budget': float(r.budget_amount or 0),
                'actual': float(r.actual_amount or 0),
                'variance': float(r.variance or 0),
                'rate': float(r.variance_rate or 0),
                'level': r.warn_level,
            } for r in rows
        ],
        'controls': [
            {
                'category': r['category_code'],
                'label': r['label'],
                'annual': r['annual'],
                'used': r['used'],
                'rate': r['rate'],
                'status': r['status'],
            } for r in board
        ],
    }
