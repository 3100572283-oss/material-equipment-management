from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from app import db
from app.models import (InventoryBatch, Material, Category, StockInItem,
                        StockOutItem, Inventory, StockIn, StockOut, Project)
from datetime import datetime, timedelta, date
from sqlalchemy import or_

from app.batch import bp


# 临近过期阈值（天）
NEAR_EXPIRE_DAYS = 30


@bp.route('/')
@login_required
def index():
    """批次库存列表"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    keyword = request.args.get('keyword', '', type=str)
    batch_no = request.args.get('batch_no', '', type=str)
    expire_status = request.args.get('expire_status', 'all', type=str)

    query = db.session.query(InventoryBatch, Material).join(
        Material, Material.id == InventoryBatch.material_id
    ).filter(InventoryBatch.project_id == project_id)

    if keyword:
        query = query.filter(or_(
            Material.name.contains(keyword),
            Material.code.contains(keyword)
        ))
    if batch_no:
        query = query.filter(InventoryBatch.batch_no.contains(batch_no))

    today = date.today()
    near_threshold = today + timedelta(days=NEAR_EXPIRE_DAYS)
    if expire_status == 'near':
        query = query.filter(
            InventoryBatch.expire_date.isnot(None),
            InventoryBatch.expire_date > today,
            InventoryBatch.expire_date <= near_threshold
        )
    elif expire_status == 'expired':
        query = query.filter(
            InventoryBatch.expire_date.isnot(None),
            InventoryBatch.expire_date <= today
        )

    query = query.order_by(InventoryBatch.expire_date.asc(),
                           InventoryBatch.stock_in_date.asc())
    batches = query.all()

    return render_template('batch/list.html', batches=batches, keyword=keyword,
                           batch_no=batch_no, expire_status=expire_status,
                           today=today, near_threshold=near_threshold)


@bp.route('/category_config')
@login_required
def category_config():
    """批次管理分类配置"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    categories = Category.query.filter_by(
        project_id=project_id, parent_id=None
    ).order_by(Category.sort_order.asc(), Category.created_at.asc()).all()

    # 关联物资数量
    for cat in categories:
        # 递归获取所有子分类ID
        cat_ids = _get_all_descendant_category_ids(cat.id)
        cat_ids.append(cat.id)
        cat.material_count = Material.query.filter(
            Material.category_id.in_(cat_ids)
        ).count()

    return render_template('batch/category_config.html', categories=categories)


def _get_all_descendant_category_ids(parent_id):
    """递归获取所有后代分类ID"""
    result = []
    children = Category.query.filter_by(parent_id=parent_id).all()
    for child in children:
        result.append(child.id)
        result.extend(_get_all_descendant_category_ids(child.id))
    return result


@bp.route('/category_config/<int:category_id>/toggle', methods=['POST'])
@login_required
def toggle_category_batch(category_id):
    """切换分类批次管理状态"""
    category = Category.query.get_or_404(category_id)
    category.batch_management = not bool(category.batch_management)
    db.session.commit()
    return jsonify({'success': True, 'enabled': category.batch_management})


@bp.route('/api/batches/<int:material_id>')
@login_required
def api_batches(material_id):
    """获取物资的批次列表（先进先出排序）"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])

    material = Material.query.filter_by(
        project_id=project_id, id=material_id
    ).first()
    if not material:
        return jsonify([])

    batches = InventoryBatch.query.filter_by(
        project_id=project_id, material_id=material_id
    ).order_by(InventoryBatch.stock_in_date.asc(),
               InventoryBatch.expire_date.asc()).all()

    return jsonify([{
        'id': b.id,
        'batch_no': b.batch_no,
        'quantity': float(b.quantity or 0),
        'unit_price': float(b.unit_price or 0),
        'production_date': b.production_date.isoformat() if b.production_date else None,
        'expire_date': b.expire_date.isoformat() if b.expire_date else None,
        'stock_in_date': b.stock_in_date.isoformat() if b.stock_in_date else None,
        'shelf_life_days': b.shelf_life_days
    } for b in batches])


@bp.route('/expiry_alerts')
@login_required
def expiry_alerts():
    """到期提醒"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    today = date.today()
    near_threshold = today + timedelta(days=NEAR_EXPIRE_DAYS)

    # 即将过期：到期日期在今天之后、阈值之内
    near_batches = db.session.query(InventoryBatch, Material).join(
        Material, Material.id == InventoryBatch.material_id
    ).filter(
        InventoryBatch.project_id == project_id,
        InventoryBatch.expire_date.isnot(None),
        InventoryBatch.expire_date > today,
        InventoryBatch.expire_date <= near_threshold
    ).order_by(InventoryBatch.expire_date.asc()).all()

    # 已过期：到期日期在今天或之前
    expired_batches = db.session.query(InventoryBatch, Material).join(
        Material, Material.id == InventoryBatch.material_id
    ).filter(
        InventoryBatch.project_id == project_id,
        InventoryBatch.expire_date.isnot(None),
        InventoryBatch.expire_date <= today
    ).order_by(InventoryBatch.expire_date.asc()).all()

    return render_template('batch/expiry_alerts.html',
                           near_batches=near_batches,
                           expired_batches=expired_batches,
                           today=today, near_threshold=near_threshold)


@bp.route('/api/fifo/<int:material_id>', methods=['POST'])
@login_required
def api_fifo(material_id):
    """先进先出出库建议"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': '未选择项目'}), 400

    try:
        qty_raw = request.form.get('quantity')
        if qty_raw is None:
            data = request.get_json(silent=True) or {}
            qty_raw = data.get('quantity', 0)
        quantity = float(qty_raw or 0)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': '数量格式错误'}), 400

    if quantity <= 0:
        return jsonify({'success': False, 'message': '数量必须大于0'}), 400

    material = Material.query.filter_by(
        project_id=project_id, id=material_id
    ).first()
    if not material:
        return jsonify({'success': False, 'message': '物资不存在'}), 404

    # 按先进先出顺序获取批次（先入库先出库，已过期的优先出库）
    batches = InventoryBatch.query.filter_by(
        project_id=project_id, material_id=material_id
    ).filter(InventoryBatch.quantity > 0).order_by(
        InventoryBatch.stock_in_date.asc(),
        InventoryBatch.expire_date.asc()
    ).all()

    total_available = sum(float(b.quantity or 0) for b in batches)
    if total_available <= 0:
        return jsonify({
            'success': False,
            'message': '该物资没有可用批次库存',
            'total_available': 0
        })

    suggestions = []
    remaining = quantity
    insufficient = False
    for b in batches:
        if remaining <= 0:
            break
        avail = float(b.quantity or 0)
        if avail <= 0:
            continue
        take = min(avail, remaining)
        suggestions.append({
            'batch_no': b.batch_no,
            'quantity': take,
            'expire_date': b.expire_date.isoformat() if b.expire_date else None,
            'stock_in_date': b.stock_in_date.isoformat() if b.stock_in_date else None
        })
        remaining -= take

    if remaining > 0:
        insufficient = True

    return jsonify({
        'success': True,
        'suggestions': suggestions,
        'total_available': total_available,
        'requested_quantity': quantity,
        'insufficient': insufficient,
        'short_quantity': round(remaining, 4) if insufficient else 0,
        'message': '批次库存不足，缺少 {:.4f}'.format(remaining) if insufficient else '库存充足'
    })


@bp.route('/api/check_expired')
@login_required
def api_check_expired():
    """检查物资是否有过期批次"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'has_expired': False, 'expired_batches': []})

    material_id = request.args.get('material_id', type=int)
    if not material_id:
        return jsonify({'has_expired': False, 'expired_batches': []})

    today = date.today()
    expired_batches = InventoryBatch.query.filter_by(
        project_id=project_id, material_id=material_id
    ).filter(
        InventoryBatch.expire_date.isnot(None),
        InventoryBatch.expire_date <= today,
        InventoryBatch.quantity > 0
    ).order_by(InventoryBatch.expire_date.asc()).all()

    return jsonify({
        'has_expired': len(expired_batches) > 0,
        'expired_batches': [{
            'id': b.id,
            'batch_no': b.batch_no,
            'quantity': float(b.quantity or 0),
            'expire_date': b.expire_date.isoformat() if b.expire_date else None
        } for b in expired_batches]
    })
