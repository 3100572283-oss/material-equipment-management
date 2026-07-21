import os
import uuid
from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify, session, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from werkzeug.utils import secure_filename

from app.concrete import bp
from app import db
from app.models import (ConcreteTicket, Project, Supplier, WorkNumber,
                       StockIn, StockInItem, Material, Inventory, Contract,
                       ProjectMaterial)
from app.decorators import log_audit
from app.utils import to_decimal, gen_stock_in_code, apply_data_scope, get_project_materials


STRENGTH_GRADES = ['C15', 'C20', 'C25', 'C30', 'C35', 'C40', 'C45', 'C50']


def _gen_ticket_no(project_id):
    """生成小票号：CT-{project_id}-{YYYYMMDD}-{seq:03d}"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f'CT-{project_id}-{today}-'
    existing = ConcreteTicket.query.filter(
        ConcreteTicket.ticket_no.like(f'{prefix}%')
    ).count()
    return f'{prefix}{existing + 1:03d}'


def _save_photo(file):
    """保存小票随车照片，返回相对路径（如 uploads/xxx.jpg）"""
    if not file or not file.filename:
        return None
    ext = os.path.splitext(secure_filename(file.filename))[1]
    if not ext:
        ext = '.jpg'
    filename = f'concrete_{uuid.uuid4().hex}{ext}'
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    file.save(os.path.join(upload_dir, filename))
    return f'uploads/{filename}'


@bp.route('/api/gen_ticket_no')
@login_required
def api_gen_ticket_no():
    """自动生成小票号（前端AJAX调用）"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': '请先选择项目'}), 400
    return jsonify({'success': True, 'ticket_no': _gen_ticket_no(project_id)})


@bp.route('/api/supplier_contracts')
@login_required
def api_supplier_contracts():
    """获取供应商的有效合同列表（按供应商过滤）"""
    project_id = session.get('current_project_id')
    supplier_id = request.args.get('supplier_id', type=int)
    
    query = Contract.query.filter(
        Contract.status == '正常履约',
        Contract.is_deleted == False
    )
    
    if project_id:
        query = query.filter(Contract.project_id == project_id)
    
    if supplier_id:
        query = query.filter(Contract.supplier_id == supplier_id)
    
    contracts = query.order_by(Contract.code.desc()).all()
    
    result = [{
        'id': c.id,
        'code': c.code,
        'name': c.name,
        'contract_type': c.contract_type,
        'business_type': c.business_type
    } for c in contracts]
    
    return jsonify(result)


@bp.route('/api/contract_progress')
@login_required
def api_contract_progress():
    """获取合同供货进度：合同总量、已供货量、剩余量

    已供货量 = 该合同下所有有效小票方量之和（含未对账、已对账，不含作废）
    合同总量取合同 amount_with_tax 字段（数值，按方量口径）
    """
    contract_id = request.args.get('contract_id', type=int)
    if not contract_id:
        return jsonify({'success': False, 'message': '缺少 contract_id'}), 400

    contract = Contract.query.get(contract_id)
    if not contract:
        return jsonify({'success': False, 'message': '合同不存在'}), 404

    total_amount = float(contract.amount_with_tax or 0)
    tickets = ConcreteTicket.query.filter_by(contract_id=contract_id).all()
    supplied = sum(float(t.volume or 0) for t in tickets)
    remaining = round(total_amount - supplied, 2)
    percent = (supplied / total_amount * 100) if total_amount > 0 else 0

    return jsonify({
        'success': True,
        'total': round(total_amount, 2),
        'supplied': round(supplied, 2),
        'remaining': remaining,
        'percent': round(percent, 2),
        'exceeded': remaining < 0
    })


@bp.route('/api/project_concrete_materials')
@login_required
def api_project_concrete_materials():
    """获取项目常用材料表中的商砼/混凝土物资

    用于小票标号选择，与入库/出库物资选择规则一致。
    """
    project_id = session.get('current_project_id')

    keyword = (request.args.get('keyword') or '').strip()

    if project_id:
        query = get_project_materials(project_id, common_only=True).filter(
            or_(
                Material.name.like('%商砼%'),
                Material.name.like('%混凝土%'),
                Material.name.like('%砼%'),
            )
        )
    else:
        query = Material.query.filter(
            Material.status == 'active',
            or_(
                Material.name.like('%商砼%'),
                Material.name.like('%混凝土%'),
                Material.name.like('%砼%'),
            )
        )
    if keyword:
        query = query.filter(
            or_(Material.name.like(f'%{keyword}%'), Material.code.like(f'%{keyword}%'))
        )
    materials = query.all()

    return jsonify([{
        'id': m.id,
        'code': m.code or '',
        'name': m.name,
        'specification': m.specification or '',
        'unit': m.unit or '',
    } for m in materials])


@bp.route('/api/mark_reconciled', methods=['POST'])
@login_required
def api_mark_reconciled():
    """标记小票为已对账（批量）"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': '请先选择项目'}), 400
    ids = request.form.getlist('ids[]') or request.json.get('ids', []) if request.is_json else request.form.getlist('ids[]')
    if not ids:
        return jsonify({'success': False, 'message': '未选择小票'}), 400
    updated = 0
    for tid in ids:
        try:
            t = ConcreteTicket.query.filter_by(id=int(tid), project_id=project_id).first()
            if t and not t.is_reconciled:
                t.is_reconciled = True
                updated += 1
        except Exception:
            continue
    db.session.commit()
    return jsonify({'success': True, 'updated': updated})


@bp.route('/')
@login_required
def index():
    """商砼小票列表页"""
    project_id = session.get('current_project_id')
    # 全部数据权限用户在"全部项目"模式下不限制项目
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    supplier_id = request.args.get('supplier_id', '', type=str)
    contract_id = request.args.get('contract_id', '', type=str)
    strength_grade = request.args.get('strength_grade', '', type=str)
    pour_part = request.args.get('pour_part', '', type=str).strip()
    start_date = request.args.get('start_date', '', type=str)
    end_date = request.args.get('end_date', '', type=str)
    is_reconciled = request.args.get('is_reconciled', '', type=str)

    query = ConcreteTicket.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, ConcreteTicket)
    if supplier_id:
        query = query.filter(ConcreteTicket.supplier_id == int(supplier_id))
    if contract_id:
        query = query.filter(ConcreteTicket.contract_id == int(contract_id))
    if strength_grade:
        query = query.filter(ConcreteTicket.strength_grade == strength_grade)
    if pour_part:
        query = query.filter(ConcreteTicket.pour_part.like(f'%{pour_part}%'))
    if start_date:
        try:
            query = query.filter(ConcreteTicket.arrival_time >= datetime.strptime(start_date, '%Y-%m-%d'))
        except Exception:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            query = query.filter(ConcreteTicket.arrival_time <= end_dt)
        except Exception:
            pass
    if is_reconciled in ('0', '1'):
        query = query.filter(ConcreteTicket.is_reconciled == (is_reconciled == '1'))

    pagination = query.order_by(ConcreteTicket.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False)

    if project_id:
        suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
        contracts = Contract.query.filter(
            Contract.project_id == project_id,
            Contract.status == '正常履约',
            Contract.is_deleted == False
        ).order_by(Contract.code.desc()).all()
    else:
        suppliers = Supplier.query.filter_by(is_deleted=False).order_by(Supplier.name).all()
        contracts = Contract.query.filter(
            Contract.status == '正常履约',
            Contract.is_deleted == False
        ).order_by(Contract.code.desc()).all()
    return render_template('concrete/list.html', pagination=pagination,
                           suppliers=suppliers, contracts=contracts,
                           strength_grades=STRENGTH_GRADES,
                           supplier_id=supplier_id, contract_id=contract_id,
                           strength_grade=strength_grade,
                           pour_part=pour_part, start_date=start_date, end_date=end_date,
                           is_reconciled=is_reconciled)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@log_audit(module='concrete', operation='新增')
def create():
    """新建商砼小票"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        ticket_no = request.form.get('ticket_no', '').strip()
        if not ticket_no:
            # 自动生成
            ticket_no = _gen_ticket_no(project_id)

        supplier_id = request.form.get('supplier_id', type=int) or None
        supplier_name = request.form.get('supplier_name', '').strip() or None
        contract_id = request.form.get('contract_id', type=int) or None
        strength_grade = request.form.get('strength_grade', '').strip() or None
        material_id = request.form.get('material_id', type=int) or None
        pour_part = request.form.get('pour_part', '').strip() or None
        work_number_id = request.form.get('work_number_id', type=int) or None
        vehicle_count = request.form.get('vehicle_count', type=int) or 1
        volume = request.form.get('volume', type=float) or 0
        vehicle_no = request.form.get('vehicle_no', '').strip() or None
        driver_name = request.form.get('driver_name', '').strip() or None
        slump = request.form.get('slump', '').strip() or None
        temperature = request.form.get('temperature', '').strip() or None
        remark = request.form.get('remark', '').strip() or None

        arrival_time = None
        arrival_time_str = request.form.get('arrival_time', '').strip()
        if arrival_time_str:
            try:
                arrival_time = datetime.strptime(arrival_time_str, '%Y-%m-%dT%H:%M')
            except Exception:
                try:
                    arrival_time = datetime.strptime(arrival_time_str, '%Y-%m-%d %H:%M')
                except Exception:
                    arrival_time = None

        photo_path = _save_photo(request.files.get('photo'))

        ticket = ConcreteTicket(
            project_id=project_id,
            dept_id=current_user.dept_id,
            ticket_no=ticket_no,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            contract_id=contract_id,
            strength_grade=strength_grade,
            material_id=material_id,
            pour_part=pour_part,
            work_number_id=work_number_id,
            vehicle_count=vehicle_count,
            volume=to_decimal(volume),
            arrival_time=arrival_time,
            vehicle_no=vehicle_no,
            driver_name=driver_name,
            slump=slump,
            temperature=temperature,
            photo_path=photo_path,
            is_reconciled=False,
            is_transferred=False,
            remark=remark,
        )
        db.session.add(ticket)
        db.session.commit()
        flash('商砼小票登记成功。', 'success')
        return redirect(url_for('concrete.index'))

    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    work_numbers = WorkNumber.query.filter_by(project_id=project_id).order_by(WorkNumber.code).all()
    default_ticket_no = _gen_ticket_no(project_id)
    return render_template('concrete/create.html', suppliers=suppliers,
                           work_numbers=work_numbers, strength_grades=STRENGTH_GRADES,
                           now=datetime.now().strftime('%Y-%m-%dT%H:%M'),
                           default_ticket_no=default_ticket_no)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@log_audit(module='concrete', operation='编辑')
def edit(id):
    """编辑商砼小票"""
    ticket = ConcreteTicket.query.get_or_404(id)

    if request.method == 'POST':
        ticket_no = request.form.get('ticket_no', '').strip()
        if not ticket_no:
            flash('小票号不能为空。', 'danger')
            return redirect(url_for('concrete.edit', id=id))

        ticket.ticket_no = ticket_no
        ticket.supplier_id = request.form.get('supplier_id', type=int) or None
        ticket.supplier_name = request.form.get('supplier_name', '').strip() or None
        ticket.contract_id = request.form.get('contract_id', type=int) or None
        ticket.strength_grade = request.form.get('strength_grade', '').strip() or None
        ticket.material_id = request.form.get('material_id', type=int) or None
        ticket.pour_part = request.form.get('pour_part', '').strip() or None
        ticket.work_number_id = request.form.get('work_number_id', type=int) or None
        ticket.vehicle_count = request.form.get('vehicle_count', type=int) or 1
        ticket.volume = to_decimal(request.form.get('volume', type=float) or 0)
        ticket.slump = request.form.get('slump', '').strip() or None
        ticket.temperature = request.form.get('temperature', '').strip() or None
        ticket.remark = request.form.get('remark', '').strip() or None

        arrival_time_str = request.form.get('arrival_time', '').strip()
        if arrival_time_str:
            try:
                ticket.arrival_time = datetime.strptime(arrival_time_str, '%Y-%m-%dT%H:%M')
            except Exception:
                try:
                    ticket.arrival_time = datetime.strptime(arrival_time_str, '%Y-%m-%d %H:%M')
                except Exception:
                    pass
        else:
            ticket.arrival_time = None

        photo = request.files.get('photo')
        if photo and photo.filename:
            ticket.photo_path = _save_photo(photo)

        db.session.commit()
        flash('商砼小票更新成功。', 'success')
        return redirect(url_for('concrete.index'))

    suppliers = Supplier.query.filter_by(project_id=ticket.project_id).order_by(Supplier.name).all()
    work_numbers = WorkNumber.query.filter_by(project_id=ticket.project_id).order_by(WorkNumber.code).all()
    arrival_time_value = ticket.arrival_time.strftime('%Y-%m-%dT%H:%M') if ticket.arrival_time else ''
    return render_template('concrete/edit.html', ticket=ticket, suppliers=suppliers,
                           work_numbers=work_numbers, strength_grades=STRENGTH_GRADES,
                           arrival_time_value=arrival_time_value)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@log_audit(module='concrete', operation='删除')
def delete(id):
    """删除商砼小票（仅未对账可删除）"""
    ticket = ConcreteTicket.query.get_or_404(id)
    if ticket.is_reconciled:
        flash('已对账的小票不可删除。', 'danger')
        return redirect(url_for('concrete.index'))
    if ticket.is_transferred:
        flash('已转入库单的小票不可删除。', 'danger')
        return redirect(url_for('concrete.index'))

    db.session.delete(ticket)
    db.session.commit()
    flash('商砼小票已删除。', 'success')
    return redirect(url_for('concrete.index'))


@bp.route('/reconcile')
@login_required
def reconcile():
    """月末汇总对账"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    month = request.args.get('month', '', type=str).strip()
    if not month:
        month = date.today().strftime('%Y-%m')
    try:
        year, mon = month.split('-')
        year, mon = int(year), int(mon)
    except Exception:
        month = date.today().strftime('%Y-%m')
        year, mon = month.split('-')
        year, mon = int(year), int(mon)

    start_dt = datetime(year, mon, 1)
    if mon == 12:
        end_dt = datetime(year + 1, 1, 1)
    else:
        end_dt = datetime(year, mon + 1, 1)

    tickets = ConcreteTicket.query.filter(
        ConcreteTicket.project_id == project_id,
        ConcreteTicket.arrival_time >= start_dt,
        ConcreteTicket.arrival_time < end_dt,
    ).all()

    # 按供应商汇总
    by_supplier = {}
    for t in tickets:
        key = t.supplier_name or (t.supplier.name if t.supplier else '未指定')
        row = by_supplier.setdefault(key, {'name': key, 'vehicle_count': 0, 'volume': 0.0})
        row['vehicle_count'] += int(t.vehicle_count or 0)
        row['volume'] += float(t.volume or 0)
    supplier_rows = sorted(by_supplier.values(), key=lambda r: r['name'])

    # 按强度等级汇总
    by_grade = {}
    for t in tickets:
        key = t.strength_grade or '未指定'
        row = by_grade.setdefault(key, {'grade': key, 'vehicle_count': 0, 'volume': 0.0})
        row['vehicle_count'] += int(t.vehicle_count or 0)
        row['volume'] += float(t.volume or 0)
    grade_rows = sorted(by_grade.values(), key=lambda r: r['grade'])

    total_vehicle = sum(r['vehicle_count'] for r in supplier_rows)
    total_volume = sum(r['volume'] for r in supplier_rows)

    return render_template('concrete/reconcile.html', month=month,
                           supplier_rows=supplier_rows, grade_rows=grade_rows,
                           total_vehicle=total_vehicle, total_volume=total_volume,
                           ticket_count=len(tickets))


@bp.route('/<int:id>/transfer', methods=['POST'])
@login_required
@log_audit(module='concrete', operation='转入库')
def transfer(id):
    """转入库单"""
    ticket = ConcreteTicket.query.get_or_404(id)
    if ticket.is_transferred:
        flash('该小票已转入库单，请勿重复操作。', 'warning')
        return redirect(url_for('concrete.index'))

    # 查找或创建商砼物资：优先使用小票已关联的 material_id，否则按强度等级匹配
    grade = ticket.strength_grade or ''
    target = None
    if ticket.material_id:
        target = Material.query.get(ticket.material_id)
    if target is None:
        material = Material.query.filter(
            Material.project_id == ticket.project_id,
            or_(Material.name.like('%商砼%'), Material.name.like('%混凝土%')),
        ).all()
        for m in material:
            if grade and grade in (m.name or ''):
                target = m
                break
            if grade and grade in (m.specification or ''):
                target = m
                break
        if target is None and material:
            target = material[0]

    if target is None:
        # 创建新物资
        mat_name = f'商砼{grade}' if grade else '商砼'
        target = Material(
            project_id=ticket.project_id,
            name=mat_name,
            specification=grade or None,
            unit='m³',
            remark='商砼小票自动创建',
        )
        db.session.add(target)
        db.session.flush()

    # 创建入库单
    stock_in = StockIn(
        project_id=ticket.project_id,
        dept_id=ticket.dept_id or current_user.dept_id,
        code=gen_stock_in_code(ticket.project_id),
        stock_in_date=ticket.arrival_time.date() if ticket.arrival_time else date.today(),
        stock_in_type='商砼入库',
        supplier_id=ticket.supplier_id,
        contract_id=ticket.contract_id,
        operator=current_user.name or current_user.username,
        remark=f'商砼小票转入库：{ticket.ticket_no}',
        total_quantity=to_decimal(ticket.volume or 0),
        total_amount=to_decimal(0),
        estimated_amount=to_decimal(0),
        actual_amount=to_decimal(0),
        is_reconciled=False,
        is_initial=False,
        approval_status='passed',
    )
    db.session.add(stock_in)
    db.session.flush()

    item = StockInItem(
        stock_in_id=stock_in.id,
        material_id=target.id,
        quantity=to_decimal(ticket.volume or 0),
        unit_price=to_decimal(0),
        amount=to_decimal(0),
        price_status='unpriced',
    )
    db.session.add(item)

    # 更新库存
    inv = Inventory.query.filter_by(
        project_id=ticket.project_id, material_id=target.id).first()
    if not inv:
        inv = Inventory(
            project_id=ticket.project_id,
            material_id=target.id,
            quantity=to_decimal(0),
            estimated_amount=to_decimal(0),
            actual_amount=to_decimal(0),
        )
        db.session.add(inv)
        db.session.flush()
    inv.quantity = to_decimal(inv.quantity) + to_decimal(ticket.volume or 0)

    ticket.is_transferred = True
    db.session.commit()
    flash(f'已转入库单：{stock_in.code}', 'success')
    return redirect(url_for('concrete.index'))


@bp.route('/stats')
@login_required
def stats():
    """浇筑部位统计"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    month = request.args.get('month', '', type=str).strip()
    if not month:
        month = date.today().strftime('%Y-%m')
    try:
        year, mon = month.split('-')
        year, mon = int(year), int(mon)
    except Exception:
        month = date.today().strftime('%Y-%m')
        year, mon = month.split('-')
        year, mon = int(year), int(mon)

    start_dt = datetime(year, mon, 1)
    if mon == 12:
        end_dt = datetime(year + 1, 1, 1)
    else:
        end_dt = datetime(year, mon + 1, 1)

    tickets = ConcreteTicket.query.filter(
        ConcreteTicket.project_id == project_id,
        ConcreteTicket.arrival_time >= start_dt,
        ConcreteTicket.arrival_time < end_dt,
    ).all()

    # 按浇筑部位汇总
    by_part = {}
    for t in tickets:
        key = t.pour_part or '未指定'
        row = by_part.setdefault(key, {'pour_part': key, 'vehicle_count': 0, 'volume': 0.0})
        row['vehicle_count'] += int(t.vehicle_count or 0)
        row['volume'] += float(t.volume or 0)
    part_rows = sorted(by_part.values(), key=lambda r: r['pour_part'])

    # 按工号汇总
    work_number_map = {wn.id: wn for wn in WorkNumber.query.filter_by(project_id=project_id).all()}
    by_work = {}
    for t in tickets:
        wn = work_number_map.get(t.work_number_id) if t.work_number_id else None
        key = wn.code if wn else '未指定'
        label = f'{wn.code}（{wn.division_name}）' if wn and wn.division_name else key
        row = by_work.setdefault(key, {'work_code': key, 'label': label, 'vehicle_count': 0, 'volume': 0.0})
        row['vehicle_count'] += int(t.vehicle_count or 0)
        row['volume'] += float(t.volume or 0)
    work_rows = sorted(by_work.values(), key=lambda r: r['work_code'])

    total_vehicle = sum(r['vehicle_count'] for r in part_rows)
    total_volume = sum(r['volume'] for r in part_rows)

    return render_template('concrete/stats.html', month=month,
                           part_rows=part_rows, work_rows=work_rows,
                           total_vehicle=total_vehicle, total_volume=total_volume,
                           ticket_count=len(tickets))
