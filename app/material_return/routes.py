from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.material_return import bp
from app import db
from app.models import (MaterialReturn, MaterialReturnItem, Material, Supplier,
                       StockOut, StockOutItem, StockIn, StockInItem, Project,
                       Inventory, InventoryBatch)
from app.decorators import editor_required, log_audit
from app.utils import to_decimal, apply_data_scope, get_project_materials


def _gen_return_number(project_id):
    """生成唯一退料单号：TL-{YYYYMMDD}-{序号}"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f'TL-{today}-'
    last = MaterialReturn.query.filter(
        MaterialReturn.return_number.like(f'{prefix}%')
    ).order_by(MaterialReturn.id.desc()).first()
    if last:
        try:
            seq = int(last.return_number.split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f'{prefix}{seq:04d}'


def _update_return_total(return_id):
    """更新退料单总金额"""
    mr = MaterialReturn.query.get(return_id)
    if not mr:
        return
    items = MaterialReturnItem.query.filter_by(return_id=return_id).all()
    total = sum(float(item.total_price or 0) for item in items)
    mr.total_amount = total
    db.session.commit()


def _create_stockin_for_return(mr):
    """退料审批通过后，自动创建入库单（退料入库）"""
    from app.utils import _gen_code_with_seq
    from app.services.inventory_cost import InventoryCostService

    # 生成入库单号
    code = _gen_code_with_seq('RK', mr.project_id, StockIn)

    stock_in = StockIn(
        project_id=mr.project_id,
        code=code,
        stock_in_date=date.today(),
        stock_in_type='退货入库',
        supplier_id=None,
        operator=(current_user.name or current_user.username) if current_user.is_authenticated else 'system',
        remark=f'退料单号：{mr.return_number}（审批通过自动生成）',
        total_quantity=sum(float(item.quantity or 0) for item in mr.items),
        total_amount=float(mr.total_amount or 0),
        approval_status='passed',
        status='approved',
    )
    db.session.add(stock_in)
    db.session.flush()

    # 入库明细
    for item in mr.items.all():
        si_item = StockInItem(
            stock_in_id=stock_in.id,
            material_id=item.material_id,
            quantity=item.quantity,
            unit_price=item.unit_price,
            amount=item.total_price,
        )
        db.session.add(si_item)

    db.session.commit()

    # 更新库存
    for item in mr.items.all():
        # 更新或创建库存记录
        inv = Inventory.query.filter_by(
            project_id=mr.project_id,
            material_id=item.material_id
        ).first()
        if inv:
            inv.quantity = float(inv.quantity or 0) + float(item.quantity or 0)
            inv.actual_amount = float(inv.actual_amount or 0) + float(item.total_price or 0)
        else:
            inv = Inventory(
                project_id=mr.project_id,
                material_id=item.material_id,
                quantity=item.quantity,
                actual_amount=item.total_price,
            )
            db.session.add(inv)

    db.session.commit()
    return stock_in


@bp.route('/')
@login_required
def index():
    """退料单列表页"""
    project_id = session.get('current_project_id')
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    status = request.args.get('status', '', type=str)

    query = MaterialReturn.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, MaterialReturn)

    if keyword:
        query = query.filter(or_(
            MaterialReturn.return_number.contains(keyword),
        ))
    if status:
        query = query.filter_by(status=status)

    pagination = query.order_by(MaterialReturn.created_at.desc()).paginate(
        page=page, per_page=15, error_out=False)

    return render_template('material_return/list.html', pagination=pagination,
                           keyword=keyword, status=status)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='material_return', operation='新增')
def create():
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        return_number = request.form.get('return_number', '').strip()
        if not return_number:
            return_number = _gen_return_number(project_id)

        stock_out_id = request.form.get('stock_out_id', type=int)

        mr = MaterialReturn(
            return_number=return_number,
            project_id=project_id,
            stock_out_id=stock_out_id,
            status='draft',
            return_type=request.form.get('return_type', '部分退料'),
            created_by=current_user.id,
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(mr)
        db.session.flush()

        # 明细
        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        reasons = request.form.getlist('reason[]')
        remarks = request.form.getlist('item_remark[]')

        for i, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = to_decimal(quantities[i]) if i < len(quantities) else 0
            price = to_decimal(unit_prices[i]) if i < len(unit_prices) else 0
            total = float(qty) * float(price)
            item = MaterialReturnItem(
                return_id=mr.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=price,
                total_price=total,
                reason=reasons[i] if i < len(reasons) else '',
                remark=remarks[i] if i < len(remarks) else ''
            )
            db.session.add(item)

        db.session.commit()
        _update_return_total(mr.id)
        flash('退料单创建成功。', 'success')
        return redirect(url_for('material_return.detail', id=mr.id))

    materials = get_project_materials(project_id).all()
    # 查找可关联的出库单
    stock_outs = StockOut.query.filter_by(project_id=project_id, is_deleted=False).order_by(StockOut.created_at.desc()).limit(50).all()
    default_return_number = _gen_return_number(project_id)
    return render_template('material_return/form.html',
                           materials=materials, stock_outs=stock_outs,
                           default_return_number=default_return_number, mr=None)


@bp.route('/<int:id>')
@login_required
def detail(id):
    mr = MaterialReturn.query.get_or_404(id)
    items = mr.items.all()
    return render_template('material_return/detail.html', mr=mr, items=items)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='material_return', operation='编辑')
def edit(id):
    mr = MaterialReturn.query.get_or_404(id)
    if mr.status not in ('draft', 'rejected'):
        flash('当前状态不允许编辑。', 'warning')
        return redirect(url_for('material_return.detail', id=id))

    if request.method == 'POST':
        mr.stock_out_id = request.form.get('stock_out_id', type=int)
        mr.return_type = request.form.get('return_type', '部分退料')
        mr.remark = request.form.get('remark', '').strip()

        # 删除旧明细
        MaterialReturnItem.query.filter_by(return_id=mr.id).delete()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        reasons = request.form.getlist('reason[]')
        remarks = request.form.getlist('item_remark[]')

        for i, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = to_decimal(quantities[i]) if i < len(quantities) else 0
            price = to_decimal(unit_prices[i]) if i < len(unit_prices) else 0
            total = float(qty) * float(price)
            item = MaterialReturnItem(
                return_id=mr.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=price,
                total_price=total,
                reason=reasons[i] if i < len(reasons) else '',
                remark=remarks[i] if i < len(remarks) else ''
            )
            db.session.add(item)

        db.session.commit()
        _update_return_total(mr.id)
        flash('退料单已更新。', 'success')
        return redirect(url_for('material_return.detail', id=mr.id))

    materials = get_project_materials(mr.project_id).all() if mr.project_id else []
    stock_outs = StockOut.query.filter_by(project_id=mr.project_id, is_deleted=False).order_by(StockOut.created_at.desc()).limit(50).all() if mr.project_id else []
    items = mr.items.all()
    return render_template('material_return/form.html',
                           mr=mr, materials=materials, stock_outs=stock_outs,
                           default_return_number=mr.return_number, items=items)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='material_return', operation='删除')
def delete(id):
    mr = MaterialReturn.query.get_or_404(id)
    if mr.status not in ('draft',):
        flash('当前状态不允许删除。', 'warning')
        return redirect(url_for('material_return.detail', id=id))

    db.session.delete(mr)
    db.session.commit()
    flash('退料单已删除。', 'success')
    return redirect(url_for('material_return.index'))


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@editor_required
def submit(id):
    """提交退料单"""
    mr = MaterialReturn.query.get_or_404(id)
    if mr.status not in ('draft', 'rejected'):
        flash('当前状态不允许提交。', 'warning')
        return redirect(url_for('material_return.detail', id=id))

    if mr.items.count() == 0:
        flash('请至少添加一条明细后再提交。', 'warning')
        return redirect(url_for('material_return.detail', id=id))

    mr.status = 'submitted'
    db.session.commit()

    # 如果没有配置审批流程，直接标记为已审批并创建退料入库
    from app.approval.service import is_approval_enabled
    if not is_approval_enabled('material_return', mr.project_id):
        mr.status = 'approved'
        db.session.commit()
        try:
            stock_in = _create_stockin_for_return(mr)
            flash(f'退料单已审批通过，已自动生成入库单 {stock_in.code}。', 'success')
        except Exception as e:
            import logging
            logging.error(f'退料入库生成失败: {e}')
            flash('退料单已审批，但自动入库失败，请手动处理。', 'warning')
    else:
        flash('退料单已提交审批。', 'success')

    return redirect(url_for('material_return.detail', id=id))


@bp.route('/<int:id>/approve', methods=['POST'])
@login_required
@editor_required
def approve(id):
    """审批通过退料单"""
    mr = MaterialReturn.query.get_or_404(id)
    if mr.status not in ('submitted',):
        flash('当前状态不允许审批。', 'warning')
        return redirect(url_for('material_return.detail', id=id))

    mr.status = 'approved'
    db.session.commit()

    try:
        stock_in = _create_stockin_for_return(mr)
        flash(f'退料单已审批通过，已自动生成入库单 {stock_in.code}。', 'success')
    except Exception as e:
        import logging
        logging.error(f'退料入库生成失败: {e}')
        flash('退料单已审批，但自动入库失败，请手动处理。', 'warning')

    return redirect(url_for('material_return.detail', id=id))


@bp.route('/<int:id>/reject', methods=['POST'])
@login_required
@editor_required
def reject(id):
    """驳回退料单"""
    mr = MaterialReturn.query.get_or_404(id)
    if mr.status not in ('submitted',):
        flash('当前状态不允许驳回。', 'warning')
        return redirect(url_for('material_return.detail', id=id))

    mr.status = 'rejected'
    db.session.commit()
    flash('退料单已驳回。', 'info')
    return redirect(url_for('material_return.detail', id=id))


@bp.route('/api/stock_out_items/<int:stock_out_id>')
@login_required
def api_stock_out_items(stock_out_id):
    """获取出库单明细（用于退料关联）"""
    stock_out = StockOut.query.get_or_404(stock_out_id)
    items = stock_out.items.all()
    result = []
    for item in items:
        result.append({
            'material_id': item.material_id,
            'material_name': item.material.name if item.material else '',
            'quantity': float(item.quantity or 0),
            'unit_price': float(item.unit_price or 0),
            'amount': float(item.amount or 0),
        })
    return jsonify({'items': result})
