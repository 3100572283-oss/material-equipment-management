import os
import uuid
from datetime import datetime, date, timedelta
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from app.turnover_material import bp
from app import db
from app.models import TurnoverMaterial, TurnoverInventory, TurnoverRecord, Project
from app.decorators import editor_required, log_audit
from app.utils import log_operation, apply_data_scope


def _get_project_id():
    pid = session.get('current_project_id')
    if not pid:
        flash('请先选择项目', 'warning')
    return pid


# ========== 周转材台账 ==========

@bp.route('/')
@login_required
def index():
    project_id = session.get('current_project_id')
    # 全部数据权限用户在"全部项目"模式下不限制项目
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目', 'warning')
            return redirect(url_for('main.index'))
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    material_type = request.args.get('material_type', '', type=str)
    query = TurnoverMaterial.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, TurnoverMaterial)
    if keyword:
        query = query.filter(TurnoverMaterial.name.contains(keyword) | TurnoverMaterial.code.contains(keyword))
    if material_type:
        query = query.filter_by(material_type=material_type)
    pagination = query.order_by(TurnoverMaterial.created_at.desc()).paginate(page=page, per_page=10, error_out=False)
    return render_template('turnover_material/index.html', pagination=pagination, keyword=keyword, material_type=material_type)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='turnover_material', operation='新增周转材')
def create():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        tm = TurnoverMaterial(
            project_id=project_id,
            code=request.form.get('code', ''),
            name=request.form.get('name', ''),
            category=request.form.get('category', ''),
            specification=request.form.get('specification', ''),
            unit=request.form.get('unit', ''),
            material_type=request.form.get('material_type', 'own'),
            rental_price=request.form.get('rental_price', 0) or 0,
            rental_unit=request.form.get('rental_unit', 'day'),
            original_value=request.form.get('original_value', 0) or 0,
            amortize_months=request.form.get('amortize_months', 0) or 0,
            remark=request.form.get('remark', '')
        )
        db.session.add(tm)
        db.session.commit()
        inv = TurnoverInventory(project_id=project_id, material_id=tm.id, quantity=0, rent_out_qty=0)
        db.session.add(inv)
        db.session.commit()
        log_operation('新增', module='周转材台账', description=f'新增周转材 {tm.name}')
        flash('周转材添加成功', 'success')
        return redirect(url_for('turnover_material.index'))
    count = TurnoverMaterial.query.filter_by(project_id=project_id).count()
    default_code = f'ZZ-{project_id}-{count+1:04d}'
    return render_template('turnover_material/form.html', material=None, default_code=default_code)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='turnover_material', operation='编辑周转材')
def edit(id):
    tm = TurnoverMaterial.query.get_or_404(id)
    if request.method == 'POST':
        tm.name = request.form.get('name', '')
        tm.category = request.form.get('category', '')
        tm.specification = request.form.get('specification', '')
        tm.unit = request.form.get('unit', '')
        tm.material_type = request.form.get('material_type', 'own')
        tm.rental_price = request.form.get('rental_price', 0) or 0
        tm.rental_unit = request.form.get('rental_unit', 'day')
        tm.original_value = request.form.get('original_value', 0) or 0
        tm.amortize_months = request.form.get('amortize_months', 0) or 0
        tm.remark = request.form.get('remark', '')
        db.session.commit()
        log_operation('编辑', module='周转材台账', description=f'编辑周转材 {tm.name}')
        flash('周转材更新成功', 'success')
        return redirect(url_for('turnover_material.index'))
    return render_template('turnover_material/form.html', material=tm)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='turnover_material', operation='删除周转材')
def delete(id):
    tm = TurnoverMaterial.query.get_or_404(id)
    db.session.delete(tm)
    db.session.commit()
    log_operation('删除', module='周转材台账', description=f'删除周转材 {tm.name}')
    flash('周转材删除成功', 'success')
    return redirect(url_for('turnover_material.index'))


# ========== 库存管理 ==========

@bp.route('/inventory')
@login_required
def inventory():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    invs = TurnoverInventory.query.filter_by(project_id=project_id).all()
    return render_template('turnover_material/inventory.html', inventories=invs)


# ========== 领用归还 ==========

@bp.route('/record')
@login_required
def record_index():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', '', type=str)
    team = request.args.get('team', '', type=str)
    query = TurnoverRecord.query.filter_by(project_id=project_id)
    if status:
        query = query.filter_by(status=status)
    if team:
        query = query.filter(TurnoverRecord.team.contains(team))
    pagination = query.order_by(TurnoverRecord.created_at.desc()).paginate(page=page, per_page=10, error_out=False)
    materials = TurnoverMaterial.query.filter_by(project_id=project_id).all()
    return render_template('turnover_material/record.html', pagination=pagination, status=status, team=team, materials=materials)


@bp.route('/record/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='turnover_material', operation='领用')
def create_record():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        material_id = int(request.form.get('material_id', 0))
        qty = float(request.form.get('qty', 0))
        inv = TurnoverInventory.query.filter_by(project_id=project_id, material_id=material_id).first()
        if not inv or float(inv.quantity or 0) < qty:
            flash('库存不足', 'danger')
            return redirect(url_for('turnover_material.create_record'))
        rec = TurnoverRecord(
            project_id=project_id,
            material_id=material_id,
            team=request.form.get('team', ''),
            qty=qty,
            out_date=datetime.strptime(request.form.get('out_date', date.today().isoformat()), '%Y-%m-%d').date(),
            expect_return_date=datetime.strptime(request.form.get('expect_return_date', ''), '%Y-%m-%d').date() if request.form.get('expect_return_date') else None,
            remark=request.form.get('remark', '')
        )
        inv.quantity = float(inv.quantity or 0) - qty
        if inv.material and inv.material.material_type == 'rental':
            inv.rent_out_qty = float(inv.rent_out_qty or 0) + qty
        db.session.add(rec)
        db.session.commit()
        log_operation('新增', module='周转材领用', description=f'领用 {rec.team} {rec.qty}')
        flash('领用单创建成功', 'success')
        return redirect(url_for('turnover_material.record_index'))
    materials = TurnoverMaterial.query.filter_by(project_id=project_id).all()
    return render_template('turnover_material/record_form.html', record=None, materials=materials)


@bp.route('/record/<int:id>/return', methods=['POST'])
@login_required
@editor_required
@log_audit(module='turnover_material', operation='归还')
def return_record(id):
    project_id = _get_project_id()
    rec = TurnoverRecord.query.get_or_404(id)
    return_qty = float(request.form.get('return_qty', 0))
    lost_qty = float(request.form.get('lost_qty', 0))
    if return_qty + lost_qty > rec.using_qty:
        flash('归还+损耗数量不能超过在用数量', 'danger')
        return redirect(url_for('turnover_material.record_index'))
    rec.returned_qty = float(rec.returned_qty or 0) + return_qty
    rec.lost_qty = float(rec.lost_qty or 0) + lost_qty
    if rec.returned_qty + rec.lost_qty >= rec.qty:
        rec.status = 'returned'
        rec.actual_return_date = date.today()
    else:
        rec.status = 'partial'
    inv = TurnoverInventory.query.filter_by(project_id=project_id, material_id=rec.material_id).first()
    if inv:
        inv.quantity = float(inv.quantity or 0) + return_qty
        if inv.material and inv.material.material_type == 'rental':
            inv.rent_out_qty = max(0, float(inv.rent_out_qty or 0) - return_qty)
    # 租赁费自动计算
    if rec.material and rec.material.material_type == 'rental':
        using_days = rec.using_days
        price = float(rec.material.rental_price or 0)
        unit_factor = 1 if rec.material.rental_unit == 'day' else 30
        total_returned = float(rec.returned_qty or 0)
        rec.rent_fee = round(using_days / unit_factor * price * total_returned, 2)
    db.session.commit()
    flash('归还处理成功', 'success')
    return redirect(url_for('turnover_material.record_index'))


# ========== 租赁费结算 ==========

@bp.route('/rental_bill')
@login_required
def rental_bill():
    project_id = _get_project_id()
    if not project_id:
        return redirect(url_for('main.index'))
    records = TurnoverRecord.query.filter(
        TurnoverRecord.project_id == project_id,
        TurnoverRecord.material_id.in_(
            db.session.query(TurnoverMaterial.id).filter_by(project_id=project_id, material_type='rental')
        )
    ).order_by(TurnoverRecord.out_date.desc()).all()
    total_fee = sum(float(r.rent_fee or 0) for r in records)
    return render_template('turnover_material/rental_bill.html', records=records, total_fee=total_fee)


# ========== API ==========

@bp.route('/api/inventory/<int:material_id>')
@login_required
def api_inventory(material_id):
    project_id = session.get('current_project_id')
    inv = TurnoverInventory.query.filter_by(project_id=project_id, material_id=material_id).first()
    if inv:
        return jsonify({'quantity': float(inv.quantity or 0), 'rent_out_qty': float(inv.rent_out_qty or 0)})
    return jsonify({'quantity': 0, 'rent_out_qty': 0})
