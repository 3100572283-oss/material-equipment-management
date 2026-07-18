from flask import (render_template, request, redirect, url_for, flash,
                   jsonify, session)
from flask_login import login_required, current_user
from datetime import datetime, date

from app.mobile import bp
from app import db
from app.models import (Material, Category, UsageUnit, StockIn, StockInItem,
                        StockOut, StockOutItem, Project, Inventory)
from app.utils import to_decimal


def _gen_stock_in_code(project_id):
    """生成入库单号: RK-{项目ID}-{年月日}-{3位序号}"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"RK-{project_id}-{today}-"
    existing = StockIn.query.filter(StockIn.code.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


def _gen_stock_out_code(project_id):
    """生成出库单号: CK-{项目ID}-{年月日}-{3位序号}"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"CK-{project_id}-{today}-"
    existing = StockOut.query.filter(StockOut.code.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


def _require_project():
    """检查是否已选择项目，返回 project_id；未选择则重定向到首页。"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return None, redirect(url_for('main.index'))
    return project_id, None


@bp.route('/')
@login_required
def index():
    """移动端首页"""
    project_id, redirect_resp = _require_project()
    if redirect_resp:
        return redirect_resp

    project = Project.query.get(project_id)
    return render_template('mobile/index.html', project=project)


@bp.route('/stock_in')
@login_required
def stock_in():
    """离线入库录入页面"""
    project_id, redirect_resp = _require_project()
    if redirect_resp:
        return redirect_resp

    return render_template('mobile/stock_in.html')


@bp.route('/stock_out')
@login_required
def stock_out():
    """离线出库录入页面"""
    project_id, redirect_resp = _require_project()
    if redirect_resp:
        return redirect_resp

    return render_template('mobile/stock_out.html')


@bp.route('/offline_data')
@login_required
def offline_data():
    """查看本地离线数据页面"""
    project_id, redirect_resp = _require_project()
    if redirect_resp:
        return redirect_resp

    return render_template('mobile/offline_data.html')


@bp.route('/api/material_list')
@login_required
def api_material_list():
    """获取当前项目的物资列表JSON（供离线缓存）"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])

    materials = Material.query.filter_by(project_id=project_id) \
        .order_by(Material.code.asc(), Material.name.asc()).all()
    return jsonify([{
        'id': m.id,
        'name': m.name,
        'code': m.code or '',
        'spec': m.specification or '',
        'unit': m.unit or '',
        'category_id': m.category_id
    } for m in materials])


@bp.route('/api/category_list')
@login_required
def api_category_list():
    """获取当前项目的分类列表JSON"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])

    categories = Category.query.filter_by(project_id=project_id) \
        .order_by(Category.sort_order.asc(), Category.name.asc()).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'parent_id': c.parent_id or 0,
        'category_code': c.category_code or ''
    } for c in categories])


@bp.route('/api/usage_units')
@login_required
def api_usage_units():
    """获取当前项目的用料单位列表JSON"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])

    units = UsageUnit.query.filter_by(project_id=project_id) \
        .order_by(UsageUnit.name.asc()).all()
    return jsonify([{
        'id': u.id,
        'name': u.name,
        'code': u.code or '',
        'manager': u.manager or ''
    } for u in units])


@bp.route('/api/sync', methods=['POST'])
@login_required
def api_sync():
    """同步离线数据到服务器

    接收JSON: {records: [{type: 'stock_in'|'stock_out', data: {...}}, ...]}
    返回: {success: True, synced: N, failed: M, errors: [...]}
    """
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': '请先选择项目。'}), 400

    payload = request.get_json(silent=True) or {}
    records = payload.get('records') or []

    if not records:
        return jsonify({'success': True, 'synced': 0, 'failed': 0, 'errors': [],
                        'message': '没有需要同步的记录。'})

    synced = 0
    failed = 0
    errors = []

    for idx, rec in enumerate(records):
        rec_type = rec.get('type')
        data = rec.get('data') or {}
        # 使用 savepoint 逐条处理，单条失败仅回滚该条，不影响其他记录
        try:
            if rec_type == 'stock_in':
                with db.session.begin_nested():
                    _sync_stock_in(project_id, data)
                synced += 1
            elif rec_type == 'stock_out':
                with db.session.begin_nested():
                    _sync_stock_out(project_id, data)
                synced += 1
            else:
                failed += 1
                errors.append(f'第{idx + 1}条：未知记录类型 {rec_type}')
        except Exception as e:
            failed += 1
            errors.append(f'第{idx + 1}条：{e}')

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'synced': 0, 'failed': len(records),
                        'errors': [f'提交数据库失败：{e}']}), 500

    return jsonify({
        'success': True,
        'synced': synced,
        'failed': failed,
        'errors': errors,
        'message': f'同步完成：成功 {synced} 条，失败 {failed} 条。'
    })


def _sync_stock_in(project_id, data):
    """同步一条入库记录：创建 StockIn + StockInItem，增加库存"""
    material_id = int(data.get('material_id') or 0)
    if material_id <= 0:
        raise ValueError('物资ID无效')

    material = Material.query.filter_by(id=material_id, project_id=project_id).first()
    if not material:
        raise ValueError(f'物资不存在(id={material_id})')

    quantity = to_decimal(data.get('quantity') or 0)
    if quantity <= 0:
        raise ValueError('数量必须大于0')

    unit_price = to_decimal(data.get('unit_price') or 0)
    amount = float(quantity) * float(unit_price)
    batch_no = (data.get('batch_no') or '').strip() or None
    remark = (data.get('remark') or '').strip()
    saved_at = data.get('saved_at') or ''

    # 解析保存时间作为入库日期
    stock_in_date = date.today()
    if saved_at:
        try:
            stock_in_date = datetime.fromisoformat(saved_at).date()
        except Exception:
            pass

    price_status = 'confirmed' if float(unit_price) > 0 else 'unpriced'

    stock_in = StockIn(
        project_id=project_id,
        code=_gen_stock_in_code(project_id),
        stock_in_date=stock_in_date,
        stock_in_type='采购入库',
        operator=current_user.name or current_user.username,
        remark=f'[移动端离线] {remark}' if remark else '[移动端离线]',
        total_quantity=quantity,
        total_amount=amount,
        estimated_amount=0,
        actual_amount=amount if price_status == 'confirmed' else 0,
        approval_status='passed',
        quality_status='passed',
    )
    db.session.add(stock_in)
    db.session.flush()

    item = StockInItem(
        stock_in_id=stock_in.id,
        material_id=material_id,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
        price_status=price_status,
        batch_no=batch_no,
    )
    db.session.add(item)

    # 更新库存：增加
    inv = Inventory.query.filter_by(
        project_id=project_id, material_id=material_id).first()
    if not inv:
        inv = Inventory(project_id=project_id, material_id=material_id,
                        quantity=0, estimated_amount=0, actual_amount=0)
        db.session.add(inv)
        db.session.flush()
    inv.quantity = to_decimal(inv.quantity) + quantity
    if price_status == 'confirmed':
        inv.actual_amount = to_decimal(inv.actual_amount or 0) + to_decimal(amount)


def _sync_stock_out(project_id, data):
    """同步一条出库记录：创建 StockOut + StockOutItem，扣减库存（不足则抛错）"""
    material_id = int(data.get('material_id') or 0)
    if material_id <= 0:
        raise ValueError('物资ID无效')

    material = Material.query.filter_by(id=material_id, project_id=project_id).first()
    if not material:
        raise ValueError(f'物资不存在(id={material_id})')

    quantity = to_decimal(data.get('quantity') or 0)
    if quantity <= 0:
        raise ValueError('数量必须大于0')

    usage_unit_id = data.get('usage_unit_id')
    usage_unit_id = int(usage_unit_id) if usage_unit_id else None
    remark = (data.get('remark') or '').strip()
    saved_at = data.get('saved_at') or ''

    # 解析保存时间作为出库日期
    stock_out_date = date.today()
    if saved_at:
        try:
            stock_out_date = datetime.fromisoformat(saved_at).date()
        except Exception:
            pass

    # 检查库存
    inv = Inventory.query.filter_by(
        project_id=project_id, material_id=material_id).first()
    current_stock = float(inv.quantity) if inv else 0
    if float(quantity) > current_stock:
        raise ValueError(
            f'{material.name}库存不足（当前 {current_stock}，出库 {float(quantity)}）')

    stock_out = StockOut(
        project_id=project_id,
        code=_gen_stock_out_code(project_id),
        stock_out_date=stock_out_date,
        stock_out_type='工程领用',
        usage_unit_id=usage_unit_id,
        operator=current_user.name or current_user.username,
        remark=f'[移动端离线] {remark}' if remark else '[移动端离线]',
        total_quantity=quantity,
        total_amount=0,
        approval_status='passed',
    )
    db.session.add(stock_out)
    db.session.flush()

    # 取库存均价作为出库单价
    unit_price = to_decimal(0)
    if inv and float(inv.quantity) > 0 and float(inv.actual_amount or 0) > 0:
        unit_price = to_decimal(
            float(inv.actual_amount) / float(inv.quantity))
    amount = float(quantity) * float(unit_price)

    item = StockOutItem(
        stock_out_id=stock_out.id,
        material_id=material_id,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
    )
    db.session.add(item)

    stock_out.total_amount = amount

    # 扣减库存
    if inv:
        inv.quantity = to_decimal(inv.quantity) - quantity
        if float(unit_price) > 0:
            inv.actual_amount = to_decimal(inv.actual_amount or 0) - to_decimal(amount)
