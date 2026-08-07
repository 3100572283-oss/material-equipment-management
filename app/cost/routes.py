# app/cost/routes.py
"""M4 成本核算 — P1 责任成本测算路由。

设计要点（对齐 M0 权限中台与 V7.3 真表）：
- 蓝图名 cost，url_prefix=/cost。
- 项目隔离：UI 级 session.current_project_id + 显式过滤；同时经 M0 全局
  with_loader_criteria（路径 A）自动按 project_id 隔离（cost_* 不在主数据排除清单）。
- 权限：view 路由用 permission_required('cost:budget:view')，写操作 edit 权限。
- 节超聚合：实际成本来源 StockOutItem（无 project_id 列）→ 必须 JOIN StockOut
  按 project_id 过滤（路径 B 安全），不依赖 loader 自动注入。
"""
from flask import (render_template, request, redirect, url_for, flash,
                   jsonify, current_app, session)
from flask_login import login_required, current_user
from datetime import datetime
from sqlalchemy import func, or_

from app import db
from app.cost import cost_bp
from app.cost.models import (ResponsibilityCostBudget, ResponsibilityCostBudgetItem,
                             CostCategory, BudgetControl, BudgetAdjustment)
from app.cost.services import BudgetService, CATEGORY_LABELS
from app.models import Contract, ContractItem, StockOut, StockOutItem, Material, Project
from app.utils import apply_data_scope
from app.decorators import permission_required


# ---------------------------------------------------------------- 工具
def _current_pid():
    """当前选中项目 id；未选则返回 None（调用方负责提示）"""
    return session.get('current_project_id')


def _actual_amount_by_material(project_id):
    """按材料聚合项目实际出库成本（元）。JOIN StockOut 确保按 project_id 隔离。"""
    rows = db.session.query(
        StockOutItem.material_id,
        func.coalesce(func.sum(StockOutItem.amount), 0),
    ).join(StockOut, StockOut.id == StockOutItem.stock_out_id).filter(
        StockOut.project_id == project_id,
    ).group_by(StockOutItem.material_id).all()
    return {mid: float(amt or 0) for mid, amt in rows}


def _compute_budget_variance(budget):
    """计算预算节超：planned=Sum(item.amount)，actual=按材料聚合出库成本。

    返回 dict: planned_total / actual_total / variance_total / items[]
    items[]: {item, actual, variance}
    """
    actual_map = _actual_amount_by_material(budget.project_id)
    items = []
    planned_total = 0.0
    actual_total = 0.0
    for it in budget.items.all():
        planned = float(it.amount or 0)
        actual = float(actual_map.get(it.material_id, 0) or 0)
        items.append({'item': it, 'actual': actual, 'variance': round(planned - actual, 2)})
        planned_total += planned
        actual_total += actual
    return {
        'planned_total': round(planned_total, 2),
        'actual_total': round(actual_total, 2),
        'variance_total': round(planned_total - actual_total, 2),
        'items': items,
    }


def _project_materials(pid):
    """供表单下拉：项目范围内或公司共享的物料（主数据，限制数量）"""
    return Material.query.filter(
        or_(Material.project_id == pid, Material.source == 'company')
    ).order_by(Material.name).limit(500).all()


# ---------------------------------------------------------------- 列表
@cost_bp.route('/budget/')
@login_required
@permission_required('cost:budget:view')
def budget_list():
    pid = _current_pid()
    if not pid:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))
    q = ResponsibilityCostBudget.query
    if pid:
        q = q.filter_by(project_id=pid)
    q = apply_data_scope(q, ResponsibilityCostBudget)
    keyword = request.args.get('keyword', '', type=str)
    if keyword:
        like = '%' + keyword + '%'
        q = q.filter(
            ResponsibilityCostBudget.wbs_code.like(like) |
            ResponsibilityCostBudget.wbs_name.like(like)
        )
    budgets = q.order_by(ResponsibilityCostBudget.created_at.desc()).all()
    # 预算总额（用于列表概览）
    for b in budgets:
        b._planned = float(b.budget_amount or 0)
    return render_template('cost/budget_list.html', budgets=budgets, keyword=keyword)


# ---------------------------------------------------------------- 新增
@cost_bp.route('/budget/create', methods=['GET', 'POST'])
@login_required
@permission_required('cost:budget:edit')
def budget_create():
    pid = _current_pid()
    if not pid:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        b = _save_budget(ResponsibilityCostBudget(project_id=pid), request, pid)
        flash('责任成本预算创建成功。', 'success')
        return redirect(url_for('cost.budget_detail', id=b.id))

    materials = _project_materials(pid)
    return render_template('cost/budget_form.html', budget=None, materials=materials,
                           categories=CostCategory.query.filter_by(project_id=pid).all())


# ---------------------------------------------------------------- 编辑
@cost_bp.route('/budget/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@permission_required('cost:budget:edit')
def budget_edit(id):
    pid = _current_pid()
    if not pid:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))
    b = ResponsibilityCostBudget.query.get_or_404(id)
    if request.method == 'POST':
        _save_budget(b, request, pid)
        flash('责任成本预算更新成功。', 'success')
        return redirect(url_for('cost.budget_detail', id=b.id))
    materials = _project_materials(pid)
    return render_template('cost/budget_form.html', budget=b, materials=materials,
                           categories=CostCategory.query.filter_by(project_id=pid).all())


def _save_budget(budget, request, pid):
    """解析表单并保存预算头 + 明细行（含动态材料行）"""
    budget.wbs_code = request.form.get('wbs_code', '').strip() or 'PROJECT'
    budget.wbs_name = request.form.get('wbs_name', '').strip() or '责任成本预算'
    budget.category_code = request.form.get('category_code', '').strip() or None
    budget.owner_role = request.form.get('owner_role', '').strip() or None
    budget.source = request.form.get('source', 'manual')
    budget.status = request.form.get('status', 'active')
    budget.remark = request.form.get('remark', '').strip() or None

    # 删除旧明细，按提交重建
    for it in budget.items.all():
        db.session.delete(it)
    db.session.flush()

    mat_ids = request.form.getlist('item_material_id')
    qtys = request.form.getlist('item_quantity')
    ups = request.form.getlist('item_unit_price')
    remarks = request.form.getlist('item_remark')
    planned = 0.0
    for i, mid in enumerate(mat_ids):
        if not mid:
            continue
        try:
            qty = float(qtys[i] or 0)
            up = float(ups[i] or 0)
        except (ValueError, IndexError):
            continue
        amount = round(qty * up, 2)
        planned += amount
        item = ResponsibilityCostBudgetItem(
            budget_id=budget.id,
            material_id=int(mid),
            quantity=qty,
            unit_price=up,
            amount=amount,
            remark=remarks[i] if i < len(remarks) else None,
        )
        budget.items.append(item)
    budget.budget_amount = round(planned, 2)
    db.session.add(budget)
    db.session.commit()
    return budget


# ---------------------------------------------------------------- 详情（节超）
@cost_bp.route('/budget/<int:id>')
@login_required
@permission_required('cost:budget:view')
def budget_detail(id):
    b = ResponsibilityCostBudget.query.get_or_404(id)
    variance = _compute_budget_variance(b)
    return render_template('cost/budget_detail.html', budget=b, variance=variance)


# ---------------------------------------------------------------- 删除
@cost_bp.route('/budget/<int:id>/delete', methods=['POST'])
@login_required
@permission_required('cost:budget:edit')
def budget_delete(id):
    b = ResponsibilityCostBudget.query.get_or_404(id)
    db.session.delete(b)  # 级联删除明细
    db.session.commit()
    flash('责任成本预算已删除。', 'success')
    return redirect(url_for('cost.budget_list'))


# ---------------------------------------------------------------- 从合同自动生成
@cost_bp.route('/api/budget/auto-gen', methods=['POST'])
@login_required
@permission_required('cost:budget:edit')
def api_budget_auto_gen():
    pid = _current_pid()
    if not pid:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('cost.budget_list'))

    # 清除本项目旧的合同自动生成预算（手动预算保留）
    old = ResponsibilityCostBudget.query.filter_by(project_id=pid, source='contract_auto').all()
    for ob in old:
        db.session.delete(ob)
    db.session.flush()

    # 聚合合同清单：按 material_id 汇总数量、不含税金额、含税金额
    rows = db.session.query(
        ContractItem.material_id,
        func.coalesce(func.sum(ContractItem.quantity), 0),
        func.coalesce(func.sum(ContractItem.unit_price_without_tax * ContractItem.quantity), 0),
        func.coalesce(func.sum(ContractItem.amount_with_tax), 0),
    ).join(Contract, Contract.id == ContractItem.contract_id).filter(
        Contract.project_id == pid,
        Contract.is_deleted == False,
        ContractItem.material_id.isnot(None),
    ).group_by(ContractItem.material_id).all()

    n = 0
    if rows:
        budget = ResponsibilityCostBudget(
            project_id=pid,
            wbs_code='PROJECT',
            wbs_name='项目责任成本（合同自动生成）',
            category_code='material',
            owner_role='project_manager',
            source='contract_auto',
            status='active',
        )
        planned = 0.0
        for mid, qty, amt_nt, amt_t in rows:
            qty = float(qty or 0)
            amt_nt = float(amt_nt or 0)
            if qty <= 0:
                continue
            up = round(amt_nt / qty, 4)
            item = ResponsibilityCostBudgetItem(
                budget_id=budget.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=up,
                amount=round(amt_nt, 2),
            )
            budget.items.append(item)
            planned += float(amt_nt or 0)
            n += 1
        budget.budget_amount = round(planned, 2)
        db.session.add(budget)

    db.session.commit()
    flash('已从合同清单生成责任成本预算（%d 条材料明细）。' % n, 'success')
    return redirect(url_for('cost.budget_list'))


# ---------------------------------------------------------------- 盈亏分析（P3 占位）
@cost_bp.route('/profit/')
@login_required
@permission_required('cost:profit:view')
def profit():
    return render_template('cost/profit.html')


# ---------------------------------------------------------------- 预算管控看板（P2）
@cost_bp.route('/control/')
@login_required
@permission_required('cost:control:view')
def control():
    pid = _current_pid()
    if not pid:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))
    fy = request.args.get('fy', type=int) or datetime.now().year
    rows = BudgetService.control_board(pid, fy) if pid else []
    # 汇总
    total_annual = round(sum(r['annual'] for r in rows), 2)
    total_used = round(sum(r['used'] for r in rows), 2)
    total_frozen = round(sum(r['frozen'] for r in rows), 2)
    total_available = round(total_annual - total_used - total_frozen, 2)
    over_count = sum(1 for r in rows if r['status'] == 'exceed')
    warn_count = sum(1 for r in rows if r['status'] == 'warn')
    return render_template('cost/control.html',
                           rows=rows, fy=fy,
                           total_annual=total_annual, total_used=total_used,
                           total_frozen=total_frozen, total_available=total_available,
                           over_count=over_count, warn_count=warn_count,
                           labels=CATEGORY_LABELS)


# ---------------------------------------------------------------- 预算控制（建/改/删）
@cost_bp.route('/control/edit', methods=['GET', 'POST'])
@cost_bp.route('/control/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@permission_required('cost:budget:edit')
def control_edit(id=0):
    """新增(id=0)或编辑预算控制科目（设定年度预算/预警线/提级审批角色）。"""
    pid = _current_pid()
    if not pid:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))
    ctrl = BudgetControl.query.get(id) if id else None
    fy = datetime.now().year

    if request.method == 'POST':
        category_code = request.form.get('category_code', '').strip()
        annual = request.form.get('annual_amount', type=float) or 0
        warn = request.form.get('warn_threshold', type=float)
        approver = request.form.get('approver_role', '').strip() or None
        if not category_code:
            flash('请选择预算科目。', 'danger')
        else:
            if ctrl is None:
                ctrl = BudgetControl(project_id=pid, category_code=category_code, fiscal_year=fy)
                db.session.add(ctrl)
            ctrl.annual_amount = annual
            ctrl.warn_threshold = warn if warn is not None else 0.8
            ctrl.approver_role = approver
            ctrl.status = True
            db.session.commit()
            flash('预算控制已保存。', 'success')
            return redirect(url_for('cost.control'))

    return render_template('cost/control_form.html', ctrl=ctrl,
                           labels=CATEGORY_LABELS, fy=fy)


@cost_bp.route('/control/<int:id>/delete', methods=['POST'])
@login_required
@permission_required('cost:budget:edit')
def control_delete(id):
    ctrl = BudgetControl.query.get_or_404(id)
    if ctrl.details.count() > 0:
        flash('该预算科目已有开支记账，不可删除（可置为停用）。', 'danger')
        return redirect(url_for('cost.control'))
    db.session.delete(ctrl)
    db.session.commit()
    flash('预算控制已删除。', 'success')
    return redirect(url_for('cost.control'))


# ---------------------------------------------------------------- 预算调整单（P2）
@cost_bp.route('/adjustment/')
@login_required
@permission_required('cost:adjust:review')
def adjustment_list():
    pid = _current_pid()
    if not pid:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))
    q = BudgetAdjustment.query
    if pid:
        q = q.filter_by(project_id=pid)
    q = apply_data_scope(q, BudgetAdjustment)
    status = request.args.get('status', '', type=str)
    if status:
        q = q.filter(BudgetAdjustment.approval_status == status)
    adjs = q.order_by(BudgetAdjustment.created_at.desc()).all()
    return render_template('cost/adjustment_list.html', adjs=adjs, status=status,
                           labels=CATEGORY_LABELS)


@cost_bp.route('/adjustment/create', methods=['GET', 'POST'])
@login_required
@permission_required('cost:adjust:review')
def adjustment_create():
    pid = _current_pid()
    if not pid:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))
    fy = datetime.now().year
    controls = BudgetControl.query.filter_by(project_id=pid, fiscal_year=fy).all()

    if request.method == 'POST':
        control_id = request.form.get('control_id', type=int)
        new_amount = request.form.get('new_amount', type=float)
        reason = request.form.get('reason', '').strip() or None
        if not control_id or new_amount is None:
            flash('请选择预算科目并填写调整后年度预算。', 'danger')
        else:
            adj = BudgetService.request_adjustment(
                control_id, new_amount, reason,
                applicant_id=getattr(current_user, 'id', None))
            db.session.commit()
            flash('预算调整单已提交，待审批。', 'success')
            return redirect(url_for('cost.adjustment_detail', id=adj.id))

    return render_template('cost/adjustment_form.html', controls=controls,
                           labels=CATEGORY_LABELS, fy=fy)


@cost_bp.route('/adjustment/<int:id>')
@login_required
@permission_required('cost:adjust:review')
def adjustment_detail(id):
    adj = BudgetAdjustment.query.get_or_404(id)
    return render_template('cost/adjustment_detail.html', adj=adj,
                           label=CATEGORY_LABELS.get(adj.category_code, adj.category_code))


@cost_bp.route('/adjustment/<int:id>/approve', methods=['POST'])
@login_required
@permission_required('cost:adjust:review')
def adjustment_approve(id):
    adj = BudgetAdjustment.query.get_or_404(id)
    try:
        BudgetService.approve_adjustment(id)
        db.session.commit()
        flash('预算调整单已审批通过，年度预算已回写。', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('cost.adjustment_detail', id=id))


@cost_bp.route('/adjustment/<int:id>/reject', methods=['POST'])
@login_required
@permission_required('cost:adjust:review')
def adjustment_reject(id):
    adj = BudgetAdjustment.query.get_or_404(id)
    try:
        BudgetService.reject_adjustment(id)
        db.session.commit()
        flash('预算调整单已驳回。', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('cost.adjustment_detail', id=id))
