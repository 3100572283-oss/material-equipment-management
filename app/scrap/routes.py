from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy import func

from app.scrap import bp
from app import db
from app.models import (MaterialScrap, MaterialScrapItem, Material, Inventory,
                       InventoryBatch, UsageUnit, Project)
from app.decorators import log_audit
from app.utils import to_decimal, apply_data_scope, get_project_materials, upload_attachment


def _gen_scrap_code(project_id):
    """生成唯一报废单号：BF-{project_id}-{YYYYMMDD}-{序号}"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f'BF-{project_id}-{today}-'
    last = MaterialScrap.query.filter(
        MaterialScrap.code.like(f'{prefix}%')
    ).order_by(MaterialScrap.id.desc()).first()
    if last:
        seq = int(last.code.split('-')[-1]) + 1
    else:
        seq = 1
    return f'{prefix}{seq:03d}'


REASON_LABELS = {
    'expired': '过期',
    'damaged': '损坏',
    'unqualified': '不合格',
    'other': '其他',
}


@bp.route('/')
@login_required
def index():
    """报废单列表页"""
    project_id = session.get('current_project_id')
    # 全部数据权限用户在"全部项目"模式下不限制项目
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', '', type=str)
    start_date = request.args.get('start_date', '', type=str)
    end_date = request.args.get('end_date', '', type=str)

    query = MaterialScrap.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, MaterialScrap)
    if status:
        query = query.filter(MaterialScrap.approval_status == status)
    if start_date:
        try:
            query = query.filter(MaterialScrap.scrap_date >= datetime.strptime(start_date, '%Y-%m-%d').date())
        except Exception:
            pass
    if end_date:
        try:
            query = query.filter(MaterialScrap.scrap_date <= datetime.strptime(end_date, '%Y-%m-%d').date())
        except Exception:
            pass

    pagination = query.order_by(MaterialScrap.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False)

    return render_template('scrap/list.html', pagination=pagination,
                           status=status, start_date=start_date, end_date=end_date,
                           reason_labels=REASON_LABELS)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@log_audit(module='scrap', operation='新增')
def create():
    """新建报废单"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        scrap_date_str = request.form.get('scrap_date')
        try:
            scrap_date = datetime.strptime(scrap_date_str, '%Y-%m-%d').date()
        except Exception:
            scrap_date = date.today()

        usage_unit_id = request.form.get('usage_unit_id', type=int) or None
        reason = request.form.get('reason', 'other')
        remark = request.form.get('remark', '').strip()

        # 定位信息（移动端传入）
        location_lat = request.form.get('location_lat', type=float) or None
        location_lng = request.form.get('location_lng', type=float) or None
        location_accuracy = request.form.get('location_accuracy', type=float) or None
        location_time_str = request.form.get('location_time', '').strip()
        location_time = None
        if location_time_str:
            try:
                location_time = datetime.fromisoformat(location_time_str)
            except Exception:
                pass

        scrap = MaterialScrap(
            project_id=project_id,
            code=_gen_scrap_code(project_id),
            scrap_date=scrap_date,
            usage_unit_id=usage_unit_id,
            reason=reason,
            remark=remark,
            approval_status='draft',
            applicant_id=current_user.id,
            applicant_name=current_user.name or current_user.username,
            location_lat=location_lat,
            location_lng=location_lng,
            location_accuracy=location_accuracy,
            location_time=location_time,
        )
        db.session.add(scrap)
        db.session.flush()

        # 处理明细
        material_ids = request.form.getlist('material_id[]')
        batch_nos = request.form.getlist('batch_no[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        reason_details = request.form.getlist('reason_detail[]')

        total_qty = to_decimal(0)
        total_amount = to_decimal(0)
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
                price = to_decimal(unit_prices[idx] if idx < len(unit_prices) else 0)
            except Exception:
                continue
            if qty == 0:
                continue
            amount = float(qty) * float(price)
            batch_no = (batch_nos[idx] if idx < len(batch_nos) else '').strip() or None
            reason_detail = (reason_details[idx] if idx < len(reason_details) else '').strip() or None

            item = MaterialScrapItem(
                scrap_id=scrap.id,
                material_id=int(mid),
                batch_no=batch_no,
                quantity=qty,
                unit_price=price,
                amount=amount,
                reason_detail=reason_detail,
            )
            db.session.add(item)
            total_qty += qty
            total_amount += to_decimal(amount)

        scrap.total_quantity = total_qty
        scrap.total_amount = total_amount

        # 照片上传（多张）
        photos = request.files.getlist('photos[]')
        for photo in photos:
            if photo and photo.filename:
                att, err = upload_attachment(photo, 'scrap',
                                             biz_id=scrap.id, project_id=project_id)
                if err:
                    flash(f'照片 {photo.filename} 上传失败：{err}', 'warning')

        db.session.commit()

        # 如果是"提交并审批"按钮，自动提交审批流
        if request.form.get('submit_type') == 'submit':
            return _submit_to_approval(scrap)

        flash('报废单创建成功，可继续编辑或提交审批。', 'success')
        return redirect(url_for('scrap.detail', id=scrap.id))

    materials = get_project_materials(project_id, common_only=True).all()
    usage_units = UsageUnit.query.filter_by(project_id=project_id).order_by(UsageUnit.name).all()
    return render_template('scrap/create.html', materials=materials,
                           usage_units=usage_units,
                           default_code=_gen_scrap_code(project_id),
                           reason_labels=REASON_LABELS,
                           today=date.today().isoformat())


def _submit_to_approval(scrap):
    """提交报废单到审批流"""
    from app.approval.service import submit_approval, is_approval_enabled
    if not is_approval_enabled('scrap'):
        # 未启用审批流：直接通过并扣减库存
        scrap.approval_status = 'pending'  # 标记为待处理（兼容历史直审）
        from app.approval.service import _apply_scrap_inventory
        _apply_scrap_inventory(scrap)
        scrap.approval_status = 'passed'
        db.session.commit()
        flash('报废单已直接通过（未启用审批流），库存已扣减。', 'success')
        return redirect(url_for('scrap.detail', id=scrap.id))
    success, msg, instance = submit_approval('scrap', scrap.id,
                                              applicant_id=current_user.id,
                                              project_id=scrap.project_id)
    if success:
        scrap.approval_status = 'pending'
        scrap.approval_instance_id = instance.id if instance else None
        db.session.commit()
        flash('报废单已提交审批。', 'success')
    else:
        flash(f'提交审批失败：{msg}', 'danger')
    return redirect(url_for('scrap.detail', id=scrap.id))


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@log_audit(module='scrap', operation='提交审批')
def submit(id):
    """提交报废单到审批流"""
    scrap = MaterialScrap.query.get_or_404(id)
    if scrap.approval_status not in ('draft', 'rejected'):
        flash('当前状态不允许提交。', 'warning')
        return redirect(url_for('scrap.detail', id=scrap.id))
    return _submit_to_approval(scrap)


@bp.route('/<int:id>')
@login_required
def detail(id):
    """报废单详情"""
    scrap = MaterialScrap.query.get_or_404(id)
    return render_template('scrap/detail.html', scrap=scrap, reason_labels=REASON_LABELS)


@bp.route('/<int:id>/approve', methods=['POST'])
@login_required
@log_audit(module='scrap', operation='审批通过')
def approve(id):
    """审批通过：扣减库存"""
    scrap = MaterialScrap.query.get_or_404(id)
    if scrap.approval_status != 'draft':
        flash('当前报废单状态不允许审批。', 'danger')
        return redirect(url_for('scrap.detail', id=scrap.id))

    # 库存校验
    insufficient = []
    for item in scrap.items:
        if item.batch_no:
            batch = InventoryBatch.query.filter_by(
                project_id=scrap.project_id,
                material_id=item.material_id,
                batch_no=item.batch_no
            ).first()
            avail = float(batch.quantity) if batch else 0
        else:
            inv = Inventory.query.filter_by(
                project_id=scrap.project_id,
                material_id=item.material_id
            ).first()
            avail = float(inv.quantity) if inv else 0
        if float(item.quantity or 0) > avail:
            mat = Material.query.get(item.material_id)
            name = mat.name if mat else f'物资#{item.material_id}'
            insufficient.append(f'{name}（批次：{item.batch_no or "无"}）需 {float(item.quantity)}，可用 {avail}')

    if insufficient:
        flash('库存不足，无法审批通过：\n' + '；'.join(insufficient), 'danger')
        return redirect(url_for('scrap.detail', id=scrap.id))

    # 扣减库存
    for item in scrap.items:
        if item.batch_no:
            batch = InventoryBatch.query.filter_by(
                project_id=scrap.project_id,
                material_id=item.material_id,
                batch_no=item.batch_no
            ).first()
            if batch:
                batch.quantity = to_decimal(batch.quantity) - to_decimal(item.quantity)
        else:
            inv = Inventory.query.filter_by(
                project_id=scrap.project_id,
                material_id=item.material_id
            ).first()
            if inv:
                inv.quantity = to_decimal(inv.quantity) - to_decimal(item.quantity)

    scrap.approval_status = 'passed'
    db.session.commit()
    flash('审批通过，库存已扣减。', 'success')
    return redirect(url_for('scrap.detail', id=scrap.id))


@bp.route('/<int:id>/reject', methods=['POST'])
@login_required
@log_audit(module='scrap', operation='审批驳回')
def reject(id):
    """审批驳回"""
    scrap = MaterialScrap.query.get_or_404(id)
    if scrap.approval_status != 'draft':
        flash('当前报废单状态不允许驳回。', 'danger')
        return redirect(url_for('scrap.detail', id=scrap.id))

    scrap.approval_status = 'rejected'
    db.session.commit()
    flash('报废单已驳回。', 'warning')
    return redirect(url_for('scrap.detail', id=scrap.id))


@bp.route('/stats')
@login_required
def stats():
    """报废统计报表"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    # 按原因汇总（仅统计已通过）
    by_reason = db.session.query(
        MaterialScrap.reason,
        func.coalesce(func.sum(MaterialScrapItem.quantity), 0),
        func.coalesce(func.sum(MaterialScrapItem.amount), 0)
    ).join(MaterialScrapItem, MaterialScrapItem.scrap_id == MaterialScrap.id
    ).filter(
        MaterialScrap.project_id == project_id,
        MaterialScrap.approval_status == 'passed'
    ).group_by(MaterialScrap.reason).all()

    reason_rows = []
    for reason, qty, amt in by_reason:
        reason_rows.append({
            'reason': reason,
            'label': REASON_LABELS.get(reason, reason),
            'quantity': float(qty),
            'amount': float(amt),
        })

    # 按分类汇总
    from app.models import Category
    by_category = db.session.query(
        Category.name,
        func.coalesce(func.sum(MaterialScrapItem.quantity), 0),
        func.coalesce(func.sum(MaterialScrapItem.amount), 0)
    ).select_from(MaterialScrapItem
    ).join(MaterialScrap, MaterialScrapItem.scrap_id == MaterialScrap.id
    ).join(Material, MaterialScrapItem.material_id == Material.id
    ).outerjoin(Category, Material.category_id == Category.id
    ).filter(
        MaterialScrap.project_id == project_id,
        MaterialScrap.approval_status == 'passed'
    ).group_by(Category.name).all()

    category_rows = []
    for cat_name, qty, amt in by_category:
        category_rows.append({
            'category': cat_name or '未分类',
            'quantity': float(qty),
            'amount': float(amt),
        })

    # 按月份汇总
    by_month = db.session.query(
        func.strftime('%Y-%m', MaterialScrap.scrap_date),
        func.coalesce(func.sum(MaterialScrapItem.quantity), 0),
        func.coalesce(func.sum(MaterialScrapItem.amount), 0)
    ).select_from(MaterialScrap
    ).join(MaterialScrapItem, MaterialScrapItem.scrap_id == MaterialScrap.id
    ).filter(
        MaterialScrap.project_id == project_id,
        MaterialScrap.approval_status == 'passed'
    ).group_by(func.strftime('%Y-%m', MaterialScrap.scrap_date)
    ).order_by(func.strftime('%Y-%m', MaterialScrap.scrap_date).desc()).all()

    month_rows = []
    for ym, qty, amt in by_month:
        month_rows.append({
            'month': ym,
            'quantity': float(qty),
            'amount': float(amt),
        })

    return render_template('scrap/stats.html',
                           reason_rows=reason_rows,
                           category_rows=category_rows,
                           month_rows=month_rows)
