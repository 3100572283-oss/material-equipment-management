from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.purchase_order import bp
from app import db
from app.models import (PurchaseOrder, PurchaseOrderItem, Material, Supplier, Project)
from app.decorators import editor_required, log_audit
from app.utils import to_decimal, apply_data_scope, get_project_materials, get_project_suppliers


def _gen_po_number(project_id):
    """生成唯一采购订单号：PO-{YYYYMMDD}-{序号}"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f'PO-{today}-'
    last = PurchaseOrder.query.filter(
        PurchaseOrder.po_number.like(f'{prefix}%')
    ).order_by(PurchaseOrder.id.desc()).first()
    if last:
        try:
            seq = int(last.po_number.split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f'{prefix}{seq:04d}'


def _update_po_total(po_id):
    """更新采购订单总金额"""
    po = PurchaseOrder.query.get(po_id)
    if not po:
        return
    items = PurchaseOrderItem.query.filter_by(po_id=po_id).all()
    total = sum(float(item.total_price or 0) for item in items)
    po.total_amount = total
    db.session.commit()


@bp.route('/')
@login_required
def index():
    """采购订单列表页"""
    project_id = session.get('current_project_id')
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    status = request.args.get('status', '', type=str)

    query = PurchaseOrder.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, PurchaseOrder)

    if keyword:
        query = query.filter(or_(
            PurchaseOrder.po_number.contains(keyword),
        ))
    if status:
        query = query.filter_by(status=status)

    pagination = query.order_by(PurchaseOrder.created_at.desc()).paginate(
        page=page, per_page=15, error_out=False)

    suppliers = get_project_suppliers(project_id, common_only=True).all() if project_id else []
    return render_template('purchase_order/list.html', pagination=pagination,
                           keyword=keyword, status=status, suppliers=suppliers)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='purchase_order', operation='新增')
def create():
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        po_number = request.form.get('po_number', '').strip()
        if not po_number:
            po_number = _gen_po_number(project_id)

        po = PurchaseOrder(
            po_number=po_number,
            project_id=project_id,
            supplier_id=request.form.get('supplier_id', type=int),
            status='draft',
            created_by=current_user.id,
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(po)
        db.session.flush()

        # 明细
        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        remarks = request.form.getlist('item_remark[]')

        for i, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = to_decimal(quantities[i]) if i < len(quantities) else 0
            price = to_decimal(unit_prices[i]) if i < len(unit_prices) else 0
            total = float(qty) * float(price)
            item = PurchaseOrderItem(
                po_id=po.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=price,
                total_price=total,
                remark=remarks[i] if i < len(remarks) else ''
            )
            db.session.add(item)

        db.session.commit()
        _update_po_total(po.id)
        flash('采购订单创建成功。', 'success')
        return redirect(url_for('purchase_order.detail', id=po.id))

    materials = get_project_materials(project_id).all()
    suppliers = get_project_suppliers(project_id, common_only=True).all()
    default_po_number = _gen_po_number(project_id)
    return render_template('purchase_order/form.html',
                           materials=materials, suppliers=suppliers,
                           default_po_number=default_po_number, po=None)


@bp.route('/<int:id>')
@login_required
def detail(id):
    po = PurchaseOrder.query.get_or_404(id)
    items = po.items.all()
    return render_template('purchase_order/detail.html', po=po, items=items)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='purchase_order', operation='编辑')
def edit(id):
    po = PurchaseOrder.query.get_or_404(id)
    if po.status not in ('draft',):
        flash('当前状态不允许编辑。', 'warning')
        return redirect(url_for('purchase_order.detail', id=id))

    if request.method == 'POST':
        po.supplier_id = request.form.get('supplier_id', type=int)
        po.remark = request.form.get('remark', '').strip()

        # 删除旧明细
        PurchaseOrderItem.query.filter_by(po_id=po.id).delete()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        remarks = request.form.getlist('item_remark[]')

        for i, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = to_decimal(quantities[i]) if i < len(quantities) else 0
            price = to_decimal(unit_prices[i]) if i < len(unit_prices) else 0
            total = float(qty) * float(price)
            item = PurchaseOrderItem(
                po_id=po.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=price,
                total_price=total,
                remark=remarks[i] if i < len(remarks) else ''
            )
            db.session.add(item)

        db.session.commit()
        _update_po_total(po.id)
        flash('采购订单已更新。', 'success')
        return redirect(url_for('purchase_order.detail', id=po.id))

    materials = get_project_materials(po.project_id).all() if po.project_id else []
    suppliers = get_project_suppliers(po.project_id, common_only=True).all() if po.project_id else []
    items = po.items.all()
    return render_template('purchase_order/form.html',
                           po=po, materials=materials, suppliers=suppliers,
                           default_po_number=po.po_number, items=items)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='purchase_order', operation='删除')
def delete(id):
    po = PurchaseOrder.query.get_or_404(id)
    if po.status not in ('draft',):
        flash('当前状态不允许删除。', 'warning')
        return redirect(url_for('purchase_order.detail', id=id))

    db.session.delete(po)
    db.session.commit()
    flash('采购订单已删除。', 'success')
    return redirect(url_for('purchase_order.index'))


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@editor_required
def submit(id):
    """提交采购订单"""
    po = PurchaseOrder.query.get_or_404(id)
    if po.status != 'draft':
        flash('当前状态不允许提交。', 'warning')
        return redirect(url_for('purchase_order.detail', id=id))

    # 检查是否有明细
    if po.items.count() == 0:
        flash('请至少添加一条明细后再提交。', 'warning')
        return redirect(url_for('purchase_order.detail', id=id))

    po.status = 'submitted'
    db.session.commit()
    flash('采购订单已提交。', 'success')
    return redirect(url_for('purchase_order.detail', id=id))


@bp.route('/<int:id>/approve', methods=['POST'])
@login_required
@editor_required
def approve(id):
    """审批通过采购订单"""
    po = PurchaseOrder.query.get_or_404(id)
    if po.status != 'submitted':
        flash('当前状态不允许审批。', 'warning')
        return redirect(url_for('purchase_order.detail', id=id))

    po.status = 'approved'
    db.session.commit()
    flash('采购订单已审批通过。', 'success')
    return redirect(url_for('purchase_order.detail', id=id))


@bp.route('/<int:id>/receive', methods=['POST'])
@login_required
@editor_required
def receive(id):
    """标记收货"""
    po = PurchaseOrder.query.get_or_404(id)
    if po.status != 'approved':
        flash('当前状态不允许收货操作。', 'warning')
        return redirect(url_for('purchase_order.detail', id=id))

    po.status = 'received'
    db.session.commit()
    flash('采购订单已标记收货。', 'success')
    return redirect(url_for('purchase_order.detail', id=id))


@bp.route('/<int:id>/close', methods=['POST'])
@login_required
@editor_required
def close(id):
    """关闭采购订单"""
    po = PurchaseOrder.query.get_or_404(id)
    if po.status not in ('received', 'approved'):
        flash('当前状态不允许关闭。', 'warning')
        return redirect(url_for('purchase_order.detail', id=id))

    po.status = 'closed'
    db.session.commit()
    flash('采购订单已关闭。', 'success')
    return redirect(url_for('purchase_order.detail', id=id))
