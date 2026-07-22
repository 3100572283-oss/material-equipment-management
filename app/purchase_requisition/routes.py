from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.purchase_requisition import bp
from app import db
from app.models import (PurchaseRequisition, PurchaseRequisitionItem,
                       Material, Category, Contract, ContractItem, Project)
from app.decorators import editor_required, log_audit
from app.utils import to_decimal, apply_data_scope, get_project_materials


def _gen_pr_no(project_id):
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"SQ-{project_id}-{today}-"
    existing = PurchaseRequisition.query.filter(
        PurchaseRequisition.pr_no.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


def _get_category_descendants(category_id):
    result = []
    cat = Category.query.get(category_id)
    if not cat:
        return result
    result.append(category_id)
    if cat.level < 3:
        children = Category.query.filter_by(parent_id=category_id).all()
        for child in children:
            result.extend(_get_category_descendants(child.id))
    return result


@bp.route('/')
@login_required
def index():
    from flask import session
    project_id = session.get('current_project_id')
    # 全部数据权限用户在"全部项目"模式下不限制项目
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    status = request.args.get('status', '', type=str)
    apply_dept = request.args.get('apply_dept', '', type=str)
    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)
    view_mode = request.args.get('view_mode', 'my')

    query = PurchaseRequisition.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, PurchaseRequisition)

    if view_mode == 'my' and not current_user.is_admin():
        query = query.filter_by(apply_user=current_user.username)

    if keyword:
        query = query.filter(or_(
            PurchaseRequisition.pr_no.contains(keyword),
            PurchaseRequisition.apply_dept.contains(keyword),
            PurchaseRequisition.apply_user.contains(keyword)
        ))
    if status:
        query = query.filter_by(status=status)
    if apply_dept:
        query = query.filter_by(apply_dept=apply_dept)
    if date_from:
        try:
            query = query.filter(PurchaseRequisition.apply_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            query = query.filter(PurchaseRequisition.apply_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass

    pagination = query.order_by(PurchaseRequisition.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)

    depts = PurchaseRequisition.query.filter_by(project_id=project_id).with_entities(
        PurchaseRequisition.apply_dept).distinct().all()
    dept_list = [d[0] for d in depts if d[0]]

    return render_template('purchase_requisition/index.html',
                           pagination=pagination, keyword=keyword,
                           status=status, apply_dept=apply_dept,
                           date_from=date_from, date_to=date_to,
                           view_mode=view_mode, dept_list=dept_list)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='purchase_requisition', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        apply_date_str = request.form.get('apply_date')
        try:
            apply_date = datetime.strptime(apply_date_str, '%Y-%m-%d').date()
        except Exception:
            apply_date = date.today()

        demand_date_str = request.form.get('demand_date')
        try:
            demand_date = datetime.strptime(demand_date_str, '%Y-%m-%d').date()
        except Exception:
            demand_date = None

        pr = PurchaseRequisition(
            project_id=project_id,
            pr_no=_gen_pr_no(project_id),
            apply_dept=request.form.get('apply_dept', '').strip() or None,
            apply_user=request.form.get('apply_user', '').strip() or current_user.username,
            apply_date=apply_date,
            demand_date=demand_date,
            status='draft',
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(pr)
        db.session.flush()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        purposes = request.form.getlist('purpose[]')

        has_items = False
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
            except Exception:
                continue
            if qty == 0:
                continue
            mat = Material.query.get(int(mid))
            if not mat:
                continue
            has_items = True
            item = PurchaseRequisitionItem(
                pr_id=pr.id,
                material_id=mat.id,
                material_name=mat.name,
                specification=mat.specification,
                unit=mat.unit,
                apply_qty=qty,
                approve_qty=0,
                purpose=purposes[idx] if idx < len(purposes) else ''
            )
            db.session.add(item)

        action = request.form.get('action')
        if action == 'submit':
            if not has_items:
                db.session.rollback()
                flash('请先添加申请明细。', 'danger')
                return redirect(url_for('purchase_requisition.create'))
            db.session.commit()
            from app.approval.service import submit_approval
            success, msg, instance = submit_approval('purchase_requisition', pr.id, project_id=pr.project_id)
            if success:
                pr.status = 'pending'
                db.session.commit()
                flash('采购申请已提交审批。', 'success')
            else:
                flash(f'提交审批失败：{msg}', 'danger')
            return redirect(url_for('purchase_requisition.detail', id=pr.id))

        db.session.commit()
        flash('采购申请创建成功。', 'success')
        return redirect(url_for('purchase_requisition.detail', id=pr.id))

    from app.approval.service import is_approval_enabled
    approval_enabled = is_approval_enabled('purchase_requisition', project_id)
    categories = Category.query.filter_by(project_id=project_id, parent_id=0).order_by(Category.sort_order).all()
    materials_raw = get_project_materials(project_id, common_only=True).all()
    materials = [{'id': m.id, 'name': m.name, 'specification': m.specification or '', 'unit': m.unit or ''} for m in materials_raw]

    copy_from_id = request.args.get('copy_from', type=int)
    copy_pr = None
    if copy_from_id:
        src = PurchaseRequisition.query.get(copy_from_id)
        if src and src.project_id == project_id:
            copy_pr = src

    return render_template('purchase_requisition/form.html', pr=copy_pr,
                           categories=categories, materials=materials,
                           today_str=date.today().strftime('%Y-%m-%d'),
                           default_pr_no=_gen_pr_no(project_id),
                           is_copy=bool(copy_pr), approval_enabled=approval_enabled)


@bp.route('/<int:id>')
@login_required
def detail(id):
    pr = PurchaseRequisition.query.get_or_404(id)

    from app.approval.service import get_instance_by_biz
    approval_instance = get_instance_by_biz('purchase_requisition', pr.id)

    return render_template('purchase_requisition/detail.html', pr=pr,
                           approval_instance=approval_instance)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='purchase_requisition', operation='编辑')
def edit(id):
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status not in ('draft', 'rejected'):
        flash('只有草稿或已驳回状态的申请才能编辑。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))

    if request.method == 'POST':
        apply_date_str = request.form.get('apply_date')
        try:
            pr.apply_date = datetime.strptime(apply_date_str, '%Y-%m-%d').date()
        except Exception:
            pr.apply_date = date.today()

        demand_date_str = request.form.get('demand_date')
        try:
            pr.demand_date = datetime.strptime(demand_date_str, '%Y-%m-%d').date()
        except Exception:
            pr.demand_date = None

        pr.apply_dept = request.form.get('apply_dept', '').strip() or None
        pr.apply_user = request.form.get('apply_user', '').strip() or current_user.username
        pr.remark = request.form.get('remark', '').strip()

        for item in list(pr.items):
            db.session.delete(item)
        db.session.flush()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        purposes = request.form.getlist('purpose[]')

        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
            except Exception:
                continue
            if qty == 0:
                continue
            mat = Material.query.get(int(mid))
            if not mat:
                continue
            item = PurchaseRequisitionItem(
                pr_id=pr.id,
                material_id=mat.id,
                material_name=mat.name,
                specification=mat.specification,
                unit=mat.unit,
                apply_qty=qty,
                approve_qty=0,
                purpose=purposes[idx] if idx < len(purposes) else ''
            )
            db.session.add(item)

        db.session.commit()

        action = request.form.get('action')
        if action == 'submit':
            if pr.items.count() == 0:
                flash('请先添加申请明细。', 'danger')
                return redirect(url_for('purchase_requisition.detail', id=id))
            from app.approval.service import submit_approval
            success, msg, instance = submit_approval('purchase_requisition', pr.id, project_id=pr.project_id)
            if success:
                pr.status = 'pending'
                db.session.commit()
                flash('采购申请已提交审批。', 'success')
            else:
                flash(f'提交审批失败：{msg}', 'danger')
            return redirect(url_for('purchase_requisition.detail', id=id))

        flash('采购申请已更新。', 'success')
        return redirect(url_for('purchase_requisition.detail', id=id))

    from app.approval.service import is_approval_enabled
    approval_enabled = is_approval_enabled('purchase_requisition', pr.project_id)
    categories = Category.query.filter_by(project_id=pr.project_id, parent_id=0).order_by(Category.sort_order).all()
    materials_raw = get_project_materials(pr.project_id, common_only=True).all()
    materials = [{'id': m.id, 'name': m.name, 'specification': m.specification or '', 'unit': m.unit or ''} for m in materials_raw]
    return render_template('purchase_requisition/form.html', pr=pr,
                           categories=categories, materials=materials,
                           today_str=pr.apply_date.strftime('%Y-%m-%d') if pr.apply_date else date.today().strftime('%Y-%m-%d'),
                           approval_enabled=approval_enabled)


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='purchase_requisition', operation='提交')
def submit(id):
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status not in ('draft', 'rejected'):
        flash('只有草稿或已驳回状态的申请才能提交审批。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))
    if pr.items.count() == 0:
        flash('请先添加申请明细。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))

    from app.approval.service import submit_approval
    success, msg, instance = submit_approval('purchase_requisition', pr.id, project_id=pr.project_id)
    if success:
        pr.status = 'pending'
        db.session.commit()
        flash('采购申请已提交审批。', 'success')
    else:
        flash(f'提交审批失败：{msg}', 'danger')

    return redirect(url_for('purchase_requisition.detail', id=id))


@bp.route('/<int:id>/withdraw', methods=['POST'])
@login_required
@log_audit(module='purchase_requisition', operation='撤回')
def withdraw(id):
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status != 'pending':
        flash('只有待审批状态的申请才能撤回。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))
    if pr.apply_user != current_user.username and pr.apply_user != current_user.name and not current_user.is_admin():
        flash('只能撤回自己的申请。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))

    from app.approval.service import withdraw as withdraw_approval, get_instance_by_biz
    inst = get_instance_by_biz('purchase_requisition', pr.id)
    if not inst:
        flash('未找到审批记录。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))

    reason = request.form.get('reason', '').strip()
    success, msg = withdraw_approval(inst.id, applicant_id=current_user.id, reason=reason)
    if success:
        pr.status = 'draft'
        db.session.commit()
    flash(msg, 'success' if success else 'danger')
    return redirect(url_for('purchase_requisition.detail', id=id))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='purchase_requisition', operation='删除')
def delete(id):
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status != 'draft':
        flash('只有草稿状态的申请才能删除。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))

    db.session.delete(pr)
    db.session.commit()
    flash('采购申请已删除。', 'success')
    return redirect(url_for('purchase_requisition.index'))


@bp.route('/<int:id>/convert_contract', methods=['POST'])
@login_required
@editor_required
@log_audit(module='purchase_requisition', operation='转合同')
def convert_contract(id):
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status != 'passed':
        flash('只有已通过审批的申请才能转合同。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))

    contract_items = []
    has_unconverted = False
    for item in pr.items:
        if item.converted:
            continue
        has_unconverted = True
        contract_items.append({
            'material_id': item.material_id,
            'material_name': item.material_name,
            'specification': item.specification,
            'unit': item.unit,
            'apply_qty': float(item.apply_qty or 0),
            'purpose': item.purpose or ''
        })

    if not has_unconverted:
        flash('所有申请明细已转合同。', 'warning')
        pr.status = 'completed'
        db.session.commit()
        return redirect(url_for('purchase_requisition.detail', id=id))

    from flask import session
    session['pending_contract_items'] = contract_items
    session['source_pr_id'] = pr.id

    return redirect(url_for('contract.create', pr_id=id))


@bp.route('/<int:id>/convert_stock_out', methods=['POST'])
@login_required
@editor_required
@log_audit(module='purchase_requisition', operation='转出库单')
def convert_stock_out(id):
    """领料申请审批通过后生成出库单（明细自动带入）"""
    from app.models import StockOut, StockOutItem, Inventory
    from app.services.inventory_cost import InventoryCostService
    from app.utils import _gen_code_with_seq
    from datetime import date as _date
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status != 'passed':
        flash('只有已通过审批的申请才能生成出库单。', 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))

    # 收集未出库的明细
    items_to_out = []
    insufficient = []
    for item in pr.items:
        if item.converted:
            continue
        # 库存校验
        inv = Inventory.query.filter_by(
            project_id=pr.project_id, material_id=item.material_id).first()
        avail = float(inv.quantity) if inv else 0
        qty = float(item.apply_qty or 0)
        if qty > avail:
            insufficient.append(f'{item.material_name}（需 {qty}，可用 {avail}）')
            continue
        items_to_out.append((item, qty, inv))

    if insufficient:
        flash('以下物资库存不足，无法生成出库单：\n' + '；'.join(insufficient), 'danger')
        return redirect(url_for('purchase_requisition.detail', id=id))

    if not items_to_out:
        flash('没有可生成出库单的明细。', 'warning')
        return redirect(url_for('purchase_requisition.detail', id=id))

    # 生成出库单
    code = _gen_code_with_seq('CK', pr.project_id, StockOut)
    stock_out = StockOut(
        project_id=pr.project_id,
        code=code,
        stock_out_date=_date.today(),
        stock_out_type='工程领用',
        operator=current_user.name or current_user.username,
        remark=f'由领料申请 {pr.pr_no} 生成',
        approval_status='passed',
    )
    db.session.add(stock_out)
    db.session.flush()

    total_qty = 0
    total_amount = 0
    for item, qty, inv in items_to_out:
        # 调用加权平均成本扣减库存
        amount = InventoryCostService.apply_outbound(pr.project_id, item.material_id, qty)
        so_item = StockOutItem(
            stock_out_id=stock_out.id,
            material_id=item.material_id,
            quantity=qty,
            unit_price=(amount / qty) if qty > 0 else 0,
            amount=amount,
        )
        db.session.add(so_item)
        item.converted = True
        total_qty += qty
        total_amount += float(amount)

    stock_out.total_quantity = total_qty
    stock_out.total_amount = total_amount

    # 全部明细已转 → 更新状态
    if all(item.converted for item in pr.items):
        pr.status = 'completed'

    db.session.commit()
    flash(f'已生成出库单 {code}，库存已扣减。', 'success')
    return redirect(url_for('stock_out.detail', id=stock_out.id))


@bp.route('/<int:id>/mark_converted', methods=['POST'])
@login_required
@editor_required
@log_audit(module='purchase_requisition', operation='标记完成')
def mark_converted(id):
    pr = PurchaseRequisition.query.get_or_404(id)
    for item in pr.items:
        if not item.converted:
            item.converted = True
            item.approve_qty = item.apply_qty
    db.session.commit()

    if all(item.converted for item in pr.items):
        pr.status = 'completed'
        db.session.commit()

    flash('采购申请已标记为已转合同。', 'success')
    return redirect(url_for('purchase_requisition.detail', id=id))


@bp.route('/api/materials_by_category/<int:category_id>')
@login_required
def api_materials_by_category(category_id):
    from flask import session
    project_id = session.get('current_project_id')
    cat_ids = _get_category_descendants(category_id)
    materials = get_project_materials(project_id, common_only=True).filter(
        Material.category_id.in_(cat_ids)
    ).order_by(Material.name).all()
    return jsonify([{
        'id': m.id,
        'name': m.name,
        'specification': m.specification or '',
        'unit': m.unit
    } for m in materials])


@bp.route('/batch_delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='purchase_requisition', operation='批量删除')
def batch_delete():
    project_id = session.get('current_project_id')
    ids = request.form.get('ids', '')
    id_list = [int(x) for x in ids.split(',') if x.strip().isdigit()]
    if not id_list:
        flash('请选择要删除的采购申请。', 'warning')
        return redirect(url_for('purchase_requisition.index'))

    success_count = 0
    fail_count = 0
    for pid in id_list:
        pr = PurchaseRequisition.query.get(pid)
        if not pr or pr.project_id != project_id:
            fail_count += 1
            continue
        if pr.status != 'draft':
            fail_count += 1
            continue
        try:
            for item in pr.items:
                db.session.delete(item)
            db.session.delete(pr)
            success_count += 1
        except Exception:
            db.session.rollback()
            fail_count += 1

    db.session.commit()
    if fail_count > 0:
        flash(f'批量删除完成：成功{success_count}条，失败{fail_count}条（仅草稿状态可删除）。', 'warning')
    else:
        flash(f'批量删除成功，共{success_count}条。', 'success')
    return redirect(url_for('purchase_requisition.index'))


@bp.route('/batch_export')
@login_required
def batch_export():
    from io import BytesIO
    from openpyxl import Workbook
    from flask import make_response
    project_id = session.get('current_project_id')
    ids_str = request.args.get('ids', '')
    id_list = [int(x) for x in ids_str.split(',') if x.strip().isdigit()]

    query = PurchaseRequisition.query.filter_by(project_id=project_id)
    if id_list:
        query = query.filter(PurchaseRequisition.id.in_(id_list))
    prs = query.order_by(PurchaseRequisition.pr_no).all()

    wb = Workbook()
    ws = wb.active
    ws.title = '采购申请'
    headers = ['申请单号', '申请部门', '申请人', '申请日期', '需求日期', '物资种类', '状态', '备注']
    ws.append(headers)
    for p in prs:
        ws.append([
            p.pr_no,
            p.apply_dept or '',
            p.apply_user or '',
            str(p.apply_date or ''),
            str(p.demand_date or ''),
            p.item_count or 0,
            p.status or '',
            p.remark or ''
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = f'attachment; filename=purchase_requisitions_{datetime.now().strftime("%Y%m%d%H%M%S")}.xlsx'
    return resp