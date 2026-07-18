import csv
import io
from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required
from sqlalchemy import or_, func, literal

from app.inventory import bp
from app import db
from app.models import Inventory, Material, Category, StockIn, StockInItem, StockOut, StockOutItem

@bp.route('/')
@login_required
def index():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    keyword = request.args.get('keyword', '', type=str)
    category_l1 = request.args.get('category_l1', 0, type=int)
    category_l2 = request.args.get('category_l2', 0, type=int)
    category_l3 = request.args.get('category_l3', 0, type=int)

    subq = db.session.query(
        Material.id,
        func.coalesce(func.sum(StockInItem.quantity), 0).label('total_in'),
        func.coalesce(func.sum(StockOutItem.quantity), 0).label('total_out')
    ).outerjoin(StockInItem, StockInItem.material_id == Material.id).outerjoin(
        StockIn, StockIn.id == StockInItem.stock_in_id
    ).outerjoin(StockOutItem, StockOutItem.material_id == Material.id).outerjoin(
        StockOut, StockOut.id == StockOutItem.stock_out_id
    ).filter(Material.project_id == project_id).group_by(Material.id).subquery()

    query = db.session.query(
        Material,
        Category,
        func.coalesce(Inventory.quantity, 0).label('current_stock'),
        func.coalesce(Inventory.in_transit_qty, 0).label('in_transit_qty'),
        func.coalesce(Inventory.estimated_amount, 0).label('estimated_amount'),
        func.coalesce(Inventory.actual_amount, 0).label('actual_amount')
    ).outerjoin(Category, Category.id == Material.category_id).outerjoin(
        Inventory, Inventory.material_id == Material.id
    ).filter(Material.project_id == project_id)

    if keyword:
        query = query.filter(or_(Material.name.contains(keyword), Material.code.contains(keyword)))
    if category_l3:
        query = query.filter(Material.category_id == category_l3)
    elif category_l2:
        child_ids = [c.id for c in Category.query.filter_by(parent_id=category_l2).all()]
        if child_ids:
            query = query.filter(Material.category_id.in_(child_ids))
    elif category_l1:
        level2_ids = [c.id for c in Category.query.filter_by(parent_id=category_l1).all()]
        level3_ids = []
        for cid in level2_ids:
            level3_ids.extend([cc.id for cc in Category.query.filter_by(parent_id=cid).all()])
        all_ids = level2_ids + level3_ids
        if all_ids:
            query = query.filter(Material.category_id.in_(all_ids))

    inventories = query.all()

    categories = Category.query.filter_by(project_id=project_id).order_by(Category.sort_order.asc()).all()
    materials = Material.query.filter_by(project_id=project_id).order_by(Material.name).all()

    from flask_login import current_user
    from app.utils import get_config
    show_estimated = request.args.get('show_estimated', '0') == '1'
    allow_initial = get_config('allow_initial_stock', 'false') == 'true' and current_user.is_admin()

    return render_template('inventory/index.html', inventories=inventories, keyword=keyword,
                           category_l1=category_l1, category_l2=category_l2, category_l3=category_l3,
                           categories=categories, materials=materials, show_estimated=show_estimated,
                           allow_initial=allow_initial)


@bp.route('/export')
@login_required
def export():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['物资编码', '物资名称', '规格型号', '物资分类', '单位', '当前库存'])

    subq = db.session.query(
        Material.id,
        func.coalesce(func.sum(StockInItem.quantity), 0).label('total_in'),
        func.coalesce(func.sum(StockOutItem.quantity), 0).label('total_out')
    ).outerjoin(StockInItem, StockInItem.material_id == Material.id).outerjoin(
        StockIn, StockIn.id == StockInItem.stock_in_id
    ).outerjoin(StockOutItem, StockOutItem.material_id == Material.id).outerjoin(
        StockOut, StockOut.id == StockOutItem.stock_out_id
    ).filter(Material.project_id == project_id).group_by(Material.id).subquery()

    query = db.session.query(
        Material,
        Category,
        func.coalesce(Inventory.quantity, 0).label('current_stock')
    ).outerjoin(Category, Category.id == Material.category_id).outerjoin(
        Inventory, Inventory.material_id == Material.id
    ).outerjoin(subq, subq.c.id == Material.id).filter(Material.project_id == project_id)

    keyword = request.args.get('keyword', '', type=str)
    category_l1 = request.args.get('category_l1', 0, type=int)
    category_l2 = request.args.get('category_l2', 0, type=int)
    category_l3 = request.args.get('category_l3', 0, type=int)
    if keyword:
        query = query.filter(or_(Material.name.contains(keyword), Material.code.contains(keyword)))
    if category_l3:
        query = query.filter(Material.category_id == category_l3)
    elif category_l2:
        child_ids = [c.id for c in Category.query.filter_by(parent_id=category_l2).all()]
        if child_ids:
            query = query.filter(Material.category_id.in_(child_ids))
    elif category_l1:
        level2_ids = [c.id for c in Category.query.filter_by(parent_id=category_l1).all()]
        level3_ids = []
        for cid in level2_ids:
            level3_ids.extend([cc.id for cc in Category.query.filter_by(parent_id=cid).all()])
        all_ids = level2_ids + level3_ids
        if all_ids:
            query = query.filter(Material.category_id.in_(all_ids))

    for m, c, stock in query:
        writer.writerow([
            m.code or '',
            m.name,
            m.specification or '',
            c.name if c else '',
            m.unit,
            float(stock)
        ])

    output.seek(0)
    filename = f"库存报表_{project_id}_{datetime.now().strftime('%Y%m%d')}.csv"
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True, download_name=filename)


@bp.route('/<int:material_id>/history')
@login_required
def history(material_id):
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    material = Material.query.filter_by(project_id=project_id, id=material_id).first_or_404()

    stock_ins = db.session.query(
        StockIn.code.label('code'),
        StockIn.stock_in_date.label('date'),
        StockIn.stock_in_type.label('type'),
        StockInItem.quantity.label('quantity'),
        StockInItem.amount.label('amount'),
        literal('入库').label('direction')
    ).join(StockInItem, StockInItem.stock_in_id == StockIn.id).filter(
        StockIn.project_id == project_id, StockInItem.material_id == material_id
    )

    stock_outs = db.session.query(
        StockOut.code.label('code'),
        StockOut.stock_out_date.label('date'),
        StockOut.stock_out_type.label('type'),
        StockOutItem.quantity.label('quantity'),
        StockOutItem.amount.label('amount'),
        literal('出库').label('direction')
    ).join(StockOutItem, StockOutItem.stock_out_id == StockOut.id).filter(
        StockOut.project_id == project_id, StockOutItem.material_id == material_id
    )

    history = stock_ins.union_all(stock_outs).order_by('date', 'code').all()
    return render_template('inventory/history.html', material=material, history=history)


def _gen_initial_stock_code(project_id):
    """生成期初中单号"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"QC-{project_id}-{today}-"
    existing = StockIn.query.filter(StockIn.code.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


@bp.route('/initial/single', methods=['POST'])
@login_required
def initial_single():
    """单条期初录入"""
    from flask import session
    from flask_login import current_user
    from app.utils import get_config, to_decimal

    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('inventory.index'))

    if not current_user.is_admin():
        flash('无权限进行期初录入。', 'danger')
        return redirect(url_for('inventory.index'))

    if get_config('allow_initial_stock', 'false') != 'true':
        flash('系统未开启期初录入功能。', 'danger')
        return redirect(url_for('inventory.index'))

    material_id = request.form.get('material_id', type=int)
    quantity = request.form.get('quantity', '0')
    unit_price = request.form.get('unit_price', '0')

    if not material_id:
        flash('请选择物资。', 'warning')
        return redirect(url_for('inventory.index'))

    try:
        qty = to_decimal(quantity)
        price = to_decimal(unit_price)
    except Exception:
        flash('数量或单价格式错误。', 'warning')
        return redirect(url_for('inventory.index'))

    if qty <= 0:
        flash('期初数量必须大于0。', 'warning')
        return redirect(url_for('inventory.index'))

    amount = float(qty) * float(price)

    price_status = 'confirmed' if float(price or 0) > 0 else 'unpriced'

    stock_in = StockIn(
        project_id=project_id,
        code=_gen_initial_stock_code(project_id),
        stock_in_date=date.today(),
        stock_in_type='期初入库',
        operator=current_user.name or current_user.username,
        remark='期初库存录入',
        total_quantity=qty,
        total_amount=amount,
        estimated_amount=amount if price_status == 'estimated' else 0,
        actual_amount=amount if price_status == 'confirmed' else 0,
        is_initial=True,
        approval_status='passed'
    )
    db.session.add(stock_in)
    db.session.flush()

    item = StockInItem(
        stock_in_id=stock_in.id,
        material_id=material_id,
        quantity=qty,
        unit_price=price,
        amount=amount,
        price_status=price_status
    )
    db.session.add(item)

    inv = Inventory.query.filter_by(project_id=project_id, material_id=material_id).first()
    if not inv:
        inv = Inventory(project_id=project_id, material_id=material_id,
                        quantity=0, estimated_amount=0, actual_amount=0)
        db.session.add(inv)

    inv.quantity = to_decimal(inv.quantity) + qty
    if price_status == 'estimated':
        inv.estimated_amount = to_decimal(inv.estimated_amount or 0) + to_decimal(amount)
    elif price_status == 'confirmed':
        inv.actual_amount = to_decimal(inv.actual_amount or 0) + to_decimal(amount)

    db.session.commit()
    flash('期初库存录入成功。', 'success')
    return redirect(url_for('inventory.index'))


@bp.route('/initial/template')
@login_required
def initial_template():
    """下载期初库存导入模板"""
    from flask import Response
    from app.utils import export_to_excel

    headers = ['物资编码', '物资名称', '规格', '单位', '期初数量', '期初单价']
    rows = [
        ['MC0101001', '示例物资', '规格示例', '个', 100, 10.5]
    ]
    from datetime import datetime
    filename = '期初库存导入模板.xlsx'
    data = export_to_excel(headers, rows, '期初库存模板', filename)
    return Response(data, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})


@bp.route('/initial/batch', methods=['POST'])
@login_required
def initial_batch():
    """Excel批量导入期初库存"""
    from flask import session
    from flask_login import current_user
    from app.utils import get_config, to_decimal

    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('inventory.index'))

    if not current_user.is_admin():
        flash('无权限进行期初录入。', 'danger')
        return redirect(url_for('inventory.index'))

    if get_config('allow_initial_stock', 'false') != 'true':
        flash('系统未开启期初录入功能。', 'danger')
        return redirect(url_for('inventory.index'))

    file = request.files.get('file')
    if not file:
        flash('请选择要上传的文件。', 'warning')
        return redirect(url_for('inventory.index'))

    try:
        import openpyxl
        wb = openpyxl.load_workbook(file)
        ws = wb.active
    except Exception as e:
        flash(f'文件读取失败：{e}', 'danger')
        return redirect(url_for('inventory.index'))

    success_count = 0
    fail_count = 0
    fail_msgs = []
    items_data = []

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(v is None or v == '' for v in row):
            continue
        try:
            mat_code = str(row[0]).strip() if row[0] else ''
            qty_str = str(row[4]).strip() if len(row) > 4 and row[4] else '0'
            price_str = str(row[5]).strip() if len(row) > 5 and row[5] else '0'

            if not mat_code:
                raise ValueError('物资编码为空')

            mat = Material.query.filter_by(project_id=project_id, code=mat_code).first()
            if not mat:
                raise ValueError(f'物资编码 {mat_code} 不存在')

            qty = to_decimal(qty_str)
            price = to_decimal(price_str)

            if qty <= 0:
                raise ValueError('期初数量必须大于0')

            amount = float(qty) * float(price)
            price_status = 'confirmed' if float(price or 0) > 0 else 'unpriced'

            items_data.append({
                'material_id': mat.id,
                'material_name': mat.name,
                'quantity': qty,
                'unit_price': price,
                'amount': amount,
                'price_status': price_status
            })
            success_count += 1
        except Exception as e:
            fail_count += 1
            fail_msgs.append(f'第{row_idx}行：{str(e)}')

    if not items_data:
        flash('没有可导入的有效数据。', 'warning')
        return redirect(url_for('inventory.index'))

    total_qty = sum(it['quantity'] for it in items_data)
    total_amount = sum(it['amount'] for it in items_data)
    total_estimated = sum(it['amount'] for it in items_data if it['price_status'] == 'estimated')
    total_actual = sum(it['amount'] for it in items_data if it['price_status'] == 'confirmed')

    stock_in = StockIn(
        project_id=project_id,
        code=_gen_initial_stock_code(project_id),
        stock_in_date=date.today(),
        stock_in_type='期初入库',
        operator=current_user.name or current_user.username,
        remark=f'期初库存批量导入（共{success_count}条）',
        total_quantity=total_qty,
        total_amount=total_amount,
        estimated_amount=total_estimated,
        actual_amount=total_actual,
        is_initial=True,
        approval_status='passed'
    )
    db.session.add(stock_in)
    db.session.flush()

    for it in items_data:
        item = StockInItem(
            stock_in_id=stock_in.id,
            material_id=it['material_id'],
            quantity=it['quantity'],
            unit_price=it['unit_price'],
            amount=it['amount'],
            price_status=it['price_status']
        )
        db.session.add(item)

        inv = Inventory.query.filter_by(project_id=project_id, material_id=it['material_id']).first()
        if not inv:
            inv = Inventory(project_id=project_id, material_id=it['material_id'],
                            quantity=0, estimated_amount=0, actual_amount=0)
            db.session.add(inv)
        inv.quantity = to_decimal(inv.quantity) + it['quantity']
        if it['price_status'] == 'estimated':
            inv.estimated_amount = to_decimal(inv.estimated_amount or 0) + to_decimal(it['amount'])
        elif it['price_status'] == 'confirmed':
            inv.actual_amount = to_decimal(inv.actual_amount or 0) + to_decimal(it['amount'])

    db.session.commit()

    msg = f'导入完成：成功{success_count}条'
    if fail_count > 0:
        msg += f'，失败{fail_count}条'
        flash(msg, 'warning')
        for fm in fail_msgs[:5]:
            flash(fm, 'warning')
        if len(fail_msgs) > 5:
            flash(f'...还有{len(fail_msgs) - 5}条错误未显示', 'warning')
    else:
        flash(msg, 'success')

    return redirect(url_for('inventory.index'))
