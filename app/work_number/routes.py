from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.work_number import bp
from app import db
from app.models import WorkNumber, MaterialQuota, Material, StockOut, StockOutItem
from app.decorators import editor_required, log_audit
from app.utils import to_decimal, ConfigCache, apply_data_scope
from sqlalchemy import func


def _gen_division_code(project_id):
    """生成分部工程编码: GH{4位流水号}"""
    max_code = db.session.query(func.max(WorkNumber.code)).filter(
        WorkNumber.project_id == project_id,
        WorkNumber.code.like('GH%'),
        WorkNumber.parent_id.is_(None)
    ).scalar()
    if max_code:
        try:
            seq = int(max_code[2:]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f'GH{seq:04d}'


def _gen_item_code(division_code):
    """生成分项工程编码: 分部编码-序号"""
    existing = WorkNumber.query.filter(
        WorkNumber.code.like(f'{division_code}-%')
    ).all()
    max_seq = 0
    for item in existing:
        try:
            seq = int(item.code.split('-')[1])
            if seq > max_seq:
                max_seq = seq
        except (IndexError, ValueError):
            continue
    return f'{division_code}-{max_seq + 1:02d}'


def _get_used_quantity(project_id, work_number_id, material_id):
    """获取工号下某物资的已领数量"""
    used = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
        StockOut, StockOutItem.stock_out_id == StockOut.id
    ).filter(
        StockOut.project_id == project_id,
        StockOut.work_number_id == work_number_id,
        StockOutItem.material_id == material_id,
        StockOut.approval_status == 'passed'
    ).scalar()
    return float(used or 0)


def _get_used_amount(project_id, work_number_id, material_id):
    """获取工号下某物资的已领金额"""
    used = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
        StockOut, StockOutItem.stock_out_id == StockOut.id
    ).filter(
        StockOut.project_id == project_id,
        StockOut.work_number_id == work_number_id,
        StockOutItem.material_id == material_id,
        StockOut.approval_status == 'passed'
    ).scalar()
    return float(used or 0)


@bp.route('/')
@login_required
def index():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    keyword = request.args.get('keyword', '', type=str)
    query = WorkNumber.query.filter(WorkNumber.parent_id.is_(None))
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, WorkNumber)
    if keyword:
        query = query.filter(WorkNumber.code.contains(keyword) | WorkNumber.division_name.contains(keyword))
    divisions = query.order_by(WorkNumber.code).all()

    return render_template('work_number/index.html', divisions=divisions, keyword=keyword)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='work_number', operation='新增分部')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        work_number = WorkNumber(
            project_id=project_id,
            parent_id=None,
            code=_gen_division_code(project_id),
            division_name=request.form.get('division_name', '').strip(),
            team_name=request.form.get('team_name', '').strip(),
            picker=request.form.get('picker', '').strip(),
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(work_number)
        db.session.commit()
        flash('分部工程创建成功。', 'success')
        return redirect(url_for('work_number.index'))
    default_code = _gen_division_code(project_id)
    return render_template('work_number/form.html', work_number=None, default_code=default_code, is_division=True)


@bp.route('/<int:division_id>/add_item', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='work_number', operation='新增分项')
def add_item(division_id):
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    division = WorkNumber.query.get_or_404(division_id)
    if division.parent_id is not None:
        flash('只能在分部工程下添加分项工程。', 'warning')
        return redirect(url_for('work_number.index'))

    if request.method == 'POST':
        work_number = WorkNumber(
            project_id=project_id,
            parent_id=division_id,
            code=_gen_item_code(division.code),
            division_name=division.division_name,
            item_name=request.form.get('item_name', '').strip(),
            team_name=request.form.get('team_name', '').strip(),
            picker=request.form.get('picker', '').strip(),
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(work_number)
        db.session.commit()
        flash('分项工程创建成功。', 'success')
        return redirect(url_for('work_number.index'))
    default_code = _gen_item_code(division.code)
    return render_template('work_number/form.html', work_number=None, default_code=default_code, 
                           is_division=False, division=division)


@bp.route('/<int:id>')
@login_required
def detail(id):
    from flask import session
    project_id = session.get('current_project_id')
    work_number = WorkNumber.query.get_or_404(id)

    quotas = MaterialQuota.query.filter_by(
        work_number_id=id, project_id=project_id
    ).order_by(MaterialQuota.id.desc()).all()

    quota_list = []
    for q in quotas:
        used_qty = _get_used_quantity(project_id, id, q.material_id)
        used_amt = _get_used_amount(project_id, id, q.material_id)
        qty_ratio = 0
        amt_ratio = 0
        if q.quota_quantity and float(q.quota_quantity) > 0:
            qty_ratio = round(used_qty / float(q.quota_quantity) * 100, 2)
        if q.quota_amount and float(q.quota_amount) > 0:
            amt_ratio = round(used_amt / float(q.quota_amount) * 100, 2)
        quota_list.append({
            'id': q.id,
            'material_id': q.material_id,
            'material_code': q.material.code if q.material else '',
            'material_name': q.material.name if q.material else '-',
            'specification': q.material.specification if q.material else '',
            'unit': q.material.unit if q.material else '',
            'quota_type': q.quota_type,
            'quota_quantity': float(q.quota_quantity or 0),
            'quota_amount': float(q.quota_amount or 0),
            'used_quantity': round(used_qty, 4),
            'used_amount': round(used_amt, 2),
            'remaining_quantity': round(float(q.quota_quantity or 0) - used_qty, 4),
            'remaining_amount': round(float(q.quota_amount or 0) - used_amt, 2),
            'qty_ratio': qty_ratio,
            'amt_ratio': amt_ratio,
        })

    materials = Material.query.filter_by(project_id=project_id).order_by(Material.name).all()
    enable_quota = ConfigCache.get('enable_quota_control') == 'true'

    return render_template('work_number/detail.html', work_number=work_number,
                           quota_list=quota_list, materials=materials,
                           enable_quota=enable_quota)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='work_number', operation='编辑')
def edit(id):
    work_number = WorkNumber.query.get_or_404(id)
    if request.method == 'POST':
        if work_number.is_division:
            work_number.division_name = request.form.get('division_name', '').strip()
            work_number.team_name = request.form.get('team_name', '').strip()
            work_number.picker = request.form.get('picker', '').strip()
            work_number.remark = request.form.get('remark', '').strip()
        else:
            work_number.item_name = request.form.get('item_name', '').strip()
            work_number.team_name = request.form.get('team_name', '').strip()
            work_number.picker = request.form.get('picker', '').strip()
            work_number.remark = request.form.get('remark', '').strip()
        db.session.commit()
        flash('工号更新成功。', 'success')
        return redirect(url_for('work_number.index'))
    return render_template('work_number/form.html', work_number=work_number, 
                           is_division=work_number.is_division)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='work_number', operation='删除')
def delete(id):
    work_number = WorkNumber.query.get_or_404(id)
    db.session.delete(work_number)
    db.session.commit()
    flash('工号删除成功。', 'success')
    return redirect(url_for('work_number.index'))


# ---------------- 材料限额 ----------------
@bp.route('/<int:id>/quota/add', methods=['POST'])
@login_required
@editor_required
@log_audit(module='work_number', operation='添加限额')
def add_quota(id):
    from flask import session
    project_id = session.get('current_project_id')
    work_number = WorkNumber.query.get_or_404(id)

    material_id = request.form.get('material_id', type=int)
    quota_type = request.form.get('quota_type', 'quantity')
    quota_quantity = request.form.get('quota_quantity', type=float) or 0
    quota_amount = request.form.get('quota_amount', type=float) or 0

    existing = MaterialQuota.query.filter_by(
        work_number_id=id, material_id=material_id
    ).first()
    if existing:
        flash('该物资已设置限额，请编辑修改。', 'warning')
        return redirect(url_for('work_number.detail', id=id))

    quota = MaterialQuota(
        project_id=project_id,
        work_number_id=id,
        material_id=material_id,
        quota_type=quota_type,
        quota_quantity=quota_quantity,
        quota_amount=quota_amount
    )
    db.session.add(quota)
    db.session.commit()
    flash('限额添加成功。', 'success')
    return redirect(url_for('work_number.detail', id=id))


@bp.route('/quota/<int:qid>/edit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='work_number', operation='编辑限额')
def edit_quota(qid):
    quota = MaterialQuota.query.get_or_404(qid)
    quota.quota_type = request.form.get('quota_type', 'quantity')
    quota.quota_quantity = request.form.get('quota_quantity', type=float) or 0
    quota.quota_amount = request.form.get('quota_amount', type=float) or 0
    db.session.commit()
    flash('限额更新成功。', 'success')
    return redirect(url_for('work_number.detail', id=quota.work_number_id))


@bp.route('/quota/<int:qid>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='work_number', operation='删除限额')
def delete_quota(qid):
    quota = MaterialQuota.query.get_or_404(qid)
    work_number_id = quota.work_number_id
    db.session.delete(quota)
    db.session.commit()
    flash('限额已删除。', 'success')
    return redirect(url_for('work_number.detail', id=work_number_id))


@bp.route('/api/quota_check')
@login_required
def api_quota_check():
    """API：检查工号物资限额情况"""
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'error': '未选择项目'}), 400

    work_number_id = request.args.get('work_number_id', type=int)
    material_id = request.args.get('material_id', type=int)
    quantity = request.args.get('quantity', type=float) or 0
    amount = request.args.get('amount', type=float) or 0

    if not work_number_id or not material_id:
        return jsonify({'error': '参数不全'}), 400

    enable_quota = ConfigCache.get('enable_quota_control') == 'true'
    if not enable_quota:
        return jsonify({'enabled': False})

    quota = MaterialQuota.query.filter_by(
        work_number_id=work_number_id, material_id=material_id
    ).first()

    if not quota:
        return jsonify({'enabled': True, 'has_quota': False})

    used_qty = _get_used_quantity(project_id, work_number_id, material_id)
    used_amt = _get_used_amount(project_id, work_number_id, material_id)

    warning_ratio = float(ConfigCache.get('quota_warning_ratio', '80'))
    force_block = ConfigCache.get('quota_force_block') == 'true'

    result = {
        'enabled': True,
        'has_quota': True,
        'quota_type': quota.quota_type,
        'quota_quantity': float(quota.quota_quantity or 0),
        'quota_amount': float(quota.quota_amount or 0),
        'used_quantity': round(used_qty, 4),
        'used_amount': round(used_amt, 2),
        'warning_ratio': warning_ratio,
        'force_block': force_block,
        'status': 'normal',  # normal / warning / over
        'message': '',
    }

    if quantity > 0 and quota.quota_type in ('quantity', 'both'):
        total_after = used_qty + quantity
        quota_qty = float(quota.quota_quantity or 0)
        if quota_qty > 0:
            ratio = total_after / quota_qty * 100
            result['total_after_qty'] = round(total_after, 4)
            result['qty_ratio_after'] = round(ratio, 2)
            if ratio >= 100:
                result['status'] = 'over'
                result['message'] = f'超数量限额：限额{quota_qty}，已领{round(used_qty,4)}，本次{quantity}，超出{round(total_after - quota_qty, 4)}'
            elif ratio >= warning_ratio:
                result['status'] = 'warning'
                result['message'] = f'数量限额已使用{round(ratio,1)}%，限额{quota_qty}，剩余{round(quota_qty - used_qty, 4)}'

    if amount > 0 and quota.quota_type in ('amount', 'both'):
        total_after = used_amt + amount
        quota_amt = float(quota.quota_amount or 0)
        if quota_amt > 0:
            ratio = total_after / quota_amt * 100
            result['total_after_amount'] = round(total_after, 2)
            result['amt_ratio_after'] = round(ratio, 2)
            if ratio >= 100:
                if result['status'] != 'over':
                    result['status'] = 'over'
                result['message'] += ('' if not result['message'] else '；') + f'超金额限额：限额{quota_amt}元，已领{round(used_amt,2)}元'
            elif ratio >= warning_ratio:
                if result['status'] == 'normal':
                    result['status'] = 'warning'
                result['message'] += ('' if not result['message'] else '；') + f'金额限额已使用{round(ratio,1)}%'

    return jsonify(result)
