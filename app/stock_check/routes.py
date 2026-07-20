from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify, Response
from flask_login import login_required, current_user
from sqlalchemy import or_, func

from app.stock_check import bp
from app import db
from app.models import StockCheck, StockCheckItem, Inventory, Material, Category
from app.decorators import editor_required, log_audit
from app.utils import to_decimal, apply_data_scope, get_project_materials


def _gen_check_no(project_id):
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"PD-{project_id}-{today}-"
    existing = StockCheck.query.filter(StockCheck.check_no.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


def _apply_inventory_add(project_id, material_id, quantity):
    inv = Inventory.query.filter_by(project_id=project_id, material_id=material_id).first()
    if not inv:
        inv = Inventory(project_id=project_id, material_id=material_id, quantity=0)
        db.session.add(inv)
        db.session.flush()
    inv.quantity = to_decimal(inv.quantity) + to_decimal(quantity)


def _apply_inventory_sub(project_id, material_id, quantity):
    inv = Inventory.query.filter_by(project_id=project_id, material_id=material_id).first()
    if not inv:
        return
    new_qty = to_decimal(inv.quantity) - to_decimal(quantity)
    if new_qty < 0:
        new_qty = to_decimal(0)
    inv.quantity = new_qty


def _get_category_descendants(category_id):
    result = []
    cat = Category.query.get(category_id)
    if not cat:
        return result
    result.append(category_id)
    if cat.level < 3:
        children = Category.query.filter_by(parent_id=category_id).all()
        for child in children:
            result.extend(_get_category_descendants(child.id))
    return result


@bp.route('/')
@login_required
def index():
    from flask import session
    project_id = session.get('current_project_id')
    # 全部数据权限用户在"全部项目"模式下不限制项目
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    check_type = request.args.get('check_type', '', type=str)
    status = request.args.get('status', '', type=str)
    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)

    query = StockCheck.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, StockCheck)
    if keyword:
        query = query.filter(or_(StockCheck.check_no.contains(keyword), StockCheck.remark.contains(keyword)))
    if check_type:
        query = query.filter_by(check_type=check_type)
    if status:
        query = query.filter_by(status=status)
    if date_from:
        try:
            query = query.filter(StockCheck.check_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            query = query.filter(StockCheck.check_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass

    pagination = query.order_by(StockCheck.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)

    return render_template('stock_check/index.html', pagination=pagination, keyword=keyword,
                           check_type=check_type, status=status,
                           date_from=date_from, date_to=date_to)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='stock_check', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        check_date_str = request.form.get('check_date')
        try:
            check_date = datetime.strptime(check_date_str, '%Y-%m-%d').date()
        except Exception:
            check_date = date.today()

        check_type = request.form.get('check_type', 'full')
        category_id = request.form.get('category_id', type=int) or None

        if check_type == 'category' and not category_id:
            flash('按分类盘点时请选择分类。', 'danger')
            return redirect(url_for('stock_check.create'))

        stock_check = StockCheck(
            project_id=project_id,
            check_no=_gen_check_no(project_id),
            check_date=check_date,
            check_type=check_type,
            category_id=category_id,
            status='draft',
            checker=request.form.get('checker', '').strip() or None,
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(stock_check)
        db.session.flush()

        if check_type == 'full':
            inventories = Inventory.query.filter_by(project_id=project_id).all()
            for inv in inventories:
                mat = Material.query.get(inv.material_id)
                if not mat:
                    continue
                item = StockCheckItem(
                    check_id=stock_check.id,
                    material_id=mat.id,
                    material_name=mat.name,
                    specification=mat.specification,
                    unit=mat.unit,
                    book_qty=inv.quantity,
                    actual_qty=None,
                    diff_reason=''
                )
                db.session.add(item)
        else:
            cat_ids = _get_category_descendants(category_id)
            materials = get_project_materials(project_id, common_only=True).filter(
                Material.category_id.in_(cat_ids)
            ).all()
            for mat in materials:
                inv = Inventory.query.filter_by(project_id=project_id, material_id=mat.id).first()
                book_qty = inv.quantity if inv else 0
                item = StockCheckItem(
                    check_id=stock_check.id,
                    material_id=mat.id,
                    material_name=mat.name,
                    specification=mat.specification,
                    unit=mat.unit,
                    book_qty=book_qty,
                    actual_qty=None,
                    diff_reason=''
                )
                db.session.add(item)

        db.session.commit()
        flash('盘点单创建成功。', 'success')
        return redirect(url_for('stock_check.detail', id=stock_check.id))

    categories = Category.query.filter_by(project_id=project_id, parent_id=0).order_by(Category.sort_order).all()
    return render_template('stock_check/create.html', categories=categories,
                           default_check_no=_gen_check_no(project_id),
                           today_str=date.today().strftime('%Y-%m-%d'))


@bp.route('/<int:id>')
@login_required
def detail(id):
    stock_check = StockCheck.query.get_or_404(id)
    category_name = ''
    if stock_check.check_type == 'category' and stock_check.category_id:
        cat = Category.query.get(stock_check.category_id)
        if cat:
            category_name = cat.name
    return render_template('stock_check/detail.html', stock_check=stock_check, category_name=category_name)


@bp.route('/<int:id>/print')
@login_required
def print_stock_check(id):
    """打印盘点单"""
    stock_check = StockCheck.query.get_or_404(id)
    category_name = ''
    if stock_check.check_type == 'category' and stock_check.category_id:
        cat = Category.query.get(stock_check.category_id)
        if cat:
            category_name = cat.name
    now_date = datetime.now().strftime('%Y-%m-%d')
    return render_template('stock_check/print.html', stock_check=stock_check,
                           category_name=category_name, now_date=now_date)


@bp.route('/<int:id>/save', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_check', operation='保存实盘')
def save(id):
    stock_check = StockCheck.query.get_or_404(id)
    if stock_check.status == 'confirmed':
        flash('已确认的盘点单不能修改。', 'danger')
        return redirect(url_for('stock_check.detail', id=id))
    if stock_check.status == 'cancelled':
        flash('已作废的盘点单不能修改。', 'danger')
        return redirect(url_for('stock_check.detail', id=id))

    item_ids = request.form.getlist('item_id[]')
    actual_qtys = request.form.getlist('actual_qty[]')
    diff_reasons = request.form.getlist('diff_reason[]')

    for idx, iid in enumerate(item_ids):
        if not iid:
            continue
        item = StockCheckItem.query.get(int(iid))
        if not item or item.check_id != stock_check.id:
            continue
        try:
            aq = actual_qtys[idx] if idx < len(actual_qtys) else ''
            item.actual_qty = to_decimal(aq) if aq != '' else None
        except Exception:
            item.actual_qty = None
        dr = diff_reasons[idx] if idx < len(diff_reasons) else ''
        item.diff_reason = dr.strip()

    if stock_check.status == 'draft':
        stock_check.status = 'ongoing'

    db.session.commit()
    flash('实盘数据已保存。', 'success')
    return redirect(url_for('stock_check.detail', id=id))


@bp.route('/<int:id>/confirm', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_check', operation='确认盘点')
def confirm(id):
    stock_check = StockCheck.query.get_or_404(id)
    if stock_check.status == 'confirmed':
        flash('盘点单已确认。', 'warning')
        return redirect(url_for('stock_check.detail', id=id))
    if stock_check.status == 'cancelled':
        flash('已作废的盘点单不能确认。', 'danger')
        return redirect(url_for('stock_check.detail', id=id))

    for item in stock_check.items:
        if item.actual_qty is None:
            flash(f'物资 {item.material_name} 未填写实盘数量，请先完成盘点。', 'danger')
            return redirect(url_for('stock_check.detail', id=id))

    for item in stock_check.items:
        diff = float(item.actual_qty or 0) - float(item.book_qty or 0)
        if diff > 0:
            _apply_inventory_add(stock_check.project_id, item.material_id, diff)
        elif diff < 0:
            _apply_inventory_sub(stock_check.project_id, item.material_id, abs(diff))

    stock_check.status = 'confirmed'
    db.session.commit()
    flash('盘点确认成功，库存已更新。', 'success')
    return redirect(url_for('stock_check.detail', id=id))


@bp.route('/<int:id>/cancel', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_check', operation='作废')
def cancel(id):
    stock_check = StockCheck.query.get_or_404(id)
    if stock_check.status != 'confirmed':
        flash('只有已确认的盘点单才能作废。', 'danger')
        return redirect(url_for('stock_check.detail', id=id))

    for item in stock_check.items:
        diff = float(item.actual_qty or 0) - float(item.book_qty or 0)
        if diff > 0:
            _apply_inventory_sub(stock_check.project_id, item.material_id, diff)
        elif diff < 0:
            _apply_inventory_add(stock_check.project_id, item.material_id, abs(diff))

    stock_check.status = 'cancelled'
    db.session.commit()
    flash('盘点单已作废，库存已冲销。', 'success')
    return redirect(url_for('stock_check.detail', id=id))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_check', operation='删除')
def delete(id):
    stock_check = StockCheck.query.get_or_404(id)
    if stock_check.status not in ('draft', 'ongoing'):
        flash('只有草稿或进行中的盘点单才能删除。', 'danger')
        return redirect(url_for('stock_check.detail', id=id))

    db.session.delete(stock_check)
    db.session.commit()
    flash('盘点单已删除。', 'success')
    return redirect(url_for('stock_check.index'))


@bp.route('/<int:id>/export')
@login_required
def export(id):
    stock_check = StockCheck.query.get_or_404(id)

    headers = ['序号', '物资名称', '规格型号', '单位', '账面数量', '实盘数量', '差异数量', '差异原因']
    rows = []
    for idx, item in enumerate(stock_check.items, 1):
        diff = float(item.actual_qty or 0) - float(item.book_qty or 0) if item.actual_qty is not None else ''
        rows.append([
            idx,
            item.material_name,
            item.specification or '',
            item.unit or '',
            float(item.book_qty or 0),
            float(item.actual_qty) if item.actual_qty is not None else '',
            diff if diff != '' else '',
            item.diff_reason or ''
        ])

    from app.utils import export_to_excel
    filename = f'盘点单_{stock_check.check_no}_{datetime.now().strftime("%Y%m%d")}.xlsx'
    data = export_to_excel(headers, rows, '盘点明细', filename)

    return Response(data, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})


@bp.route('/api/category_children/<int:parent_id>')
@login_required
def api_category_children(parent_id):
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])

    query = Category.query.filter_by(project_id=project_id)
    if parent_id == 0:
        query = query.filter_by(parent_id=0)
    else:
        query = query.filter_by(parent_id=parent_id)

    categories = query.order_by(Category.sort_order.asc(), Category.created_at.asc()).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'level': c.level,
        'parent_id': c.parent_id
    } for c in categories])
