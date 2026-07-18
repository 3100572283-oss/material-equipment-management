import os
import uuid
from datetime import datetime, date, timedelta
from flask import render_template, request, redirect, url_for, flash, jsonify, session, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import func
from app.equipment import bp
from app import db
from app.models import (
    Equipment, EquipmentMaintenance, EquipmentStatusLog, EquipmentRentSettle,
    Project, Supplier
)
from app.decorators import editor_required, log_audit
from app.utils import log_operation, get_dict_items


def _get_project_id():
    pid = session.get('current_project_id')
    if not pid:
        flash('请先选择项目', 'warning')
    return pid


def _parse_date(value, fmt='%Y-%m-%d'):
    if not value:
        return None
    try:
        return datetime.strptime(value, fmt).date()
    except (ValueError, TypeError):
        return None


def _to_float(value, default=0):
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        if value is None or value == '':
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _save_photo(file):
    """保存设备照片，返回相对路径（如 uploads/xxx.jpg）"""
    if not file or not file.filename:
        return None
    ext = os.path.splitext(secure_filename(file.filename))[1]
    if not ext:
        ext = '.jpg'
    filename = f'eq_{uuid.uuid4().hex}{ext}'
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    file.save(os.path.join(upload_dir, filename))
    return f'uploads/{filename}'


def _gen_equipment_code(project_id):
    """生成设备编号：SB-{project_id}-{count+1:04d}"""
    count = Equipment.query.filter_by(project_id=project_id).count()
    return f'SB-{project_id}-{count + 1:04d}'


def _gen_settle_no():
    """生成结算单号：ZJ+年月日+3位序号，如 ZJ20260718001"""
    date_str = datetime.now().strftime('%Y%m%d')
    base = f'ZJ{date_str}'
    max_code = db.session.query(func.max(EquipmentRentSettle.settle_no)).filter(
        EquipmentRentSettle.settle_no.like(f'{base}%')
    ).scalar()
    if max_code:
        try:
            seq = int(max_code[-3:]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f'{base}{seq:03d}'


def _common_form_ctx():
    """表单页面通用上下文"""
    return {
        'category_items': get_dict_items('equipment_category'),
        'source_items': get_dict_items('equipment_source'),
        'status_items': get_dict_items('equipment_status'),
        'rent_type_items': get_dict_items('rent_type'),
    }


# ========== 设备台账 ==========

@bp.route('/')
@login_required
def index():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    category = request.args.get('category', '', type=str)
    status = request.args.get('status', '', type=str)
    source_type = request.args.get('source_type', '', type=str)

    query = Equipment.query.filter_by(project_id=project_id, is_deleted=False)
    if keyword:
        query = query.filter(Equipment.name.contains(keyword) | Equipment.code.contains(keyword))
    if category:
        query = query.filter_by(category=category)
    if status:
        query = query.filter_by(status=status)
    if source_type:
        query = query.filter_by(source_type=source_type)
    pagination = query.order_by(Equipment.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False
    )

    base_q = Equipment.query.filter_by(project_id=project_id, is_deleted=False)
    stats = {
        'total': base_q.count(),
        'self_count': base_q.filter_by(source_type='self').count(),
        'rent_count': base_q.filter_by(source_type='rent').count(),
        'in_use': base_q.filter_by(status='in_use').count(),
        'repairing': base_q.filter_by(status='repairing').count(),
        'exited': base_q.filter_by(status='exited').count(),
    }

    return render_template(
        'equipment/index.html',
        pagination=pagination, keyword=keyword, category=category,
        status=status, source_type=source_type, stats=stats,
        category_items=get_dict_items('equipment_category'),
        status_items=get_dict_items('equipment_status'),
        source_items=get_dict_items('equipment_source'),
    )


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='新增设备')
def create():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        source_type = request.form.get('source_type', 'self')
        eq = Equipment(
            project_id=project_id,
            code=request.form.get('code', '').strip() or _gen_equipment_code(project_id),
            name=request.form.get('name', '').strip(),
            source_type=source_type,
            category=request.form.get('category', '') or None,
            specification=request.form.get('specification', '') or None,
            manufacturer=request.form.get('manufacturer', '') or None,
            department=request.form.get('department', '') or None,
            responsible=request.form.get('responsible', '') or None,
            location=request.form.get('location', '') or None,
            status=request.form.get('status', 'in_use'),
            remark=request.form.get('remark', '') or None,
        )
        # 自有设备字段
        if source_type == 'self':
            eq.purchase_date = _parse_date(request.form.get('purchase_date'))
            eq.original_value = _to_float(request.form.get('original_value'), 0)
            eq.use_years = _to_int(request.form.get('use_years'), 0)
            eq.residual_rate = _to_float(request.form.get('residual_rate'), 5)
        # 租赁设备字段
        elif source_type == 'rent':
            eq.supplier_id = _to_int(request.form.get('supplier_id'), 0) or None
            eq.rent_type = request.form.get('rent_type', '') or None
            eq.rent_unit_price = _to_float(request.form.get('rent_unit_price'), 0)
            eq.rent_period = _to_int(request.form.get('rent_period'), 0) or None
            eq.entry_date = _parse_date(request.form.get('entry_date'))
            eq.expected_exit_date = _parse_date(request.form.get('expected_exit_date'))
            eq.entry_exit_fee = _to_float(request.form.get('entry_exit_fee'), 0)
            eq.deposit = _to_float(request.form.get('deposit'), 0)
        # 劳务队自带字段
        elif source_type == 'labor':
            eq.labor_team = request.form.get('labor_team', '') or None
            eq.entry_date = _parse_date(request.form.get('entry_date'))

        photo = request.files.get('photo')
        photo_path = _save_photo(photo)
        if photo_path:
            eq.photo = photo_path

        db.session.add(eq)
        db.session.commit()
        log_operation('新增', module='设备台账', description=f'新增设备 {eq.name}')
        flash('设备添加成功', 'success')
        return redirect(url_for('equipment.index'))

    default_code = _gen_equipment_code(project_id)
    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    ctx = _common_form_ctx()
    ctx.update(equipment=None, default_code=default_code, suppliers=suppliers)
    return render_template('equipment/form.html', **ctx)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='编辑设备')
def edit(id):
    eq = Equipment.query.get_or_404(id)
    if request.method == 'POST':
        source_type = request.form.get('source_type', eq.source_type or 'self')
        eq.name = request.form.get('name', '').strip()
        eq.source_type = source_type
        eq.category = request.form.get('category', '') or None
        eq.specification = request.form.get('specification', '') or None
        eq.manufacturer = request.form.get('manufacturer', '') or None
        eq.department = request.form.get('department', '') or None
        eq.responsible = request.form.get('responsible', '') or None
        eq.location = request.form.get('location', '') or None
        eq.status = request.form.get('status', 'in_use')
        eq.remark = request.form.get('remark', '') or None

        # 自有设备字段
        if source_type == 'self':
            eq.purchase_date = _parse_date(request.form.get('purchase_date'))
            eq.original_value = _to_float(request.form.get('original_value'), 0)
            eq.use_years = _to_int(request.form.get('use_years'), 0)
            eq.residual_rate = _to_float(request.form.get('residual_rate'), 5)
            # 清空租赁/劳务字段
            eq.supplier_id = None
            eq.rent_type = None
            eq.rent_unit_price = 0
            eq.rent_period = None
            eq.entry_date = None
            eq.expected_exit_date = None
            eq.entry_exit_fee = 0
            eq.deposit = 0
            eq.labor_team = None
        # 租赁设备字段
        elif source_type == 'rent':
            eq.supplier_id = _to_int(request.form.get('supplier_id'), 0) or None
            eq.rent_type = request.form.get('rent_type', '') or None
            eq.rent_unit_price = _to_float(request.form.get('rent_unit_price'), 0)
            eq.rent_period = _to_int(request.form.get('rent_period'), 0) or None
            eq.entry_date = _parse_date(request.form.get('entry_date'))
            eq.expected_exit_date = _parse_date(request.form.get('expected_exit_date'))
            eq.entry_exit_fee = _to_float(request.form.get('entry_exit_fee'), 0)
            eq.deposit = _to_float(request.form.get('deposit'), 0)
            # 清空自有/劳务字段
            eq.purchase_date = None
            eq.original_value = 0
            eq.use_years = 0
            eq.residual_rate = 5
            eq.labor_team = None
        # 劳务队自带字段
        elif source_type == 'labor':
            eq.labor_team = request.form.get('labor_team', '') or None
            eq.entry_date = _parse_date(request.form.get('entry_date'))
            # 清空自有/租赁字段
            eq.purchase_date = None
            eq.original_value = 0
            eq.use_years = 0
            eq.residual_rate = 5
            eq.supplier_id = None
            eq.rent_type = None
            eq.rent_unit_price = 0
            eq.rent_period = None
            eq.expected_exit_date = None
            eq.entry_exit_fee = 0
            eq.deposit = 0

        photo = request.files.get('photo')
        photo_path = _save_photo(photo)
        if photo_path:
            eq.photo = photo_path

        db.session.commit()
        log_operation('编辑', module='设备台账', description=f'编辑设备 {eq.name}')
        flash('设备更新成功', 'success')
        return redirect(url_for('equipment.index'))

    suppliers = Supplier.query.filter_by(project_id=eq.project_id).order_by(Supplier.name).all()
    ctx = _common_form_ctx()
    ctx.update(equipment=eq, default_code=eq.code, suppliers=suppliers)
    return render_template('equipment/form.html', **ctx)


@bp.route('/<int:id>/detail')
@login_required
def detail(id):
    eq = Equipment.query.get_or_404(id)
    maintenances = EquipmentMaintenance.query.filter_by(equipment_id=id).order_by(
        EquipmentMaintenance.maintain_date.desc()
    ).all()
    status_logs = EquipmentStatusLog.query.filter_by(equipment_id=id).order_by(
        EquipmentStatusLog.created_at.desc()
    ).all()
    rent_settles = EquipmentRentSettle.query.filter_by(equipment_id=id).order_by(
        EquipmentRentSettle.created_at.desc()
    ).all()
    return render_template(
        'equipment/detail.html',
        equipment=eq, maintenances=maintenances,
        status_logs=status_logs, rent_settles=rent_settles,
        status_items=get_dict_items('equipment_status'),
    )


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='删除设备')
def delete(id):
    eq = Equipment.query.get_or_404(id)
    # 软删除
    eq.is_deleted = True
    eq.deleted_at = datetime.now()
    db.session.commit()
    log_operation('删除', module='设备台账', description=f'软删除设备 {eq.name}')
    flash('设备删除成功', 'success')
    return redirect(url_for('equipment.index'))


# ========== 设备状态变更 ==========

@bp.route('/<int:id>/change_status', methods=['POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='设备状态变更')
def change_status(id):
    eq = Equipment.query.get_or_404(id)
    old_status = eq.status
    new_status = request.form.get('new_status', '').strip()
    exit_date_str = request.form.get('exit_date', '').strip()
    remark = request.form.get('remark', '').strip()

    if not new_status:
        flash('请选择新状态', 'danger')
        return redirect(url_for('equipment.detail', id=id))

    exit_date = _parse_date(exit_date_str)
    if new_status == 'exited' and not exit_date:
        flash('设备退场必须填写退场日期', 'danger')
        return redirect(url_for('equipment.detail', id=id))

    log = EquipmentStatusLog(
        equipment_id=id,
        old_status=old_status,
        new_status=new_status,
        exit_date=exit_date,
        operator_id=current_user.id if current_user.is_authenticated else None,
        operator_name=current_user.name or current_user.username if current_user.is_authenticated else None,
        remark=remark or None,
    )
    db.session.add(log)

    eq.status = new_status
    if new_status == 'exited' and exit_date:
        eq.exit_date = exit_date

    db.session.commit()
    log_operation('状态变更', module='设备台账',
                  description=f'设备 {eq.name} 状态由 {old_status} 变更为 {new_status}')
    flash('设备状态变更成功', 'success')
    return redirect(url_for('equipment.detail', id=id))


# ========== 设备租赁结算 ==========

@bp.route('/rent_settle')
@login_required
def rent_settle():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    supplier_id = request.args.get('supplier_id', 0, type=int)
    settle_period = request.args.get('settle_period', '', type=str)
    status = request.args.get('status', '', type=str)

    query = EquipmentRentSettle.query.join(Equipment).filter(
        Equipment.project_id == project_id
    )
    if keyword:
        query = query.filter(
            Equipment.name.contains(keyword) | EquipmentRentSettle.settle_no.contains(keyword)
        )
    if supplier_id:
        query = query.filter(EquipmentRentSettle.supplier_id == supplier_id)
    if settle_period:
        query = query.filter(EquipmentRentSettle.settle_period == settle_period)
    if status:
        query = query.filter(EquipmentRentSettle.status == status)

    pagination = query.order_by(EquipmentRentSettle.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False
    )

    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    return render_template(
        'equipment/rent_settle.html',
        pagination=pagination, keyword=keyword, supplier_id=supplier_id,
        settle_period=settle_period, status=status,
        suppliers=suppliers,
        rent_type_items=get_dict_items('rent_type'),
    )


@bp.route('/rent_settle/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='新增结算单')
def rent_settle_create():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        equipment_id = _to_int(request.form.get('equipment_id'), 0)
        eq = Equipment.query.get_or_404(equipment_id)
        unit_price = _to_float(request.form.get('unit_price'), 0)
        quantity = _to_float(request.form.get('quantity'), 0)
        rent_amount = round(unit_price * quantity, 2)
        other_fee = _to_float(request.form.get('other_fee'), 0)
        total_amount = round(rent_amount + other_fee, 2)

        settle = EquipmentRentSettle(
            settle_no=_gen_settle_no(),
            equipment_id=equipment_id,
            supplier_id=eq.supplier_id,
            settle_period=request.form.get('settle_period', '') or None,
            settle_start_date=_parse_date(request.form.get('settle_start_date')),
            settle_end_date=_parse_date(request.form.get('settle_end_date')),
            rent_type=request.form.get('rent_type', '') or eq.rent_type or None,
            unit_price=unit_price,
            quantity=quantity,
            rent_amount=rent_amount,
            other_fee=other_fee,
            total_amount=total_amount,
            status='draft',
            remark=request.form.get('remark', '') or None,
        )
        db.session.add(settle)
        db.session.commit()
        log_operation('新增', module='设备租赁结算', description=f'新增结算单 {settle.settle_no}')
        flash('结算单创建成功', 'success')
        return redirect(url_for('equipment.rent_settle'))

    rent_equipments = Equipment.query.filter_by(
        project_id=project_id, source_type='rent', is_deleted=False
    ).order_by(Equipment.name).all()
    return render_template(
        'equipment/rent_settle_form.html',
        settle=None, rent_equipments=rent_equipments,
        rent_type_items=get_dict_items('rent_type'),
    )


@bp.route('/rent_settle/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='编辑结算单')
def rent_settle_edit(id):
    settle = EquipmentRentSettle.query.get_or_404(id)
    if request.method == 'POST':
        equipment_id = _to_int(request.form.get('equipment_id'), 0)
        eq = Equipment.query.get_or_404(equipment_id)
        unit_price = _to_float(request.form.get('unit_price'), 0)
        quantity = _to_float(request.form.get('quantity'), 0)
        rent_amount = round(unit_price * quantity, 2)
        other_fee = _to_float(request.form.get('other_fee'), 0)
        total_amount = round(rent_amount + other_fee, 2)

        settle.equipment_id = equipment_id
        settle.supplier_id = eq.supplier_id
        settle.settle_period = request.form.get('settle_period', '') or None
        settle.settle_start_date = _parse_date(request.form.get('settle_start_date'))
        settle.settle_end_date = _parse_date(request.form.get('settle_end_date'))
        settle.rent_type = request.form.get('rent_type', '') or eq.rent_type or None
        settle.unit_price = unit_price
        settle.quantity = quantity
        settle.rent_amount = rent_amount
        settle.other_fee = other_fee
        settle.total_amount = total_amount
        settle.remark = request.form.get('remark', '') or None
        db.session.commit()
        log_operation('编辑', module='设备租赁结算', description=f'编辑结算单 {settle.settle_no}')
        flash('结算单更新成功', 'success')
        return redirect(url_for('equipment.rent_settle'))

    rent_equipments = Equipment.query.filter_by(
        project_id=settle.equipment.project_id, source_type='rent', is_deleted=False
    ).order_by(Equipment.name).all()
    return render_template(
        'equipment/rent_settle_form.html',
        settle=settle, rent_equipments=rent_equipments,
        rent_type_items=get_dict_items('rent_type'),
    )


@bp.route('/rent_settle/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='删除结算单')
def rent_settle_delete(id):
    settle = EquipmentRentSettle.query.get_or_404(id)
    settle_no = settle.settle_no
    db.session.delete(settle)
    db.session.commit()
    log_operation('删除', module='设备租赁结算', description=f'删除结算单 {settle_no}')
    flash('结算单删除成功', 'success')
    return redirect(url_for('equipment.rent_settle'))


@bp.route('/rent_settle/<int:id>/confirm', methods=['POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='确认结算单')
def rent_settle_confirm(id):
    settle = EquipmentRentSettle.query.get_or_404(id)
    if settle.status != 'draft':
        flash('只有草稿状态的结算单才能确认', 'warning')
        return redirect(url_for('equipment.rent_settle'))
    settle.status = 'confirmed'
    db.session.commit()
    log_operation('确认', module='设备租赁结算', description=f'确认结算单 {settle.settle_no}')
    flash('结算单已确认', 'success')
    return redirect(url_for('equipment.rent_settle'))


@bp.route('/rent_settle/api/equipment/<int:eid>')
@login_required
def rent_settle_api_equipment(eid):
    """获取租赁设备信息（供应商、租赁方式、单价）"""
    eq = Equipment.query.get_or_404(eid)
    return jsonify({
        'supplier_id': eq.supplier_id or 0,
        'supplier_name': eq.supplier.name if eq.supplier else '',
        'rent_type': eq.rent_type or '',
        'unit_price': float(eq.rent_unit_price or 0),
    })


# ========== 设备详情 & 维保 ==========

@bp.route('/<int:id>/maintenance/add', methods=['POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='新增维保')
def add_maintenance(id):
    eq = Equipment.query.get_or_404(id)
    m = EquipmentMaintenance(
        equipment_id=id,
        maintain_date=_parse_date(request.form.get('maintain_date')) or date.today(),
        maintain_type=request.form.get('maintain_type', '保养'),
        content=request.form.get('content', ''),
        cost=_to_float(request.form.get('cost'), 0),
        vendor=request.form.get('vendor', ''),
        next_maintain_date=_parse_date(request.form.get('next_maintain_date')),
        operator=request.form.get('operator', '') or (current_user.name or current_user.username),
        remark=request.form.get('remark', '')
    )
    db.session.add(m)
    db.session.commit()
    flash('维保记录添加成功', 'success')
    return redirect(url_for('equipment.detail', id=id))


@bp.route('/maintenance/<int:mid>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='equipment', operation='删除维保')
def delete_maintenance(mid):
    m = EquipmentMaintenance.query.get_or_404(mid)
    eq_id = m.equipment_id
    db.session.delete(m)
    db.session.commit()
    flash('维保记录删除成功', 'success')
    return redirect(url_for('equipment.detail', id=eq_id))


# ========== 折旧明细 ==========

@bp.route('/depreciation')
@login_required
def depreciation():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    equipments = Equipment.query.filter_by(
        project_id=project_id, is_deleted=False, source_type='self'
    ).order_by(Equipment.created_at.desc()).all()
    total_original = sum(float(e.original_value or 0) for e in equipments)
    total_net = sum(e.net_value for e in equipments)
    total_depreciated = total_original - total_net
    return render_template(
        'equipment/depreciation.html', equipments=equipments,
        total_original=round(total_original, 2),
        total_net=round(total_net, 2),
        total_depreciated=round(total_depreciated, 2)
    )


# ========== API ==========

@bp.route('/api/expiring_maintenance')
@login_required
def api_expiring_maintenance():
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])
    today = date.today()
    warning_date = today + timedelta(days=30)
    records = EquipmentMaintenance.query.join(Equipment).filter(
        Equipment.project_id == project_id,
        Equipment.is_deleted == False,
        EquipmentMaintenance.next_maintain_date != None,
        EquipmentMaintenance.next_maintain_date <= warning_date
    ).order_by(EquipmentMaintenance.next_maintain_date.asc()).all()
    result = []
    for r in records:
        status = 'expired' if r.next_maintain_date < today else 'warning'
        result.append({
            'equipment_name': r.equipment.name if r.equipment else '-',
            'maintain_type': r.maintain_type,
            'next_date': r.next_maintain_date.strftime('%Y-%m-%d'),
            'status': status
        })
    return jsonify(result)
