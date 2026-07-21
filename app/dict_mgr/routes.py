from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.dict_mgr import bp
from app import db
from app.models import SysDictType, SysDictItem
from app.decorators import admin_required, log_audit
from app.utils import clear_dict_cache


def _reorder_dict_items(dict_type_id):
    """删除字典项后重新整理排序号"""
    items = SysDictItem.query.filter_by(dict_type_id=dict_type_id).order_by(
        SysDictItem.sort_order, SysDictItem.id).all()
    for index, item in enumerate(items, 1):
        if item.sort_order != index:
            item.sort_order = index
    db.session.commit()


@bp.route('/')
@login_required
@admin_required
def index():
    """字典管理首页：左侧字典类型列表，右侧选中类型的字典项列表"""
    dict_types = SysDictType.query.order_by(SysDictType.created_at.desc()).all()
    selected_type_id = request.args.get('type_id', type=int)
    selected_type = None
    items = []
    if not dict_types and not selected_type_id:
        return render_template('dict_mgr/index.html', dict_types=dict_types,
                               selected_type=None, items=[])
    if not selected_type_id and dict_types:
        selected_type_id = dict_types[0].id

    if selected_type_id:
        selected_type = SysDictType.query.get_or_404(selected_type_id)
        items = SysDictItem.query.filter_by(dict_type_id=selected_type_id).order_by(
            SysDictItem.sort_order, SysDictItem.id).all()
    return render_template('dict_mgr/index.html', dict_types=dict_types,
                           selected_type=selected_type, items=items)


# ============== 字典类型 ==============

@bp.route('/type/create', methods=['POST'])
@login_required
@admin_required
@log_audit(module='dict_mgr', operation='新增字典')
def type_create():
    dict_type = request.form.get('dict_type', '').strip()
    dict_name = request.form.get('dict_name', '').strip()
    remark = request.form.get('remark', '').strip()
    is_active = request.form.get('is_active') == 'on'

    if not dict_type or not dict_name:
        flash('字典类型编码和名称不能为空。', 'danger')
        return redirect(url_for('dict_mgr.index'))

    if SysDictType.query.filter_by(dict_type=dict_type).first():
        flash('字典类型编码已存在。', 'danger')
        return redirect(url_for('dict_mgr.index'))

    dt = SysDictType(dict_type=dict_type, dict_name=dict_name, remark=remark, is_active=is_active)
    db.session.add(dt)
    db.session.commit()
    clear_dict_cache()
    flash('字典类型创建成功。', 'success')
    return redirect(url_for('dict_mgr.index', type_id=dt.id))


@bp.route('/type/<int:id>/edit', methods=['POST'])
@login_required
@admin_required
@log_audit(module='dict_mgr', operation='编辑字典')
def type_edit(id):
    dt = SysDictType.query.get_or_404(id)
    new_dict_type = request.form.get('dict_type', '').strip()
    dict_name = request.form.get('dict_name', '').strip()
    remark = request.form.get('remark', '').strip()
    is_active = request.form.get('is_active') == 'on'

    if not new_dict_type or not dict_name:
        flash('字典类型编码和名称不能为空。', 'danger')
        return redirect(url_for('dict_mgr.index', type_id=id))

    existing = SysDictType.query.filter_by(dict_type=new_dict_type).first()
    if existing and existing.id != id:
        flash('字典类型编码已被其他类型使用。', 'danger')
        return redirect(url_for('dict_mgr.index', type_id=id))

    dt.dict_type = new_dict_type
    dt.dict_name = dict_name
    dt.remark = remark
    dt.is_active = is_active
    db.session.commit()
    clear_dict_cache()
    flash('字典类型更新成功。', 'success')
    return redirect(url_for('dict_mgr.index', type_id=id))


@bp.route('/type/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='dict_mgr', operation='删除字典')
def type_delete(id):
    dt = SysDictType.query.get_or_404(id)
    if dt.items.count() > 0:
        flash('该字典类型下存在字典项，不能删除。', 'danger')
        return redirect(url_for('dict_mgr.index', type_id=id))
    db.session.delete(dt)
    db.session.commit()
    clear_dict_cache()
    flash('字典类型删除成功。', 'success')
    return redirect(url_for('dict_mgr.index'))


@bp.route('/type/<int:type_id>/items')
@login_required
@admin_required
def type_items(type_id):
    """返回指定类型的字典项列表JSON"""
    items = SysDictItem.query.filter_by(dict_type_id=type_id).order_by(
        SysDictItem.sort_order, SysDictItem.id).all()
    return jsonify([
        {
            'id': it.id,
            'item_label': it.item_label,
            'item_value': it.item_value,
            'sort_order': it.sort_order,
            'is_active': it.is_active
        }
        for it in items
    ])


# ============== 字典项 ==============

@bp.route('/item/create', methods=['POST'])
@login_required
@admin_required
@log_audit(module='dict_mgr', operation='新增字典项')
def item_create():
    dict_type_id = request.form.get('dict_type_id', type=int)
    item_label = request.form.get('item_label', '').strip()
    item_value = request.form.get('item_value', '').strip()
    sort_order = request.form.get('sort_order', type=int, default=None)
    is_active = request.form.get('is_active') == 'on'

    if not dict_type_id or not item_label or not item_value:
        flash('所属类型、标签、值不能为空。', 'danger')
        return redirect(url_for('dict_mgr.index', type_id=dict_type_id))

    if sort_order is None:
        max_sort = db.session.query(db.func.max(SysDictItem.sort_order)).filter_by(dict_type_id=dict_type_id).scalar() or 0
        sort_order = max_sort + 1

    SysDictType.query.get_or_404(dict_type_id)
    item = SysDictItem(
        dict_type_id=dict_type_id,
        item_label=item_label,
        item_value=item_value,
        sort_order=sort_order,
        is_active=is_active
    )
    db.session.add(item)
    db.session.commit()
    clear_dict_cache()
    flash('字典项创建成功。', 'success')
    return redirect(url_for('dict_mgr.index', type_id=dict_type_id))


@bp.route('/item/batch_create', methods=['POST'])
@login_required
@admin_required
@log_audit(module='dict_mgr', operation='批量新增字典项')
def item_batch_create():
    dict_type_id = request.form.get('dict_type_id', type=int)
    batch_items = request.form.get('batch_items', '').strip()
    is_active = request.form.get('is_active') == 'on'

    if not dict_type_id:
        flash('所属类型不能为空。', 'danger')
        return redirect(url_for('dict_mgr.index'))

    if not batch_items:
        flash('请输入字典项列表。', 'danger')
        return redirect(url_for('dict_mgr.index', type_id=dict_type_id))

    SysDictType.query.get_or_404(dict_type_id)

    max_sort = db.session.query(db.func.max(SysDictItem.sort_order)).filter_by(dict_type_id=dict_type_id).scalar() or 0
    base_sort = max_sort + 1
    sort_step = 1

    lines = batch_items.split('\n')
    created_count = 0
    errors = []

    for idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        parts = line.split(',', 1)
        if len(parts) != 2:
            errors.append(f'第{idx+1}行格式错误：{line}')
            continue
        item_label, item_value = parts[0].strip(), parts[1].strip()
        if not item_label or not item_value:
            errors.append(f'第{idx+1}行标签或值为空：{line}')
            continue

        existing = SysDictItem.query.filter_by(dict_type_id=dict_type_id, item_value=item_value).first()
        if existing:
            errors.append(f'第{idx+1}行值已存在：{item_value}')
            continue

        item = SysDictItem(
            dict_type_id=dict_type_id,
            item_label=item_label,
            item_value=item_value,
            sort_order=base_sort + idx * sort_step,
            is_active=is_active
        )
        db.session.add(item)
        created_count += 1

    if created_count > 0:
        db.session.commit()
        clear_dict_cache()

    if errors:
        flash(f'批量创建完成，成功{created_count}条，失败{len(errors)}条：{"; ".join(errors[:5])}{"..." if len(errors) > 5 else ""}', 'warning')
    else:
        flash(f'批量创建成功，共{created_count}条。', 'success')

    return redirect(url_for('dict_mgr.index', type_id=dict_type_id))


@bp.route('/item/<int:id>/edit', methods=['POST'])
@login_required
@admin_required
@log_audit(module='dict_mgr', operation='编辑字典项')
def item_edit(id):
    item = SysDictItem.query.get_or_404(id)
    item_label = request.form.get('item_label', '').strip()
    item_value = request.form.get('item_value', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int)
    is_active = request.form.get('is_active') == 'on'

    if not item_label or not item_value:
        flash('标签、值不能为空。', 'danger')
        return redirect(url_for('dict_mgr.index', type_id=item.dict_type_id))

    item.item_label = item_label
    item.item_value = item_value
    item.sort_order = sort_order
    item.is_active = is_active
    db.session.commit()
    clear_dict_cache()
    flash('字典项更新成功。', 'success')
    return redirect(url_for('dict_mgr.index', type_id=item.dict_type_id))


@bp.route('/item/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='dict_mgr', operation='删除字典项')
def item_delete(id):
    item = SysDictItem.query.get_or_404(id)
    type_id = item.dict_type_id
    db.session.delete(item)
    db.session.commit()
    _reorder_dict_items(type_id)
    clear_dict_cache()
    flash('字典项删除成功。', 'success')
    return redirect(url_for('dict_mgr.index', type_id=type_id))


@bp.route('/item/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
@log_audit(module='dict_mgr', operation='启用/禁用字典项')
def item_toggle(id):
    item = SysDictItem.query.get_or_404(id)
    item.is_active = not item.is_active
    db.session.commit()
    clear_dict_cache()
    flash(f'字典项已{"启用" if item.is_active else "禁用"}。', 'success')
    return redirect(url_for('dict_mgr.index', type_id=item.dict_type_id))
