from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from app.material_transfer import bp
from app import db
from app.models import (MaterialTransfer, MaterialTransferItem,
                       Inventory, Material, Project)
from app.decorators import editor_required, log_audit
from app.utils import to_decimal


def _gen_transfer_no(project_id):
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"DB-{project_id}-{today}-"
    existing = MaterialTransfer.query.filter(
        MaterialTransfer.transfer_no.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


def _apply_inventory(project_id, material_id, quantity):
    inv = Inventory.query.filter_by(project_id=project_id, material_id=material_id).first()
    if not inv:
        inv = Inventory(project_id=project_id, material_id=material_id, quantity=0)
        db.session.add(inv)
        db.session.flush()
    inv.quantity = to_decimal(inv.quantity) + to_decimal(quantity)


def _apply_in_transit(project_id, material_id, quantity):
    inv = Inventory.query.filter_by(project_id=project_id, material_id=material_id).first()
    if not inv:
        inv = Inventory(project_id=project_id, material_id=material_id, quantity=0, in_transit_qty=0)
        db.session.add(inv)
        db.session.flush()
    inv.in_transit_qty = to_decimal(inv.in_transit_qty) + to_decimal(quantity)


@bp.route('/')
@login_required
def index():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    status = request.args.get('status', '', type=str)
    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)

    query = MaterialTransfer.query.filter(
        (MaterialTransfer.from_project_id == project_id) |
        (MaterialTransfer.to_project_id == project_id)
    )

    if keyword:
        query = query.filter(MaterialTransfer.transfer_no.contains(keyword))
    if status:
        query = query.filter_by(status=status)
    if date_from:
        try:
            query = query.filter(MaterialTransfer.transfer_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            query = query.filter(MaterialTransfer.transfer_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass

    pagination = query.order_by(MaterialTransfer.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)

    return render_template('material_transfer/index.html',
                           pagination=pagination, keyword=keyword,
                           status=status, date_from=date_from, date_to=date_to)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='material_transfer', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    other_projects = Project.query.filter(Project.id != project_id).all()

    if request.method == 'POST':
        to_project_id = request.form.get('to_project_id', type=int)
        if not to_project_id:
            flash('请选择调入项目。', 'danger')
            return redirect(url_for('material_transfer.create'))

        transfer_date_str = request.form.get('transfer_date')
        try:
            transfer_date = datetime.strptime(transfer_date_str, '%Y-%m-%d').date()
        except Exception:
            transfer_date = date.today()

        transfer = MaterialTransfer(
            transfer_no=_gen_transfer_no(project_id),
            from_project_id=project_id,
            to_project_id=to_project_id,
            transfer_date=transfer_date,
            status='draft',
            applicant=request.form.get('applicant', '').strip() or current_user.username,
            handler=request.form.get('handler', '').strip(),
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(transfer)
        db.session.flush()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')

        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
            except Exception:
                continue
            if qty == 0:
                continue

            inv = Inventory.query.filter_by(project_id=project_id, material_id=int(mid)).first()
            if inv and float(inv.quantity) < float(qty):
                flash(f'物资库存不足，无法调拨。', 'danger')
                db.session.rollback()
                return redirect(url_for('material_transfer.create'))

            mat = Material.query.get(int(mid))
            if not mat:
                continue

            item = MaterialTransferItem(
                transfer_id=transfer.id,
                material_id=mat.id,
                material_name=mat.name,
                specification=mat.specification,
                unit=mat.unit,
                transfer_qty=qty,
                out_qty=0,
                in_qty=0
            )
            db.session.add(item)

        db.session.commit()
        flash('调拨单创建成功。', 'success')
        return redirect(url_for('material_transfer.detail', id=transfer.id))

    inventories_raw = Inventory.query.filter_by(project_id=project_id).all()
    inventories = [{
        'material_id': i.material_id,
        'quantity': float(i.quantity or 0),
        'material': {
            'id': i.material.id,
            'name': i.material.name,
            'specification': i.material.specification or '',
            'unit': i.material.unit or ''
        }
    } for i in inventories_raw]
    return render_template('material_transfer/form.html', transfer=None,
                           other_projects=other_projects, inventories=inventories,
                           today_str=date.today().strftime('%Y-%m-%d'),
                           default_transfer_no=_gen_transfer_no(project_id))


@bp.route('/<int:id>')
@login_required
def detail(id):
    transfer = MaterialTransfer.query.get_or_404(id)

    from app.approval.service import get_instance_by_biz
    approval_instance = get_instance_by_biz('material_transfer', transfer.id)

    return render_template('material_transfer/detail.html', transfer=transfer,
                           approval_instance=approval_instance)


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='material_transfer', operation='提交')
def submit(id):
    transfer = MaterialTransfer.query.get_or_404(id)
    if transfer.status != 'draft':
        flash('只有草稿状态的调拨单才能提交审批。', 'danger')
        return redirect(url_for('material_transfer.detail', id=id))
    if transfer.items.count() == 0:
        flash('请先添加调拨明细。', 'danger')
        return redirect(url_for('material_transfer.detail', id=id))

    from app.approval.service import submit_approval
    success, msg, instance = submit_approval('material_transfer', transfer.id)
    if success:
        transfer.status = 'pending'
        db.session.commit()
        flash('调拨单已提交审批。', 'success')
    else:
        flash(f'提交审批失败：{msg}', 'danger')

    return redirect(url_for('material_transfer.detail', id=id))


@bp.route('/<int:id>/confirm_out', methods=['POST'])
@login_required
@editor_required
@log_audit(module='material_transfer', operation='确认出库')
def confirm_out(id):
    transfer = MaterialTransfer.query.get_or_404(id)
    if transfer.status != 'pending':
        flash('审批未通过，无法确认出库。', 'danger')
        return redirect(url_for('material_transfer.detail', id=id))

    for item in transfer.items:
        inv = Inventory.query.filter_by(project_id=transfer.from_project_id, material_id=item.material_id).first()
        if inv and float(inv.quantity) < float(item.transfer_qty):
            flash(f'物资 {item.material_name} 库存不足，无法出库。', 'danger')
            return redirect(url_for('material_transfer.detail', id=id))

    for item in transfer.items:
        _apply_inventory(transfer.from_project_id, item.material_id, -float(item.transfer_qty))
        _apply_in_transit(transfer.to_project_id, item.material_id, float(item.transfer_qty))
        item.out_qty = item.transfer_qty

    transfer.status = 'in_transit'
    transfer.handler = request.form.get('handler', transfer.handler) or current_user.username
    db.session.commit()
    flash('出库确认成功，调出项目库存已扣减，调入项目显示为在途物资。', 'success')
    return redirect(url_for('material_transfer.detail', id=id))


@bp.route('/<int:id>/confirm_in', methods=['POST'])
@login_required
@editor_required
@log_audit(module='material_transfer', operation='确认入库')
def confirm_in(id):
    transfer = MaterialTransfer.query.get_or_404(id)
    if transfer.status != 'in_transit':
        flash('当前状态不是在途，无法确认入库。', 'danger')
        return redirect(url_for('material_transfer.detail', id=id))

    in_qtys = request.form.getlist('in_qty[]')
    for idx, item in enumerate(transfer.items):
        if idx < len(in_qtys):
            try:
                qty = to_decimal(in_qtys[idx])
                item.in_qty = qty if qty >= 0 else 0
            except Exception:
                item.in_qty = item.out_qty
        else:
            item.in_qty = item.out_qty

    for item in transfer.items:
        _apply_inventory(transfer.to_project_id, item.material_id, float(item.in_qty))
        _apply_in_transit(transfer.to_project_id, item.material_id, -float(item.out_qty))

    transfer.status = 'completed'
    transfer.handler = request.form.get('handler', transfer.handler) or current_user.username
    db.session.commit()
    flash('入库确认成功，调入项目库存已增加。', 'success')
    return redirect(url_for('material_transfer.detail', id=id))


@bp.route('/<int:id>/cancel', methods=['POST'])
@login_required
@editor_required
@log_audit(module='material_transfer', operation='取消')
def cancel(id):
    transfer = MaterialTransfer.query.get_or_404(id)
    if transfer.status not in ('draft', 'pending'):
        flash('只有草稿或待出库状态的调拨单才能取消。', 'danger')
        return redirect(url_for('material_transfer.detail', id=id))

    transfer.status = 'cancelled'
    db.session.commit()
    flash('调拨单已取消。', 'success')
    return redirect(url_for('material_transfer.index'))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='material_transfer', operation='删除')
def delete(id):
    transfer = MaterialTransfer.query.get_or_404(id)
    if transfer.status not in ('draft', 'cancelled'):
        flash('只有草稿或已取消状态的调拨单才能删除。', 'danger')
        return redirect(url_for('material_transfer.detail', id=id))

    db.session.delete(transfer)
    db.session.commit()
    flash('调拨单已删除。', 'success')
    return redirect(url_for('material_transfer.index'))


@bp.route('/api/inventory/<int:project_id>')
@login_required
def api_inventory(project_id):
    inventories = Inventory.query.filter_by(project_id=project_id).all()
    return jsonify([{
        'id': i.material_id,
        'quantity': float(i.quantity or 0)
    } for i in inventories])