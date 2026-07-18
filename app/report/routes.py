from flask import render_template, render_template_string, request, session, Response, flash, redirect, url_for
from flask_login import login_required
import urllib.parse
from sqlalchemy import func, and_, extract, literal_column
from datetime import datetime, timedelta
from decimal import Decimal
from app.report import bp
from app.models import (StockIn, StockInItem, StockOut, StockOutItem,
                        Material, Supplier, Category, UsageUnit,
                        ContractItem, Contract, ReconciliationItem,
                        Reconciliation, Inventory)
from app import db
from app.utils import export_to_excel


def _make_export_response(data, filename):
    """生成带中文文件名的导出响应"""
    encoded_filename = urllib.parse.quote(filename, safe='')
    return Response(
        data,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': f'attachment; filename="{encoded_filename}"; filename*=UTF-8\'\'{encoded_filename}'
        }
    )


def _calc_amount_without_tax(amount_with_tax, tax_rate):
    """根据含税金额和税率计算不含税金额，保留2位小数"""
    try:
        rate = Decimal(str(tax_rate or 13)) / Decimal('100')
        if rate > 0:
            return float(round(Decimal(str(amount_with_tax or 0)) / (Decimal('1') + rate), 2))
    except Exception:
        pass
    return float(amount_with_tax or 0)


def _build_reconciliation_settlement_map(stock_in_ids):
    """构建已对账入库的结算单价映射: {(stock_in_id, material_id): settlement_price}
    用于已对账入库按实际结算单价计算金额"""
    result = {}
    if not stock_in_ids:
        return result
    recon_items = db.session.query(ReconciliationItem).join(
        Reconciliation, ReconciliationItem.reconciliation_id == Reconciliation.id
    ).filter(
        Reconciliation.status == '已确认',
        ReconciliationItem.source_stock_in_ids.isnot(None)
    ).all()
    for ri in recon_items:
        if not ri.source_stock_in_ids:
            continue
        for sid in ri.source_stock_in_ids.split(','):
            sid = sid.strip()
            if sid.isdigit():
                key = (int(sid), ri.material_id)
                if key not in result:
                    result[key] = float(ri.settlement_price or 0)
    return result


def _build_contract_item_tax_map(contract_item_ids):
    """构建合同明细税率映射: {contract_item_id: tax_rate}"""
    result = {}
    if not contract_item_ids:
        return result
    cis = ContractItem.query.filter(ContractItem.id.in_(list(contract_item_ids))).all()
    for ci in cis:
        result[ci.id] = float(ci.tax_rate or 13)
    return result


def _build_inventory_unit_cost_map(project_id):
    """构建库存不含税加权单价映射: {material_id: 不含税单价}
    基于 Inventory.actual_amount / quantity 计算"""
    result = {}
    if not project_id:
        return result
    invs = Inventory.query.filter_by(project_id=project_id).all()
    for inv in invs:
        qty = float(inv.quantity or 0)
        if qty > 0:
            result[inv.material_id] = float(inv.actual_amount or 0) / qty
    return result


@bp.route('/')
@login_required
def index():
    """报表中心首页"""
    return render_template('report/index.html')


@bp.route('/stock_in')
@login_required
def stock_in_report():
    """入库统计报表"""
    project_id = session.get('current_project_id')
    view_type = request.args.get('view', 'summary')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    supplier_id = request.args.get('supplier_id', '0')
    category_l1 = request.args.get('category_l1', '0')
    category_l2 = request.args.get('category_l2', '0')
    category_l3 = request.args.get('category_l3', '0')

    suppliers = Supplier.query.filter_by(project_id=project_id).all() if project_id else []
    categories = Category.query.filter_by(project_id=project_id).all() if project_id else []

    def _get_category_ids(l1, l2, l3):
        l1, l2, l3 = int(l1), int(l2), int(l3)
        if l3 > 0:
            return [l3]
        elif l2 > 0:
            return [c.id for c in Category.query.filter_by(parent_id=l2).all()]
        elif l1 > 0:
            ids = []
            for l2c in Category.query.filter_by(parent_id=l1).all():
                ids.append(l2c.id)
                ids.extend([c.id for c in Category.query.filter_by(parent_id=l2c.id).all()])
            return ids
        return []

    if not project_id:
        return render_template('report/stock_in.html', view_type=view_type,
                               suppliers=suppliers, categories=categories,
                               start_date=start_date, end_date=end_date,
                               supplier_id=int(supplier_id), category_l1=int(category_l1),
                               category_l2=int(category_l2), category_l3=int(category_l3),
                               summary_data=[], detail_data=[])

    query = StockIn.query.filter(StockIn.project_id == project_id)
    if start_date:
        query = query.filter(StockIn.stock_in_date >= start_date)
    if end_date:
        query = query.filter(StockIn.stock_in_date <= end_date)
    if supplier_id and int(supplier_id) > 0:
        query = query.filter(StockIn.supplier_id == int(supplier_id))

    stock_ins = query.order_by(StockIn.stock_in_date.desc()).all()
    stock_in_ids = [si.id for si in stock_ins]

    category_ids = _get_category_ids(category_l1, category_l2, category_l3)

    # 预构建已对账入库的结算单价映射，用于已对账入库取实际结算金额
    reconcil_map = _build_reconciliation_settlement_map(stock_in_ids)

    # 预构建合同明细税率映射
    contract_item_ids = set()
    for si in stock_ins:
        for it in si.items:
            if it.contract_item_id:
                contract_item_ids.add(it.contract_item_id)
    ci_tax_map = _build_contract_item_tax_map(contract_item_ids)

    def _calc_item_amounts(item, si, material_id):
        """计算单个入库明细的含税金额、不含税金额、价格状态标签
        已对账入库按 ReconciliationItem.settlement_price 计算实际金额；
        未对账入库按 StockInItem.amount 暂估金额。"""
        tax_rate = ci_tax_map.get(item.contract_item_id, 13) if item.contract_item_id else 13
        if si.is_reconciled and (si.id, material_id) in reconcil_map:
            settlement_price = reconcil_map.get((si.id, material_id), 0)
            amount_with_tax = settlement_price * float(item.quantity or 0)
            price_label = '实际'
        else:
            amount_with_tax = float(item.amount or 0)
            price_label = '暂估'
        amount_without_tax = _calc_amount_without_tax(amount_with_tax, tax_rate)
        return amount_with_tax, amount_without_tax, price_label

    if view_type == 'summary':
        # 按物资汇总含税/不含税双口径金额
        items_query = db.session.query(
            StockInItem, StockIn, Material, Category
        ).join(
            StockIn, StockInItem.stock_in_id == StockIn.id
        ).join(
            Material, StockInItem.material_id == Material.id
        ).outerjoin(
            Category, Category.id == Material.category_id
        ).filter(StockIn.id.in_(stock_in_ids))
        if category_ids:
            items_query = items_query.filter(Material.category_id.in_(category_ids))

        summary_dict = {}
        for item, si, mat, cat in items_query.all():
            amt_with_tax, amt_without_tax, _ = _calc_item_amounts(item, si, mat.id)
            key = mat.id
            if key not in summary_dict:
                summary_dict[key] = {
                    'name': mat.name,
                    'specification': mat.specification,
                    'unit': mat.unit,
                    'category_name': cat.name if cat else None,
                    'total_qty': 0.0,
                    'total_amount': 0.0,
                    'total_amount_without_tax': 0.0,
                }
            summary_dict[key]['total_qty'] += float(item.quantity or 0)
            summary_dict[key]['total_amount'] += amt_with_tax
            summary_dict[key]['total_amount_without_tax'] += amt_without_tax

        summary_data = sorted(summary_dict.values(),
                              key=lambda x: x['total_amount'], reverse=True)
        for d in summary_data:
            d['total_qty'] = round(d['total_qty'], 4)
            d['total_amount'] = round(d['total_amount'], 2)
            d['total_amount_without_tax'] = round(d['total_amount_without_tax'], 2)

        return render_template('report/stock_in.html', view_type=view_type,
                               suppliers=suppliers, categories=categories,
                               start_date=start_date, end_date=end_date,
                               supplier_id=int(supplier_id), category_l1=int(category_l1),
                               category_l2=int(category_l2), category_l3=int(category_l3),
                               summary_data=summary_data, detail_data=[])
    else:
        detail_query = db.session.query(
            StockIn.stock_in_date,
            StockIn.code,
            StockIn.id.label('stock_in_id'),
            StockIn.is_reconciled,
            Material.name,
            Material.specification,
            Material.unit,
            Material.id.label('material_id'),
            StockInItem.quantity,
            StockInItem.unit_price,
            StockInItem.amount,
            StockInItem.contract_item_id,
            Supplier.name.label('supplier_name')
        ).join(StockInItem, StockInItem.stock_in_id == StockIn.id).join(
            Material, Material.id == StockInItem.material_id
        ).outerjoin(Supplier, Supplier.id == StockIn.supplier_id).filter(
            StockIn.id.in_(stock_in_ids)
        )
        if category_ids:
            detail_query = detail_query.filter(Material.category_id.in_(category_ids))

        raw_rows = detail_query.order_by(StockIn.stock_in_date.desc()).all()

        # 计算每行含税/不含税金额与价格状态标签
        detail_data = []
        for row in raw_rows:
            tax_rate = ci_tax_map.get(row.contract_item_id, 13) if row.contract_item_id else 13
            if row.is_reconciled and (row.stock_in_id, row.material_id) in reconcil_map:
                # 已对账确认的入库按实际结算单价计算
                settlement_price = reconcil_map.get((row.stock_in_id, row.material_id), 0)
                amount_with_tax = settlement_price * float(row.quantity or 0)
                price_label = '实际'
            else:
                # 未对账入库按暂估金额
                amount_with_tax = float(row.amount or 0)
                price_label = '暂估'
            amount_without_tax = _calc_amount_without_tax(amount_with_tax, tax_rate)

            detail_data.append({
                'stock_in_date': row.stock_in_date,
                'code': row.code,
                'name': row.name,
                'specification': row.specification,
                'unit': row.unit,
                'quantity': float(row.quantity or 0),
                'unit_price': float(row.unit_price or 0),
                'amount': amount_with_tax,
                'amount_without_tax': amount_without_tax,
                'supplier_name': row.supplier_name,
                'price_label': price_label,
            })

        return render_template('report/stock_in.html', view_type=view_type,
                               suppliers=suppliers, categories=categories,
                               start_date=start_date, end_date=end_date,
                               supplier_id=int(supplier_id), category_l1=int(category_l1),
                               category_l2=int(category_l2), category_l3=int(category_l3),
                               summary_data=[], detail_data=detail_data)


@bp.route('/stock_out')
@login_required
def stock_out_report():
    """发料统计报表"""
    project_id = session.get('current_project_id')
    view_type = request.args.get('view', 'summary')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    usage_unit_id = request.args.get('usage_unit_id', '0')
    category_l1 = request.args.get('category_l1', '0')
    category_l2 = request.args.get('category_l2', '0')
    category_l3 = request.args.get('category_l3', '0')

    usage_units = UsageUnit.query.filter_by(project_id=project_id).all() if project_id else []
    categories = Category.query.filter_by(project_id=project_id).all() if project_id else []

    def _get_category_ids(l1, l2, l3):
        l1, l2, l3 = int(l1), int(l2), int(l3)
        if l3 > 0:
            return [l3]
        elif l2 > 0:
            return [c.id for c in Category.query.filter_by(parent_id=l2).all()]
        elif l1 > 0:
            ids = []
            for l2c in Category.query.filter_by(parent_id=l1).all():
                ids.append(l2c.id)
                ids.extend([c.id for c in Category.query.filter_by(parent_id=l2c.id).all()])
            return ids
        return []

    if not project_id:
        return render_template('report/stock_out.html', view_type=view_type,
                               usage_units=usage_units, categories=categories,
                               start_date=start_date, end_date=end_date,
                               usage_unit_id=int(usage_unit_id), category_l1=int(category_l1),
                               category_l2=int(category_l2), category_l3=int(category_l3),
                               summary_data=[], detail_data=[])

    query = StockOut.query.filter(StockOut.project_id == project_id)
    if start_date:
        query = query.filter(StockOut.stock_out_date >= start_date)
    if end_date:
        query = query.filter(StockOut.stock_out_date <= end_date)
    if usage_unit_id and int(usage_unit_id) > 0:
        query = query.filter(StockOut.usage_unit_id == int(usage_unit_id))

    stock_outs = query.order_by(StockOut.stock_out_date.desc()).all()
    stock_out_ids = [so.id for so in stock_outs]

    category_ids = _get_category_ids(category_l1, category_l2, category_l3)

    # 预构建库存不含税加权单价映射，用于计算出库成本
    inv_unit_cost_map = _build_inventory_unit_cost_map(project_id)

    def _calc_out_cost_without_tax(material_id, quantity):
        """根据库存不含税加权单价计算出库成本（不含税）"""
        unit_cost = inv_unit_cost_map.get(material_id, 0)
        return float(quantity or 0) * unit_cost

    if view_type == 'summary':
        # 按物资汇总出库数量、含税参考金额、不含税成本
        items_query = db.session.query(
            StockOutItem, Material, Category
        ).join(
            Material, StockOutItem.material_id == Material.id
        ).outerjoin(
            Category, Category.id == Material.category_id
        ).join(
            StockOut, StockOut.id == StockOutItem.stock_out_id
        ).filter(StockOut.id.in_(stock_out_ids))
        if category_ids:
            items_query = items_query.filter(Material.category_id.in_(category_ids))

        summary_dict = {}
        for item, mat, cat in items_query.all():
            key = mat.id
            cost_without_tax = _calc_out_cost_without_tax(mat.id, item.quantity)
            if key not in summary_dict:
                summary_dict[key] = {
                    'name': mat.name,
                    'specification': mat.specification,
                    'unit': mat.unit,
                    'category_name': cat.name if cat else None,
                    'total_qty': 0.0,
                    'total_amount': 0.0,
                    'total_cost_without_tax': 0.0,
                }
            summary_dict[key]['total_qty'] += float(item.quantity or 0)
            summary_dict[key]['total_amount'] += float(item.amount or 0)
            summary_dict[key]['total_cost_without_tax'] += cost_without_tax

        summary_data = sorted(summary_dict.values(),
                              key=lambda x: x['total_amount'], reverse=True)
        for d in summary_data:
            d['total_qty'] = round(d['total_qty'], 4)
            d['total_amount'] = round(d['total_amount'], 2)
            d['total_cost_without_tax'] = round(d['total_cost_without_tax'], 2)

        return render_template('report/stock_out.html', view_type=view_type,
                               usage_units=usage_units, categories=categories,
                               start_date=start_date, end_date=end_date,
                               usage_unit_id=int(usage_unit_id), category_l1=int(category_l1),
                               category_l2=int(category_l2), category_l3=int(category_l3),
                               summary_data=summary_data, detail_data=[])
    else:
        detail_query = db.session.query(
            StockOut.stock_out_date,
            StockOut.code,
            Material.name,
            Material.specification,
            Material.unit,
            Material.id.label('material_id'),
            StockOutItem.quantity,
            StockOutItem.unit_price,
            StockOutItem.amount,
            UsageUnit.name.label('usage_unit_name')
        ).join(StockOutItem, StockOutItem.stock_out_id == StockOut.id).join(
            Material, Material.id == StockOutItem.material_id
        ).outerjoin(UsageUnit, UsageUnit.id == StockOut.usage_unit_id).filter(
            StockOut.id.in_(stock_out_ids)
        )
        if category_ids:
            detail_query = detail_query.filter(Material.category_id.in_(category_ids))

        raw_rows = detail_query.order_by(StockOut.stock_out_date.desc()).all()

        # 计算每行不含税成本
        detail_data = []
        for row in raw_rows:
            cost_without_tax = _calc_out_cost_without_tax(row.material_id, row.quantity)
            detail_data.append({
                'stock_out_date': row.stock_out_date,
                'code': row.code,
                'name': row.name,
                'specification': row.specification,
                'unit': row.unit,
                'quantity': float(row.quantity or 0),
                'unit_price': float(row.unit_price or 0),
                'amount': float(row.amount or 0),
                'cost_without_tax': cost_without_tax,
                'usage_unit_name': row.usage_unit_name,
            })

        return render_template('report/stock_out.html', view_type=view_type,
                               usage_units=usage_units, categories=categories,
                               start_date=start_date, end_date=end_date,
                               usage_unit_id=int(usage_unit_id), category_l1=int(category_l1),
                               category_l2=int(category_l2), category_l3=int(category_l3),
                               summary_data=[], detail_data=detail_data)


@bp.route('/material_movement')
@login_required
def material_movement():
    """物资动态表 - 月度汇总视图（金额统一为不含税口径）"""
    project_id = session.get('current_project_id')

    if not project_id:
        return render_template('report/material_movement.html', months=[], view='summary',
                               unpriced_count=0)

    # 获取所有已结账期间
    from app.models import MovementSnapshot, PeriodClose
    closed_periods = {pc.period for pc in PeriodClose.query.filter_by(
        project_id=project_id, status='closed'
    ).all()}

    # 预获取所有已结账月份的快照汇总
    closed_snap_totals = {}
    if closed_periods:
        snap_rows = db.session.query(
            MovementSnapshot.period,
            func.coalesce(func.sum(MovementSnapshot.in_amount), 0).label('in_amount'),
            func.coalesce(func.sum(MovementSnapshot.out_amount), 0).label('out_amount'),
            func.coalesce(func.sum(MovementSnapshot.end_amount), 0).label('end_amount'),
        ).filter(
            MovementSnapshot.project_id == project_id,
            MovementSnapshot.period.in_(list(closed_periods))
        ).group_by(MovementSnapshot.period).all()
        for row in snap_rows:
            closed_snap_totals[row.period] = {
                'in_amount': float(row.in_amount or 0),
                'out_amount': float(row.out_amount or 0),
                'end_amount': float(row.end_amount or 0),
            }

    stock_in_min = db.session.query(func.coalesce(func.min(StockIn.stock_in_date), datetime.now().date())).filter(
        StockIn.project_id == project_id
    ).scalar()
    stock_out_min = db.session.query(func.coalesce(func.min(StockOut.stock_out_date), datetime.now().date())).filter(
        StockOut.project_id == project_id
    ).scalar()
    start_date = min(stock_in_min, stock_out_min)
    current_date = datetime.now().date()

    # 预构建合同明细税率映射
    contract_item_ids = set()
    for item in StockInItem.query.join(StockIn, StockInItem.stock_in_id == StockIn.id).filter(
        StockIn.project_id == project_id
    ).all():
        if item.contract_item_id:
            contract_item_ids.add(item.contract_item_id)
    ci_tax_map = _build_contract_item_tax_map(contract_item_ids)

    # 预构建库存不含税加权单价映射（基于已对账成本 actual_amount）
    inv_unit_cost_map = _build_inventory_unit_cost_map(project_id)

    # 一次性获取所有入库明细，按月聚合不含税金额
    in_rows = db.session.query(
        StockInItem.contract_item_id, StockInItem.amount, StockIn.stock_in_date
    ).join(StockIn, StockInItem.stock_in_id == StockIn.id).filter(
        StockIn.project_id == project_id
    ).all()
    in_by_month = {}
    for contract_item_id, amount, in_date in in_rows:
        if not in_date:
            continue
        mkey = in_date.strftime('%Y-%m')
        if mkey in closed_periods:
            continue
        tax_rate = ci_tax_map.get(contract_item_id, 13) if contract_item_id else 13
        amt_without_tax = _calc_amount_without_tax(float(amount or 0), tax_rate)
        in_by_month[mkey] = in_by_month.get(mkey, 0.0) + amt_without_tax

    # 一次性获取所有出库明细，按月聚合不含税成本
    out_rows = db.session.query(
        StockOutItem.quantity, StockOut.stock_out_date, StockOutItem.material_id
    ).join(StockOut, StockOutItem.stock_out_id == StockOut.id).filter(
        StockOut.project_id == project_id
    ).all()
    out_by_month = {}
    for qty, out_date, material_id in out_rows:
        if not out_date:
            continue
        mkey = out_date.strftime('%Y-%m')
        if mkey in closed_periods:
            continue
        unit_cost = inv_unit_cost_map.get(material_id, 0)
        cost = float(qty or 0) * unit_cost
        out_by_month[mkey] = out_by_month.get(mkey, 0.0) + cost

    # 统计未对账入库笔数（暂估入库）
    unpriced_count = StockIn.query.filter(
        StockIn.project_id == project_id,
        StockIn.is_reconciled == False
    ).count()

    months = []
    current_year, current_month = start_date.year, start_date.month
    end_year, end_month = current_date.year, current_date.month

    cumulative_in = 0.0
    cumulative_out = 0.0
    last_balance = 0.0

    while (current_year, current_month) <= (end_year, end_month):
        mkey = f"{current_year}-{current_month:02d}"
        if mkey in closed_snap_totals:
            snap = closed_snap_totals[mkey]
            month_in = snap['in_amount']
            month_out = snap['out_amount']
            this_balance = snap['end_amount']
        else:
            month_in = in_by_month.get(mkey, 0.0)
            month_out = out_by_month.get(mkey, 0.0)
            this_balance = last_balance + month_in - month_out
        cumulative_in += month_in
        cumulative_out += month_out

        months.append({
            'month': mkey,
            'month_display': mkey,
            'last_month_balance': round(last_balance, 2),
            'month_in_amount': round(month_in, 2),
            'cumulative_in_amount': round(cumulative_in, 2),
            'month_out_amount': round(month_out, 2),
            'cumulative_out_amount': round(cumulative_out, 2),
            'this_month_balance': round(this_balance, 2),
            'is_closed': mkey in closed_periods,
        })
        last_balance = this_balance

        if current_month == 12:
            current_year += 1
            current_month = 1
        else:
            current_month += 1

    months.sort(key=lambda x: x['month'], reverse=True)

    return render_template('report/material_movement.html', months=months, view='summary',
                           unpriced_count=unpriced_count)


@bp.route('/material_movement/detail')
@login_required
def material_movement_detail():
    """物资动态表 - 月度明细视图（金额统一为不含税口径）"""
    project_id = session.get('current_project_id')
    month_str = request.args.get('month', datetime.now().strftime('%Y-%m'))

    if not project_id:
        return render_template('report/material_movement.html', month=month_str, movements=[],
                               view='detail', unpriced_count=0)

    try:
        year, month = map(int, month_str.split('-'))
    except ValueError:
        year, month = datetime.now().year, datetime.now().month

    # 检查是否已结账，已结账优先读快照
    from app.models import MovementSnapshot, PeriodClose
    is_closed = PeriodClose.query.filter_by(
        project_id=project_id, period=month_str, status='closed'
    ).first() is not None

    if is_closed:
        snaps = MovementSnapshot.query.filter_by(
            project_id=project_id, period=month_str
        ).all()
        snap_map = {s.material_id: s for s in snaps}
        materials = Material.query.filter_by(project_id=project_id).all()
        movements = []
        for mat in materials:
            snap = snap_map.get(mat.id)
            if snap:
                movements.append({
                    'material': mat,
                    'last_month_balance_qty': float(snap.begin_qty),
                    'last_month_balance_amt': float(snap.begin_amount),
                    'period_in_qty': float(snap.in_qty),
                    'period_in_amt': float(snap.in_amount),
                    'period_out_qty': float(snap.out_qty),
                    'period_out_amt': float(snap.out_amount),
                    'this_month_balance_qty': float(snap.end_qty),
                    'this_month_balance_amt': float(snap.end_amount),
                })
            else:
                movements.append({
                    'material': mat,
                    'last_month_balance_qty': 0,
                    'last_month_balance_amt': 0,
                    'period_in_qty': 0,
                    'period_in_amt': 0,
                    'period_out_qty': 0,
                    'period_out_amt': 0,
                    'this_month_balance_qty': 0,
                    'this_month_balance_amt': 0,
                })
        unpriced_count = 0
        return render_template('report/material_movement.html', month=month_str, movements=movements,
                               view='detail', unpriced_count=unpriced_count, is_closed=is_closed)

    from calendar import monthrange
    _, last_day = monthrange(year, month)
    start_of_month = datetime(year, month, 1).date()
    end_of_month = datetime(year, month, last_day).date()

    materials = Material.query.filter_by(project_id=project_id).all()

    # 预构建合同明细税率映射
    contract_item_ids = set()
    for item in StockInItem.query.join(StockIn, StockInItem.stock_in_id == StockIn.id).filter(
        StockIn.project_id == project_id
    ).all():
        if item.contract_item_id:
            contract_item_ids.add(item.contract_item_id)
    ci_tax_map = _build_contract_item_tax_map(contract_item_ids)

    # 预构建库存不含税加权单价映射
    inv_unit_cost_map = _build_inventory_unit_cost_map(project_id)

    # 统计未对账入库笔数
    unpriced_count = StockIn.query.filter(
        StockIn.project_id == project_id,
        StockIn.is_reconciled == False
    ).count()

    movements = []
    for mat in materials:
        # 上月结存 - 数量
        begin_in_qty = db.session.query(func.coalesce(func.sum(StockInItem.quantity), 0)).join(
            StockIn, StockIn.id == StockInItem.stock_in_id
        ).filter(
            StockInItem.material_id == mat.id,
            StockIn.stock_in_date < start_of_month
        ).scalar() or 0

        begin_out_qty = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
            StockOut, StockOut.id == StockOutItem.stock_out_id
        ).filter(
            StockOutItem.material_id == mat.id,
            StockOut.stock_out_date < start_of_month
        ).scalar() or 0

        # 上月结存 - 金额（不含税）
        begin_in_rows = db.session.query(
            StockInItem.contract_item_id, StockInItem.amount
        ).join(StockIn, StockIn.id == StockInItem.stock_in_id).filter(
            StockInItem.material_id == mat.id,
            StockIn.stock_in_date < start_of_month
        ).all()
        begin_in_amt = 0.0
        for contract_item_id, amount in begin_in_rows:
            tax_rate = ci_tax_map.get(contract_item_id, 13) if contract_item_id else 13
            begin_in_amt += _calc_amount_without_tax(float(amount or 0), tax_rate)

        begin_out_rows = db.session.query(
            StockOutItem.quantity, StockOutItem.material_id
        ).join(StockOut, StockOut.id == StockOutItem.stock_out_id).filter(
            StockOutItem.material_id == mat.id,
            StockOut.stock_out_date < start_of_month
        ).all()
        begin_out_amt = 0.0
        for qty, mid in begin_out_rows:
            unit_cost = inv_unit_cost_map.get(mid, 0)
            begin_out_amt += float(qty or 0) * unit_cost

        last_month_balance_qty = float(begin_in_qty) - float(begin_out_qty)
        last_month_balance_amt = begin_in_amt - begin_out_amt

        # 本期入库 - 数量与金额（不含税）
        period_in_qty = db.session.query(func.coalesce(func.sum(StockInItem.quantity), 0)).join(
            StockIn, StockIn.id == StockInItem.stock_in_id
        ).filter(
            StockInItem.material_id == mat.id,
            StockIn.stock_in_date >= start_of_month,
            StockIn.stock_in_date <= end_of_month
        ).scalar() or 0

        period_in_rows = db.session.query(
            StockInItem.contract_item_id, StockInItem.amount
        ).join(StockIn, StockIn.id == StockInItem.stock_in_id).filter(
            StockInItem.material_id == mat.id,
            StockIn.stock_in_date >= start_of_month,
            StockIn.stock_in_date <= end_of_month
        ).all()
        period_in_amt = 0.0
        for contract_item_id, amount in period_in_rows:
            tax_rate = ci_tax_map.get(contract_item_id, 13) if contract_item_id else 13
            period_in_amt += _calc_amount_without_tax(float(amount or 0), tax_rate)

        # 本期出库 - 数量与金额（不含税成本）
        period_out_qty = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
            StockOut, StockOut.id == StockOutItem.stock_out_id
        ).filter(
            StockOutItem.material_id == mat.id,
            StockOut.stock_out_date >= start_of_month,
            StockOut.stock_out_date <= end_of_month
        ).scalar() or 0

        period_out_rows = db.session.query(
            StockOutItem.quantity, StockOutItem.material_id
        ).join(StockOut, StockOut.id == StockOutItem.stock_out_id).filter(
            StockOutItem.material_id == mat.id,
            StockOut.stock_out_date >= start_of_month,
            StockOut.stock_out_date <= end_of_month
        ).all()
        period_out_amt = 0.0
        for qty, mid in period_out_rows:
            unit_cost = inv_unit_cost_map.get(mid, 0)
            period_out_amt += float(qty or 0) * unit_cost

        # 本月结存 - 数量与金额
        this_month_balance_qty = last_month_balance_qty + float(period_in_qty) - float(period_out_qty)
        this_month_balance_amt = last_month_balance_amt + period_in_amt - period_out_amt

        movements.append({
            'material': mat,
            'last_month_balance_qty': last_month_balance_qty,
            'last_month_balance_amt': round(last_month_balance_amt, 2),
            'period_in_qty': float(period_in_qty),
            'period_in_amt': round(period_in_amt, 2),
            'period_out_qty': float(period_out_qty),
            'period_out_amt': round(period_out_amt, 2),
            'this_month_balance_qty': this_month_balance_qty,
            'this_month_balance_amt': round(this_month_balance_amt, 2)
        })

    return render_template('report/material_movement.html', month=month_str, movements=movements,
                           view='detail', unpriced_count=unpriced_count)


@bp.route('/stock_in/export')
@login_required
def export_stock_in():
    """导出入库统计Excel"""
    project_id = session.get('current_project_id')
    view_type = request.args.get('view', 'summary')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    supplier_id = request.args.get('supplier_id', '0')
    category_l1 = request.args.get('category_l1', '0')
    category_l2 = request.args.get('category_l2', '0')
    category_l3 = request.args.get('category_l3', '0')

    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('report.stock_in_report'))

    query = StockIn.query.filter(StockIn.project_id == project_id)
    if start_date:
        query = query.filter(StockIn.stock_in_date >= start_date)
    if end_date:
        query = query.filter(StockIn.stock_in_date <= end_date)
    if supplier_id and int(supplier_id) > 0:
        query = query.filter(StockIn.supplier_id == int(supplier_id))

    stock_ins = query.all()
    stock_in_ids = [si.id for si in stock_ins] if stock_ins else [0]

    def _get_category_ids(l1, l2, l3):
        l1, l2, l3 = int(l1), int(l2), int(l3)
        if l3 > 0:
            return [l3]
        elif l2 > 0:
            return [c.id for c in Category.query.filter_by(parent_id=l2).all()]
        elif l1 > 0:
            ids = []
            for l2c in Category.query.filter_by(parent_id=l1).all():
                ids.append(l2c.id)
                ids.extend([c.id for c in Category.query.filter_by(parent_id=l2c.id).all()])
            return ids
        return []

    category_ids = _get_category_ids(category_l1, category_l2, category_l3)
    date_str = datetime.now().strftime('%Y%m%d')

    if view_type == 'summary':
        headers = ['物资名称', '规格型号', '分类', '单位', '入库数量', '入库金额']
        rows = []
        summary_query = db.session.query(
            Material.name, Material.specification, Material.unit,
            Category.name.label('category_name'),
            func.coalesce(func.sum(StockInItem.quantity), 0).label('total_qty'),
            func.coalesce(func.sum(StockInItem.amount), 0).label('total_amount')
        ).join(StockInItem, StockInItem.material_id == Material.id).join(
            StockIn, StockIn.id == StockInItem.stock_in_id
        ).outerjoin(Category, Category.id == Material.category_id).filter(
            StockIn.id.in_(stock_in_ids)
        )
        if category_ids:
            summary_query = summary_query.filter(Material.category_id.in_(category_ids))

        summary_data = summary_query.group_by(
            Material.id, Material.name, Material.specification, Material.unit, Category.name
        ).order_by(func.sum(StockInItem.amount).desc()).all()

        for row in summary_data:
            rows.append([
                row.name, row.specification or '', row.category_name or '', row.unit,
                float(row.total_qty or 0), float(row.total_amount or 0)
            ])

        filename = f'入库统计汇总_{date_str}.xlsx'
        data = export_to_excel(headers, rows, '入库统计汇总', filename)
    else:
        headers = ['日期', '单据编号', '物资名称', '规格', '单位', '数量', '单价', '金额', '供应商']
        rows = []
        detail_query = db.session.query(
            StockIn.stock_in_date, StockIn.code, Material.name, Material.specification,
            Material.unit, StockInItem.quantity, StockInItem.unit_price, StockInItem.amount,
            Supplier.name.label('supplier_name')
        ).join(StockInItem, StockInItem.stock_in_id == StockIn.id).join(
            Material, Material.id == StockInItem.material_id
        ).outerjoin(Supplier, Supplier.id == StockIn.supplier_id).filter(
            StockIn.id.in_(stock_in_ids)
        )
        if category_ids:
            detail_query = detail_query.filter(Material.category_id.in_(category_ids))

        detail_data = detail_query.order_by(StockIn.stock_in_date.desc()).all()

        for row in detail_data:
            rows.append([
                str(row.stock_in_date or ''), row.code, row.name, row.specification or '',
                row.unit, float(row.quantity or 0), float(row.unit_price or 0),
                float(row.amount or 0), row.supplier_name or ''
            ])

        filename = f'入库统计明细_{date_str}.xlsx'
        data = export_to_excel(headers, rows, '入库统计报表', filename)
        return _make_export_response(data, filename)


@bp.route('/stock_out/export')
@login_required
def export_stock_out():
    """导出发料统计Excel"""
    project_id = session.get('current_project_id')
    view_type = request.args.get('view', 'summary')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    usage_unit_id = request.args.get('usage_unit_id', '0')
    category_l1 = request.args.get('category_l1', '0')
    category_l2 = request.args.get('category_l2', '0')
    category_l3 = request.args.get('category_l3', '0')

    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('report.stock_out_report'))

    query = StockOut.query.filter(StockOut.project_id == project_id)
    if start_date:
        query = query.filter(StockOut.stock_out_date >= start_date)
    if end_date:
        query = query.filter(StockOut.stock_out_date <= end_date)
    if usage_unit_id and int(usage_unit_id) > 0:
        query = query.filter(StockOut.usage_unit_id == int(usage_unit_id))

    stock_outs = query.all()
    stock_out_ids = [so.id for so in stock_outs] if stock_outs else [0]

    def _get_category_ids(l1, l2, l3):
        l1, l2, l3 = int(l1), int(l2), int(l3)
        if l3 > 0:
            return [l3]
        elif l2 > 0:
            return [c.id for c in Category.query.filter_by(parent_id=l2).all()]
        elif l1 > 0:
            ids = []
            for l2c in Category.query.filter_by(parent_id=l1).all():
                ids.append(l2c.id)
                ids.extend([c.id for c in Category.query.filter_by(parent_id=l2c.id).all()])
            return ids
        return []

    category_ids = _get_category_ids(category_l1, category_l2, category_l3)
    date_str = datetime.now().strftime('%Y%m%d')

    if view_type == 'summary':
        headers = ['物资名称', '规格型号', '分类', '单位', '出库数量', '出库金额']
        rows = []
        summary_query = db.session.query(
            Material.name, Material.specification, Material.unit,
            Category.name.label('category_name'),
            func.coalesce(func.sum(StockOutItem.quantity), 0).label('total_qty'),
            func.coalesce(func.sum(StockOutItem.amount), 0).label('total_amount')
        ).join(StockOutItem, StockOutItem.material_id == Material.id).join(
            StockOut, StockOut.id == StockOutItem.stock_out_id
        ).outerjoin(Category, Category.id == Material.category_id).filter(
            StockOut.id.in_(stock_out_ids)
        )
        if category_ids:
            summary_query = summary_query.filter(Material.category_id.in_(category_ids))

        summary_data = summary_query.group_by(
            Material.id, Material.name, Material.specification, Material.unit, Category.name
        ).order_by(func.sum(StockOutItem.amount).desc()).all()

        for row in summary_data:
            rows.append([
                row.name, row.specification or '', row.category_name or '', row.unit,
                float(row.total_qty or 0), float(row.total_amount or 0)
            ])

        filename = f'出库统计汇总_{date_str}.xlsx'
        data = export_to_excel(headers, rows, '出库统计汇总', filename)
    else:
        headers = ['日期', '单据编号', '物资名称', '规格', '单位', '数量', '单价', '金额', '领料单位']
        rows = []
        detail_query = db.session.query(
            StockOut.stock_out_date, StockOut.code, Material.name, Material.specification,
            Material.unit, StockOutItem.quantity, StockOutItem.unit_price, StockOutItem.amount,
            UsageUnit.name.label('usage_unit_name')
        ).join(StockOutItem, StockOutItem.stock_out_id == StockOut.id).join(
            Material, Material.id == StockOutItem.material_id
        ).outerjoin(UsageUnit, UsageUnit.id == StockOut.usage_unit_id).filter(
            StockOut.id.in_(stock_out_ids)
        )
        if category_ids:
            detail_query = detail_query.filter(Material.category_id.in_(category_ids))

        detail_data = detail_query.order_by(StockOut.stock_out_date.desc()).all()

        for row in detail_data:
            rows.append([
                str(row.stock_out_date or ''), row.code, row.name, row.specification or '',
                row.unit, float(row.quantity or 0), float(row.unit_price or 0),
                float(row.amount or 0), row.usage_unit_name or ''
            ])

        filename = f'出库统计明细_{date_str}.xlsx'
    data = export_to_excel(headers, rows, '出库统计报表', filename)
    return _make_export_response(data, filename)


@bp.route('/material_movement/export')
@login_required
def export_material_movement():
    """导出物资动态表Excel"""
    project_id = session.get('current_project_id')
    view_type = request.args.get('view', 'summary')
    month_str = request.args.get('month', datetime.now().strftime('%Y-%m'))

    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('report.material_movement'))

    date_str = datetime.now().strftime('%Y%m%d')

    if view_type == 'summary':
        stock_in_min = db.session.query(func.coalesce(func.min(StockIn.stock_in_date), datetime.now().date())).filter(
            StockIn.project_id == project_id
        ).scalar()
        stock_out_min = db.session.query(func.coalesce(func.min(StockOut.stock_out_date), datetime.now().date())).filter(
            StockOut.project_id == project_id
        ).scalar()
        start_date = min(stock_in_min, stock_out_min)

        current_date = datetime.now().date()
        headers = ['序号', '账期', '上月结存(元)', '本期收料(元)', '开累收料(元)', '本期发料(元)', '开累发料(元)', '本月结存(元)']
        rows = []

        current_year, current_month = start_date.year, start_date.month
        end_year, end_month = current_date.year, current_date.month
        idx = 0

        while (current_year, current_month) <= (end_year, end_month):
            from calendar import monthrange
            _, last_day = monthrange(current_year, current_month)
            month_start = datetime(current_year, current_month, 1).date()
            month_end = datetime(current_year, current_month, last_day).date()

            # 上月结存 = 截至上月末累计收料 - 截至上月末累计发料
            cumulative_in_before = db.session.query(func.coalesce(func.sum(StockInItem.amount), 0)).join(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(
                StockIn.project_id == project_id,
                StockIn.stock_in_date < month_start
            ).scalar() or 0

            cumulative_out_before = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
                StockOut, StockOut.id == StockOutItem.stock_out_id
            ).filter(
                StockOut.project_id == project_id,
                StockOut.stock_out_date < month_start
            ).scalar() or 0

            last_month_balance = float(cumulative_in_before) - float(cumulative_out_before)

            month_in_amount = db.session.query(func.coalesce(func.sum(StockInItem.amount), 0)).join(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(
                StockIn.project_id == project_id,
                StockIn.stock_in_date >= month_start,
                StockIn.stock_in_date <= month_end
            ).scalar() or 0

            cumulative_in_amount = db.session.query(func.coalesce(func.sum(StockInItem.amount), 0)).join(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(
                StockIn.project_id == project_id,
                StockIn.stock_in_date <= month_end
            ).scalar() or 0

            month_out_amount = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
                StockOut, StockOut.id == StockOutItem.stock_out_id
            ).filter(
                StockOut.project_id == project_id,
                StockOut.stock_out_date >= month_start,
                StockOut.stock_out_date <= month_end
            ).scalar() or 0

            cumulative_out_amount = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
                StockOut, StockOut.id == StockOutItem.stock_out_id
            ).filter(
                StockOut.project_id == project_id,
                StockOut.stock_out_date <= month_end
            ).scalar() or 0

            # 本月结存 = 上月结存 + 本期收料 - 本期发料
            this_month_balance = last_month_balance + float(month_in_amount) - float(month_out_amount)

            idx += 1
            rows.append([
                idx,
                f"{current_year}-{current_month:02d}",
                last_month_balance,
                float(month_in_amount),
                float(cumulative_in_amount),
                float(month_out_amount),
                float(cumulative_out_amount),
                this_month_balance
            ])

            if current_month == 12:
                current_year += 1
                current_month = 1
            else:
                current_month += 1

        rows.reverse()
        # 反转后重新编排序号
        for i, row in enumerate(rows, 1):
            row[0] = i
        filename = f'物资动态表汇总_{date_str}.xlsx'
        data = export_to_excel(headers, rows, '物资动态表汇总', filename)
    else:
        try:
            year, month = map(int, month_str.split('-'))
        except ValueError:
            year, month = datetime.now().year, datetime.now().month

        from calendar import monthrange
        _, last_day = monthrange(year, month)
        start_of_month = datetime(year, month, 1).date()
        end_of_month = datetime(year, month, last_day).date()

        materials = Material.query.filter_by(project_id=project_id).all()
        headers = ['物资编码', '物资名称', '规格型号', '单位',
                   '上月结存数量', '上月结存金额(元)',
                   '本期入库数量', '本期入库金额(元)',
                   '本期出库数量', '本期出库金额(元)',
                   '本月结存数量', '本月结存金额(元)']
        rows = []

        for mat in materials:
            # 上月结存 - 数量与金额
            begin_in_qty = db.session.query(func.coalesce(func.sum(StockInItem.quantity), 0)).join(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(StockInItem.material_id == mat.id, StockIn.stock_in_date < start_of_month).scalar() or 0
            begin_out_qty = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
                StockOut, StockOut.id == StockOutItem.stock_out_id
            ).filter(StockOutItem.material_id == mat.id, StockOut.stock_out_date < start_of_month).scalar() or 0
            begin_in_amt = db.session.query(func.coalesce(func.sum(StockInItem.amount), 0)).join(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(StockInItem.material_id == mat.id, StockIn.stock_in_date < start_of_month).scalar() or 0
            begin_out_amt = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
                StockOut, StockOut.id == StockOutItem.stock_out_id
            ).filter(StockOutItem.material_id == mat.id, StockOut.stock_out_date < start_of_month).scalar() or 0

            last_month_balance_qty = float(begin_in_qty) - float(begin_out_qty)
            last_month_balance_amt = float(begin_in_amt) - float(begin_out_amt)

            # 本期入库 - 数量与金额
            period_in_qty = db.session.query(func.coalesce(func.sum(StockInItem.quantity), 0)).join(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(StockInItem.material_id == mat.id, StockIn.stock_in_date >= start_of_month,
                      StockIn.stock_in_date <= end_of_month).scalar() or 0
            period_in_amt = db.session.query(func.coalesce(func.sum(StockInItem.amount), 0)).join(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(StockInItem.material_id == mat.id, StockIn.stock_in_date >= start_of_month,
                      StockIn.stock_in_date <= end_of_month).scalar() or 0

            # 本期出库 - 数量与金额
            period_out_qty = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
                StockOut, StockOut.id == StockOutItem.stock_out_id
            ).filter(StockOutItem.material_id == mat.id, StockOut.stock_out_date >= start_of_month,
                      StockOut.stock_out_date <= end_of_month).scalar() or 0
            period_out_amt = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
                StockOut, StockOut.id == StockOutItem.stock_out_id
            ).filter(StockOutItem.material_id == mat.id, StockOut.stock_out_date >= start_of_month,
                      StockOut.stock_out_date <= end_of_month).scalar() or 0

            # 本月结存 - 数量与金额
            this_month_balance_qty = last_month_balance_qty + float(period_in_qty) - float(period_out_qty)
            this_month_balance_amt = last_month_balance_amt + float(period_in_amt) - float(period_out_amt)

            rows.append([
                mat.code or '', mat.name, mat.specification or '', mat.unit,
                last_month_balance_qty, last_month_balance_amt,
                float(period_in_qty), float(period_in_amt),
                float(period_out_qty), float(period_out_amt),
                this_month_balance_qty, this_month_balance_amt
            ])

        filename = f'物资动态表明细_{month_str}_{date_str}.xlsx'
        data = export_to_excel(headers, rows, f'物资动态表明细-{month_str}', filename)
    return _make_export_response(data, filename)


@bp.route('/advanced')
@login_required
def advanced():
    """高级分析汇总页"""
    return render_template('report/advanced.html')


@bp.route('/abc_analysis')
@login_required
def abc_analysis():
    """ABC分类分析"""
    project_id = session.get('current_project_id')
    if not project_id:
        return render_template('report/abc_analysis.html', abc_data=[], chart_data=[])

    end_date = datetime.now().date()
    start_date = (datetime.now() - timedelta(days=365)).date()

    results = db.session.query(
        Material.id,
        Material.name,
        Material.specification,
        Material.unit,
        func.coalesce(func.sum(StockInItem.amount), 0).label('total_amount')
    ).join(StockInItem, StockInItem.material_id == Material.id).join(
        StockIn, StockIn.id == StockInItem.stock_in_id
    ).filter(
        Material.project_id == project_id,
        StockIn.stock_in_date >= start_date,
        StockIn.stock_in_date <= end_date
    ).group_by(Material.id, Material.name, Material.specification, Material.unit).order_by(
        func.sum(StockInItem.amount).desc()
    ).all()

    total_value = sum(float(r.total_amount) for r in results)
    abc_data = []
    cumulative = 0

    for r in results:
        cumulative += float(r.total_amount)
        pct = (float(r.total_amount) / total_value * 100) if total_value else 0
        cum_pct = (cumulative / total_value * 100) if total_value else 0

        if cum_pct <= 70:
            cls = 'A'
        elif cum_pct <= 90:
            cls = 'B'
        else:
            cls = 'C'

        abc_data.append({
            'material_id': r.id,
            'name': r.name,
            'specification': r.specification,
            'unit': r.unit,
            'amount': float(r.total_amount),
            'percentage': round(pct, 2),
            'cumulative_percentage': round(cum_pct, 2),
            'class': cls
        })

    chart_data = [
        {'name': 'A类', 'value': round(sum(d['amount'] for d in abc_data if d['class'] == 'A'), 2)},
        {'name': 'B类', 'value': round(sum(d['amount'] for d in abc_data if d['class'] == 'B'), 2)},
        {'name': 'C类', 'value': round(sum(d['amount'] for d in abc_data if d['class'] == 'C'), 2)},
    ]

    return render_template('report/abc_analysis.html', abc_data=abc_data, chart_data=chart_data)


@bp.route('/turnover_rate')
@login_required
def turnover_rate():
    """库存周转率分析"""
    project_id = session.get('current_project_id')
    month_str = request.args.get('month', datetime.now().strftime('%Y-%m'))

    if not project_id:
        return render_template('report/turnover_rate.html', month=month_str, turnover_data=[], summary={})

    try:
        year, month = map(int, month_str.split('-'))
    except ValueError:
        year, month = datetime.now().year, datetime.now().month

    from calendar import monthrange
    _, last_day = monthrange(year, month)
    start_of_month = datetime(year, month, 1).date()
    end_of_month = datetime(year, month, last_day).date()

    materials = Material.query.filter_by(project_id=project_id).all()
    turnover_data = []

    for mat in materials:
        begin_in = db.session.query(func.coalesce(func.sum(StockInItem.quantity), 0)).join(
            StockIn, StockIn.id == StockInItem.stock_in_id
        ).filter(
            StockInItem.material_id == mat.id,
            StockIn.stock_in_date < start_of_month
        ).scalar() or 0

        begin_out = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
            StockOut, StockOut.id == StockOutItem.stock_out_id
        ).filter(
            StockOutItem.material_id == mat.id,
            StockOut.stock_out_date < start_of_month
        ).scalar() or 0

        begin_qty = float(begin_in) - float(begin_out)

        period_in = db.session.query(func.coalesce(func.sum(StockInItem.quantity), 0)).join(
            StockIn, StockIn.id == StockInItem.stock_in_id
        ).filter(
            StockInItem.material_id == mat.id,
            StockIn.stock_in_date >= start_of_month,
            StockIn.stock_in_date <= end_of_month
        ).scalar() or 0

        period_out = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
            StockOut, StockOut.id == StockOutItem.stock_out_id
        ).filter(
            StockOutItem.material_id == mat.id,
            StockOut.stock_out_date >= start_of_month,
            StockOut.stock_out_date <= end_of_month
        ).scalar() or 0

        end_qty = begin_qty + float(period_in) - float(period_out)
        avg_inventory = (begin_qty + end_qty) / 2
        rate = float(period_out) / avg_inventory if avg_inventory > 0 else 0
        days = 30 / rate if rate > 0 else 0

        turnover_data.append({
            'material': mat,
            'begin_qty': begin_qty,
            'end_qty': end_qty,
            'avg_inventory': avg_inventory,
            'total_out_qty': float(period_out),
            'turnover_rate': round(rate, 2),
            'turnover_days': round(days, 1),
            'is_low': rate < 1.0 and avg_inventory > 0
        })

    turnover_data.sort(key=lambda x: x['turnover_rate'], reverse=True)

    total_mat = len(turnover_data)
    low_count = sum(1 for d in turnover_data if d['is_low'])
    avg_rate = sum(d['turnover_rate'] for d in turnover_data) / total_mat if total_mat else 0

    summary = {
        'total_materials': total_mat,
        'low_turnover_count': low_count,
        'avg_turnover_rate': round(avg_rate, 2)
    }

    return render_template('report/turnover_rate.html', month=month_str, turnover_data=turnover_data, summary=summary)


@bp.route('/price_trend')
@login_required
def price_trend():
    """采购价格趋势"""
    project_id = session.get('current_project_id')
    material_id = request.args.get('material_id', '0')

    materials = Material.query.filter_by(project_id=project_id).order_by(Material.name).all() if project_id else []

    chart_data = []
    selected_material = None

    if project_id and material_id and int(material_id) > 0:
        selected_material = Material.query.get(int(material_id))
        current = datetime.now()
        months = []
        for i in range(11, -1, -1):
            year = current.year
            month = current.month - i
            while month <= 0:
                month += 12
                year -= 1
            months.append((year, month))

        for year, month in months:
            from calendar import monthrange
            _, last_day = monthrange(year, month)
            m_start = datetime(year, month, 1).date()
            m_end = datetime(year, month, last_day).date()

            avg_price = db.session.query(func.avg(StockInItem.unit_price)).join(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(
                StockInItem.material_id == int(material_id),
                StockIn.project_id == project_id,
                StockIn.stock_in_date >= m_start,
                StockIn.stock_in_date <= m_end
            ).scalar()

            chart_data.append({
                'month': f"{year}-{month:02d}",
                'price': round(float(avg_price), 4) if avg_price else 0
            })

    return render_template('report/price_trend.html', materials=materials, material_id=int(material_id),
                           selected_material=selected_material, chart_data=chart_data)


@bp.route('/cost_composition')
@login_required
def cost_composition():
    """项目成本构成"""
    project_id = session.get('current_project_id')
    if not project_id:
        return render_template('report/cost_composition.html', pie_data=[], trend_data=[])

    end_date = datetime.now().date()
    start_date = (datetime.now() - timedelta(days=365)).date()

    categories = Category.query.filter_by(project_id=project_id).all()
    cat_map = {c.id: c for c in categories}

    def get_l1_id(cat_id):
        if cat_id not in cat_map:
            return None
        cat = cat_map[cat_id]
        if cat.level == 1:
            return cat.id
        elif cat.level == 2:
            return cat.parent_id
        elif cat.level == 3:
            parent = cat_map.get(cat.parent_id)
            return parent.parent_id if parent else None
        return None

    l1_cats = {c.id: c.name for c in categories if c.level == 1}

    results = db.session.query(
        Material.category_id,
        func.coalesce(func.sum(StockInItem.amount), 0).label('total_amount')
    ).join(StockInItem, StockInItem.material_id == Material.id).join(
        StockIn, StockIn.id == StockInItem.stock_in_id
    ).filter(
        Material.project_id == project_id,
        StockIn.stock_in_date >= start_date,
        StockIn.stock_in_date <= end_date
    ).group_by(Material.category_id).all()

    composition = {}
    for r in results:
        l1_id = get_l1_id(r.category_id)
        if l1_id:
            composition[l1_id] = composition.get(l1_id, 0) + float(r.total_amount)

    pie_data = []
    for l1_id, amount in composition.items():
        pie_data.append({
            'name': l1_cats.get(l1_id, '未知分类'),
            'value': round(amount, 2)
        })
    pie_data.sort(key=lambda x: x['value'], reverse=True)

    current = datetime.now()
    months = []
    for i in range(11, -1, -1):
        year = current.year
        month = current.month - i
        while month <= 0:
            month += 12
            year -= 1
        months.append((year, month))

    trend_data = []
    for year, month in months:
        from calendar import monthrange
        _, last_day = monthrange(year, month)
        m_start = datetime(year, month, 1).date()
        m_end = datetime(year, month, last_day).date()

        total = db.session.query(func.coalesce(func.sum(StockInItem.amount), 0)).join(
            StockIn, StockIn.id == StockInItem.stock_in_id
        ).filter(
            StockIn.project_id == project_id,
            StockIn.stock_in_date >= m_start,
            StockIn.stock_in_date <= m_end
        ).scalar() or 0

        trend_data.append({
            'month': f"{year}-{month:02d}",
            'amount': round(float(total), 2)
        })

    return render_template('report/cost_composition.html', pie_data=pie_data, trend_data=trend_data)


@bp.route('/supplier_share')
@login_required
def supplier_share():
    """供应商采购占比"""
    project_id = session.get('current_project_id')
    if not project_id:
        return render_template('report/supplier_share.html', supplier_data=[], chart_data=[])

    end_date = datetime.now().date()
    start_date = (datetime.now() - timedelta(days=365)).date()

    results = db.session.query(
        Supplier.id,
        Supplier.name,
        func.coalesce(func.sum(StockInItem.amount), 0).label('total_amount')
    ).join(StockIn, StockIn.supplier_id == Supplier.id).join(
        StockInItem, StockInItem.stock_in_id == StockIn.id
    ).filter(
        StockIn.project_id == project_id,
        StockIn.stock_in_date >= start_date,
        StockIn.stock_in_date <= end_date
    ).group_by(Supplier.id, Supplier.name).order_by(
        func.sum(StockInItem.amount).desc()
    ).limit(10).all()

    total_amount = sum(float(r.total_amount) for r in results)

    supplier_data = []
    chart_data = []
    for r in results:
        pct = (float(r.total_amount) / total_amount * 100) if total_amount else 0
        supplier_data.append({
            'id': r.id,
            'name': r.name,
            'amount': round(float(r.total_amount), 2),
            'percentage': round(pct, 2)
        })
        chart_data.append({
            'name': r.name,
            'value': round(float(r.total_amount), 2)
        })

    return render_template('report/supplier_share.html', supplier_data=supplier_data, chart_data=chart_data)


@bp.route('/quota_execution')
@login_required
def quota_execution():
    """限额执行情况报表"""
    project_id = session.get('current_project_id')
    if not project_id:
        return render_template('report/quota_execution.html', work_numbers=[], data=[], summary={})

    from app.models import MaterialQuota, WorkNumber, StockOut, StockOutItem
    from app.utils import ConfigCache

    enable_quota = ConfigCache.get('enable_quota_control') == 'true'
    work_number_id = request.args.get('work_number_id', '0', type=str)

    work_numbers = WorkNumber.query.filter_by(project_id=project_id).order_by(WorkNumber.code).all()

    query = MaterialQuota.query.filter_by(project_id=project_id)
    if work_number_id and int(work_number_id) > 0:
        query = query.filter_by(work_number_id=int(work_number_id))

    quotas = query.order_by(MaterialQuota.work_number_id, MaterialQuota.id).all()

    data = []
    total_quota_qty = 0
    total_used_qty = 0
    total_quota_amt = 0
    total_used_amt = 0
    over_count = 0
    warning_count = 0

    warning_ratio = float(ConfigCache.get('quota_warning_ratio', '80'))

    for q in quotas:
        used_qty = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
            StockOut, StockOutItem.stock_out_id == StockOut.id
        ).filter(
            StockOut.project_id == project_id,
            StockOut.work_number_id == q.work_number_id,
            StockOutItem.material_id == q.material_id,
            StockOut.approval_status == 'passed'
        ).scalar() or 0
        used_qty = float(used_qty)

        used_amt = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
            StockOut, StockOutItem.stock_out_id == StockOut.id
        ).filter(
            StockOut.project_id == project_id,
            StockOut.work_number_id == q.work_number_id,
            StockOutItem.material_id == q.material_id,
            StockOut.approval_status == 'passed'
        ).scalar() or 0
        used_amt = float(used_amt)

        quota_qty = float(q.quota_quantity or 0)
        quota_amt = float(q.quota_amount or 0)

        qty_ratio = (used_qty / quota_qty * 100) if quota_qty > 0 else 0
        amt_ratio = (used_amt / quota_amt * 100) if quota_amt > 0 else 0

        status = 'normal'
        if q.quota_type in ('quantity', 'both') and quota_qty > 0:
            if qty_ratio >= 100:
                status = 'over'
            elif qty_ratio >= warning_ratio:
                status = 'warning'
        if q.quota_type in ('amount', 'both') and quota_amt > 0:
            if amt_ratio >= 100:
                status = 'over'
            elif amt_ratio >= warning_ratio and status == 'normal':
                status = 'warning'

        if status == 'over':
            over_count += 1
        elif status == 'warning':
            warning_count += 1

        total_quota_qty += quota_qty
        total_used_qty += used_qty
        total_quota_amt += quota_amt
        total_used_amt += used_amt

        wn = WorkNumber.query.get(q.work_number_id)
        data.append({
            'id': q.id,
            'work_number_code': wn.code if wn else '',
            'work_number_name': wn.division_name if wn else '',
            'material_id': q.material_id,
            'material_code': q.material.code if q.material else '',
            'material_name': q.material.name if q.material else '',
            'specification': q.material.specification if q.material else '',
            'unit': q.material.unit if q.material else '',
            'quota_type': q.quota_type,
            'quota_quantity': quota_qty,
            'used_quantity': round(used_qty, 4),
            'remaining_quantity': round(quota_qty - used_qty, 4),
            'qty_ratio': round(qty_ratio, 2),
            'quota_amount': quota_amt,
            'used_amount': round(used_amt, 2),
            'remaining_amount': round(quota_amt - used_amt, 2),
            'amt_ratio': round(amt_ratio, 2),
            'status': status,
        })

    overall_qty_ratio = (total_used_qty / total_quota_qty * 100) if total_quota_qty > 0 else 0
    overall_amt_ratio = (total_used_amt / total_quota_amt * 100) if total_quota_amt > 0 else 0

    summary = {
        'total_quotas': len(data),
        'over_count': over_count,
        'warning_count': warning_count,
        'total_quota_qty': round(total_quota_qty, 4),
        'total_used_qty': round(total_used_qty, 4),
        'overall_qty_ratio': round(overall_qty_ratio, 2),
        'total_quota_amt': round(total_quota_amt, 2),
        'total_used_amt': round(total_used_amt, 2),
        'overall_amt_ratio': round(overall_amt_ratio, 2),
        'enable_quota': enable_quota,
    }

    return render_template('report/quota_execution.html', work_numbers=work_numbers,
                           data=data, summary=summary, work_number_id=work_number_id)


@bp.route('/quota_execution/export')
@login_required
def export_quota_execution():
    """导出现额执行报表Excel"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('report.quota_execution'))

    from app.models import MaterialQuota, WorkNumber, StockOut, StockOutItem
    from app.utils import ConfigCache

    work_number_id = request.args.get('work_number_id', '0', type=str)

    query = MaterialQuota.query.filter_by(project_id=project_id)
    if work_number_id and int(work_number_id) > 0:
        query = query.filter_by(work_number_id=int(work_number_id))

    quotas = query.order_by(MaterialQuota.work_number_id, MaterialQuota.id).all()

    date_str = datetime.now().strftime('%Y%m%d')
    headers = ['工号编码', '分部工程', '物资编码', '物资名称', '规格型号', '单位',
               '限额类型', '数量限额', '已领数量', '剩余数量', '数量执行率%',
               '金额限额(元)', '已领金额(元)', '剩余金额(元)', '金额执行率%', '状态']
    rows = []

    warning_ratio = float(ConfigCache.get('quota_warning_ratio', '80'))

    for q in quotas:
        used_qty = db.session.query(func.coalesce(func.sum(StockOutItem.quantity), 0)).join(
            StockOut, StockOutItem.stock_out_id == StockOut.id
        ).filter(
            StockOut.project_id == project_id,
            StockOut.work_number_id == q.work_number_id,
            StockOutItem.material_id == q.material_id,
            StockOut.approval_status == 'passed'
        ).scalar() or 0
        used_qty = float(used_qty)

        used_amt = db.session.query(func.coalesce(func.sum(StockOutItem.amount), 0)).join(
            StockOut, StockOutItem.stock_out_id == StockOut.id
        ).filter(
            StockOut.project_id == project_id,
            StockOut.work_number_id == q.work_number_id,
            StockOutItem.material_id == q.material_id,
            StockOut.approval_status == 'passed'
        ).scalar() or 0
        used_amt = float(used_amt)

        quota_qty = float(q.quota_quantity or 0)
        quota_amt = float(q.quota_amount or 0)

        qty_ratio = (used_qty / quota_qty * 100) if quota_qty > 0 else 0
        amt_ratio = (used_amt / quota_amt * 100) if quota_amt > 0 else 0

        status_text = '正常'
        if q.quota_type in ('quantity', 'both') and quota_qty > 0:
            if qty_ratio >= 100:
                status_text = '超领'
            elif qty_ratio >= warning_ratio:
                status_text = '预警'
        if q.quota_type in ('amount', 'both') and quota_amt > 0:
            if amt_ratio >= 100:
                status_text = '超领'
            elif amt_ratio >= warning_ratio and status_text == '正常':
                status_text = '预警'

        wn = WorkNumber.query.get(q.work_number_id)
        type_text = {'quantity': '数量限额', 'amount': '金额限额', 'both': '双限额'}.get(q.quota_type, q.quota_type)

        rows.append([
            wn.code if wn else '',
            wn.division_name if wn else '',
            q.material.code if q.material else '',
            q.material.name if q.material else '',
            q.material.specification if q.material else '',
            q.material.unit if q.material else '',
            type_text,
            quota_qty,
            round(used_qty, 4),
            round(quota_qty - used_qty, 4),
            round(qty_ratio, 2),
            quota_amt,
            round(used_amt, 2),
            round(quota_amt - used_amt, 2),
            round(amt_ratio, 2),
            status_text,
        ])

    filename = f'物资动态表_{date_str}.xlsx'
    data = export_to_excel(headers, rows, '物资动态表', filename)
    return _make_export_response(data, filename)


def _parse_month_range(start_month, end_month):
    """解析 YYYY-MM 月份字符串，返回 (start_date, end_date)
    start_date 为 start_month 的第一天
    end_date 为 end_month 下一个月的第一天（开区间）"""
    start_date = datetime.strptime(start_month + '-01', '%Y-%m-%d').date()
    end_year, end_m = map(int, end_month.split('-'))
    if end_m == 12:
        end_date = datetime(end_year + 1, 1, 1).date()
    else:
        end_date = datetime(end_year, end_m + 1, 1).date()
    return start_date, end_date


def _build_category_l1_map(project_id):
    """构建分类映射: {category_id: l1_category_obj}"""
    categories = Category.query.filter_by(project_id=project_id).all()
    cat_map = {c.id: c for c in categories}
    l1_cats = {c.id: c for c in categories if c.level == 1}

    def get_l1(cat_id):
        if not cat_id or cat_id not in cat_map:
            return None
        cat = cat_map[cat_id]
        if cat.level == 1:
            return cat
        if cat.level == 2:
            return cat_map.get(cat.parent_id)
        if cat.level == 3:
            parent = cat_map.get(cat.parent_id)
            return cat_map.get(parent.parent_id) if parent else None
        return None
    return l1_cats, get_l1


def _query_receive_issue_data(project_id, start_date, end_date):
    """查询收发存汇总数据，返回 (groups, totals)
    groups: [{'l1_category': Category, 'materials': [material_row_dict], 'subtotals': dict}]
    totals: 各列总计 dict"""
    # 期初入库
    opening_in_rows = db.session.query(
        StockInItem.material_id.label('mid'),
        func.coalesce(func.sum(StockInItem.quantity), 0).label('qty'),
        func.coalesce(func.sum(StockInItem.amount), 0).label('amt')
    ).join(StockIn).filter(
        StockIn.project_id == project_id,
        StockIn.stock_in_date < start_date,
        StockIn.approval_status == 'passed'
    ).group_by(StockInItem.material_id).all()
    opening_in_map = {r.mid: (float(r.qty), float(r.amt)) for r in opening_in_rows}

    # 期初出库
    opening_out_rows = db.session.query(
        StockOutItem.material_id.label('mid'),
        func.coalesce(func.sum(StockOutItem.quantity), 0).label('qty'),
        func.coalesce(func.sum(StockOutItem.amount), 0).label('amt')
    ).join(StockOut).filter(
        StockOut.project_id == project_id,
        StockOut.stock_out_date < start_date,
        StockOut.approval_status == 'passed'
    ).group_by(StockOutItem.material_id).all()
    opening_out_map = {r.mid: (float(r.qty), float(r.amt)) for r in opening_out_rows}

    # 本期入库
    period_in_rows = db.session.query(
        StockInItem.material_id.label('mid'),
        func.coalesce(func.sum(StockInItem.quantity), 0).label('qty'),
        func.coalesce(func.sum(StockInItem.amount), 0).label('amt')
    ).join(StockIn).filter(
        StockIn.project_id == project_id,
        StockIn.stock_in_date >= start_date,
        StockIn.stock_in_date < end_date,
        StockIn.approval_status == 'passed'
    ).group_by(StockInItem.material_id).all()
    period_in_map = {r.mid: (float(r.qty), float(r.amt)) for r in period_in_rows}

    # 本期出库
    period_out_rows = db.session.query(
        StockOutItem.material_id.label('mid'),
        func.coalesce(func.sum(StockOutItem.quantity), 0).label('qty'),
        func.coalesce(func.sum(StockOutItem.amount), 0).label('amt')
    ).join(StockOut).filter(
        StockOut.project_id == project_id,
        StockOut.stock_out_date >= start_date,
        StockOut.stock_out_date < end_date,
        StockOut.approval_status == 'passed'
    ).group_by(StockOutItem.material_id).all()
    period_out_map = {r.mid: (float(r.qty), float(r.amt)) for r in period_out_rows}

    # 仅返回有发生额或期初余额的物资
    affected_ids = set(opening_in_map.keys()) | set(opening_out_map.keys()) \
        | set(period_in_map.keys()) | set(period_out_map.keys())

    materials = db.session.query(
        Material.id, Material.code, Material.name,
        Material.specification, Material.unit, Material.category_id
    ).filter(
        Material.project_id == project_id,
        Material.id.in_(list(affected_ids)) if affected_ids else False
    ).order_by(Material.code).all()

    l1_cats, get_l1 = _build_category_l1_map(project_id)

    # 按一级分类分组
    grouped = {}
    for mat in materials:
        l1 = get_l1(mat.category_id)
        l1_id = l1.id if l1 else 0
        l1_name = l1.name if l1 else '未分类'

        o_in = opening_in_map.get(mat.id, (0.0, 0.0))
        o_out = opening_out_map.get(mat.id, (0.0, 0.0))
        p_in = period_in_map.get(mat.id, (0.0, 0.0))
        p_out = period_out_map.get(mat.id, (0.0, 0.0))

        opening_qty = o_in[0] - o_out[0]
        opening_amt = o_in[1] - o_out[1]
        period_in_qty, period_in_amt = p_in
        period_out_qty, period_out_amt = p_out
        ending_qty = opening_qty + period_in_qty - period_out_qty
        ending_amt = opening_amt + period_in_amt - period_out_amt

        row = {
            'material_id': mat.id,
            'code': mat.code or '',
            'name': mat.name,
            'specification': mat.specification or '',
            'unit': mat.unit or '',
            'opening_qty': round(opening_qty, 4),
            'opening_amt': round(opening_amt, 2),
            'period_in_qty': round(period_in_qty, 4),
            'period_in_amt': round(period_in_amt, 2),
            'period_out_qty': round(period_out_qty, 4),
            'period_out_amt': round(period_out_amt, 2),
            'ending_qty': round(ending_qty, 4),
            'ending_amt': round(ending_amt, 2),
        }
        if l1_id not in grouped:
            grouped[l1_id] = {
                'l1_id': l1_id,
                'l1_name': l1_name,
                'materials': [],
                'subtotals': {
                    'opening_qty': 0.0, 'opening_amt': 0.0,
                    'period_in_qty': 0.0, 'period_in_amt': 0.0,
                    'period_out_qty': 0.0, 'period_out_amt': 0.0,
                    'ending_qty': 0.0, 'ending_amt': 0.0,
                }
            }
        grouped[l1_id]['materials'].append(row)
        for k in grouped[l1_id]['subtotals']:
            grouped[l1_id]['subtotals'][k] += row[k]

    # 按 l1_id 排序，未分类放最后
    groups = sorted(grouped.values(), key=lambda g: (g['l1_id'] == 0, g['l1_id']))
    for g in groups:
        for k in g['subtotals']:
            g['subtotals'][k] = round(g['subtotals'][k], 2 if k.endswith('_amt') else 4)

    totals = {
        'opening_qty': 0.0, 'opening_amt': 0.0,
        'period_in_qty': 0.0, 'period_in_amt': 0.0,
        'period_out_qty': 0.0, 'period_out_amt': 0.0,
        'ending_qty': 0.0, 'ending_amt': 0.0,
    }
    for g in groups:
        for k in totals:
            totals[k] += g['subtotals'][k]
    for k in totals:
        totals[k] = round(totals[k], 2 if k.endswith('_amt') else 4)

    return groups, totals


@bp.route('/receive_issue')
@login_required
def receive_issue():
    """收发存汇总表"""
    project_id = session.get('current_project_id')
    now = datetime.now()
    default_month = now.strftime('%Y-%m')
    start_month = request.args.get('start_month', default_month)
    end_month = request.args.get('end_month', default_month)

    if not project_id:
        return render_template('report/receive_issue.html',
                               start_month=start_month, end_month=end_month,
                               groups=[], totals={})

    try:
        start_date, end_date = _parse_month_range(start_month, end_month)
    except ValueError:
        flash('月份格式错误，应为 YYYY-MM', 'danger')
        return redirect(url_for('report.receive_issue'))

    groups, totals = _query_receive_issue_data(project_id, start_date, end_date)

    return render_template('report/receive_issue.html',
                           start_month=start_month, end_month=end_month,
                           groups=groups, totals=totals)


@bp.route('/receive_issue/export')
@login_required
def export_receive_issue():
    """导出收发存汇总表Excel"""
    project_id = session.get('current_project_id')
    now = datetime.now()
    default_month = now.strftime('%Y-%m')
    start_month = request.args.get('start_month', default_month)
    end_month = request.args.get('end_month', default_month)

    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('report.receive_issue'))

    try:
        start_date, end_date = _parse_month_range(start_month, end_month)
    except ValueError:
        flash('月份格式错误，应为 YYYY-MM', 'danger')
        return redirect(url_for('report.receive_issue'))

    groups, totals = _query_receive_issue_data(project_id, start_date, end_date)

    headers = ['物资编码', '物资名称', '规格型号', '单位',
               '期初数量', '期初金额(元)',
               '本期收入数量', '本期收入金额(元)',
               '本期发出数量', '本期发出金额(元)',
               '期末结存数量', '期末结存金额(元)']
    rows = []
    for g in groups:
        # 分类小计行
        rows.append([f"【{g['l1_name']}】", '', '', '', '', '', '', '', '', '', '', ''])
        for m in g['materials']:
            rows.append([
                m['code'], m['name'], m['specification'], m['unit'],
                m['opening_qty'], m['opening_amt'],
                m['period_in_qty'], m['period_in_amt'],
                m['period_out_qty'], m['period_out_amt'],
                m['ending_qty'], m['ending_amt'],
            ])
        st = g['subtotals']
        rows.append([
            f'{g["l1_name"]} 小计', '', '', '',
            st['opening_qty'], st['opening_amt'],
            st['period_in_qty'], st['period_in_amt'],
            st['period_out_qty'], st['period_out_amt'],
            st['ending_qty'], st['ending_amt'],
        ])
    rows.append([
        '合计', '', '', '',
        totals['opening_qty'], totals['opening_amt'],
        totals['period_in_qty'], totals['period_in_amt'],
        totals['period_out_qty'], totals['period_out_amt'],
        totals['ending_qty'], totals['ending_amt'],
    ])

    date_str = datetime.now().strftime('%Y%m%d')
    filename = f'收发存汇总表_{start_month}_{end_month}_{date_str}.xlsx'
    data = export_to_excel(headers, rows, f'收发存汇总表({start_month}~{end_month})', filename)
    return _make_export_response(data, filename)


def _query_ledger_data(project_id, material_id, start_date, end_date):
    """查询物资分类明细账数据，返回 (material, opening, movements)
    opening: 期初结存 dict {qty, amt}
    movements: [{'date', 'code', 'type', 'summary', 'in_qty', 'in_amt', 'out_qty', 'out_amt', 'balance_qty', 'balance_amt'}]"""
    material = Material.query.filter_by(id=material_id, project_id=project_id).first()

    # 期初结存
    opening_in = db.session.query(
        func.coalesce(func.sum(StockInItem.quantity), 0).label('qty'),
        func.coalesce(func.sum(StockInItem.amount), 0).label('amt')
    ).join(StockIn).filter(
        StockIn.project_id == project_id,
        StockInItem.material_id == material_id,
        StockIn.stock_in_date < start_date,
        StockIn.approval_status == 'passed'
    ).one()
    opening_out = db.session.query(
        func.coalesce(func.sum(StockOutItem.quantity), 0).label('qty'),
        func.coalesce(func.sum(StockOutItem.amount), 0).label('amt')
    ).join(StockOut).filter(
        StockOut.project_id == project_id,
        StockOutItem.material_id == material_id,
        StockOut.stock_out_date < start_date,
        StockOut.approval_status == 'passed'
    ).one()
    opening = {
        'qty': float(opening_in.qty) - float(opening_out.qty),
        'amt': float(opening_in.amt) - float(opening_out.amt),
    }

    # 本期入库明细
    in_rows = db.session.query(
        StockIn.stock_in_date.label('date'),
        StockIn.code.label('code'),
        StockIn.stock_in_type.label('type'),
        StockIn.remark.label('remark'),
        StockInItem.quantity.label('qty'),
        StockInItem.unit_price.label('unit_price'),
        StockInItem.amount.label('amt'),
    ).join(StockIn, StockInItem.stock_in_id == StockIn.id).filter(
        StockIn.project_id == project_id,
        StockInItem.material_id == material_id,
        StockIn.stock_in_date >= start_date,
        StockIn.stock_in_date < end_date,
        StockIn.approval_status == 'passed'
    ).all()

    # 本期出库明细
    out_rows = db.session.query(
        StockOut.stock_out_date.label('date'),
        StockOut.code.label('code'),
        StockOut.stock_out_type.label('type'),
        StockOut.remark.label('remark'),
        StockOutItem.quantity.label('qty'),
        StockOutItem.unit_price.label('unit_price'),
        StockOutItem.amount.label('amt'),
    ).join(StockOut, StockOutItem.stock_out_id == StockOut.id).filter(
        StockOut.project_id == project_id,
        StockOutItem.material_id == material_id,
        StockOut.stock_out_date >= start_date,
        StockOut.stock_out_date < end_date,
        StockOut.approval_status == 'passed'
    ).all()

    # 合并并按日期排序
    combined = []
    for r in in_rows:
        combined.append({
            'date': r.date,
            'code': r.code,
            'type': r.type or '入库',
            'summary': r.remark or '',
            'in_qty': float(r.qty or 0),
            'in_amt': float(r.amt or 0),
            'out_qty': 0.0,
            'out_amt': 0.0,
            '_sort': r.date,
        })
    for r in out_rows:
        combined.append({
            'date': r.date,
            'code': r.code,
            'type': r.type or '出库',
            'summary': r.remark or '',
            'in_qty': 0.0,
            'in_amt': 0.0,
            'out_qty': float(r.qty or 0),
            'out_amt': float(r.amt or 0),
            '_sort': r.date,
        })
    combined.sort(key=lambda x: x['_sort'])

    # 计算累计结存
    balance_qty = opening['qty']
    balance_amt = opening['amt']
    movements = []
    for c in combined:
        balance_qty += c['in_qty'] - c['out_qty']
        balance_amt += c['in_amt'] - c['out_amt']
        movements.append({
            'date': c['date'],
            'code': c['code'],
            'type': c['type'],
            'summary': c['summary'],
            'in_qty': round(c['in_qty'], 4),
            'in_amt': round(c['in_amt'], 2),
            'out_qty': round(c['out_qty'], 4),
            'out_amt': round(c['out_amt'], 2),
            'balance_qty': round(balance_qty, 4),
            'balance_amt': round(balance_amt, 2),
        })

    opening['qty'] = round(opening['qty'], 4)
    opening['amt'] = round(opening['amt'], 2)
    return material, opening, movements


@bp.route('/ledger')
@login_required
def ledger():
    """物资分类明细账"""
    project_id = session.get('current_project_id')
    materials = Material.query.filter_by(project_id=project_id).order_by(Material.code).all() if project_id else []

    now = datetime.now()
    # 默认本月1日 ~ 下月1日
    first_day = now.replace(day=1).date()
    if now.month == 12:
        next_first = datetime(now.year + 1, 1, 1).date()
    else:
        next_first = datetime(now.year, now.month + 1, 1).date()

    material_id = request.args.get('material_id', '0')
    start_date_str = request.args.get('start_date', first_day.strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', '')

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        start_date = first_day
        start_date_str = start_date.strftime('%Y-%m-%d')

    # end_date 处理：留空表示到月末，转换为下月1日（开区间）
    if end_date_str:
        try:
            end_date_parsed = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            # 闭区间转开区间：加一天
            end_date = end_date_parsed + timedelta(days=1)
        except ValueError:
            end_date = next_first
    else:
        end_date = next_first

    material = None
    opening = {'qty': 0.0, 'amt': 0.0}
    movements = []

    if project_id and material_id and int(material_id) > 0:
        material, opening, movements = _query_ledger_data(
            project_id, int(material_id), start_date, end_date
        )

    return render_template('report/ledger.html',
                           materials=materials, material_id=int(material_id),
                           start_date=start_date_str, end_date=end_date_str,
                           selected_material=material,
                           opening=opening, movements=movements)


@bp.route('/ledger/export')
@login_required
def export_ledger():
    """导出物资分类明细账Excel"""
    project_id = session.get('current_project_id')
    material_id = request.args.get('material_id', '0')
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')

    if not project_id or not material_id or int(material_id) <= 0:
        flash('请选择项目与物资', 'warning')
        return redirect(url_for('report.ledger'))

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('开始日期格式错误', 'danger')
        return redirect(url_for('report.ledger', material_id=material_id,
                                start_date=start_date_str, end_date=end_date_str))

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() + timedelta(days=1)
        except ValueError:
            flash('结束日期格式错误', 'danger')
            return redirect(url_for('report.ledger', material_id=material_id,
                                    start_date=start_date_str, end_date=end_date_str))
    else:
        now = datetime.now()
        end_date = (datetime(now.year, now.month + 1, 1) if now.month < 12
                    else datetime(now.year + 1, 1, 1)).date()

    material, opening, movements = _query_ledger_data(
        project_id, int(material_id), start_date, end_date
    )

    if not material:
        flash('物资不存在', 'warning')
        return redirect(url_for('report.ledger'))

    headers = ['日期', '单据号', '类型', '摘要',
               '收入数量', '收入金额(元)',
               '发出数量', '发出金额(元)',
               '结存数量', '结存金额(元)']
    rows = []

    # 期初行
    rows.append([
        start_date.strftime('%Y-%m-%d'), '', '期初结存', '期初结存',
        '', '',
        '', '',
        round(opening['qty'], 4), round(opening['amt'], 2),
    ])
    for m in movements:
        rows.append([
            m['date'].strftime('%Y-%m-%d') if m['date'] else '',
            m['code'], m['type'], m['summary'],
            m['in_qty'] if m['in_qty'] else '',
            m['in_amt'] if m['in_amt'] else '',
            m['out_qty'] if m['out_qty'] else '',
            m['out_amt'] if m['out_amt'] else '',
            m['balance_qty'], m['balance_amt'],
        ])

    date_str = datetime.now().strftime('%Y%m%d')
    sheet_title = f"{material.name}({material.code or ''}) 明细账"
    filename = f'物资明细账_{material.code or material.id}_{date_str}.xlsx'
    data = export_to_excel(headers, rows, sheet_title, filename)
    return _make_export_response(data, filename)


@bp.route('/work_number_cost')
@login_required
def work_number_cost():
    """工号成本统计报表"""
    project_id = session.get('current_project_id')
    from app.models import WorkNumber, StockOut, StockOutItem, Material, Category, UsageUnit, MaterialQuota
    from datetime import datetime, timedelta

    time_range = request.args.get('time_range', 'month')
    now = datetime.now()
    
    if time_range == 'month':
        start_date = now.replace(day=1).date()
        end_date = (now + timedelta(days=32)).replace(day=1).date()
    elif time_range == 'last_month':
        last_month = now.month - 1 if now.month > 1 else 12
        last_year = now.year if now.month > 1 else now.year - 1
        start_date = datetime(last_year, last_month, 1).date()
        end_date = now.replace(day=1).date()
    elif time_range == 'quarter':
        quarter = (now.month - 1) // 3 + 1
        start_month = (quarter - 1) * 3 + 1
        start_date = datetime(now.year, start_month, 1).date()
        end_date = datetime(now.year, start_month + 3, 1).date() if start_month + 3 <= 12 else datetime(now.year + 1, 1, 1).date()
    elif time_range == 'year':
        start_date = datetime(now.year, 1, 1).date()
        end_date = datetime(now.year + 1, 1, 1).date()
    else:
        start_date_str = request.args.get('start_date', '')
        end_date_str = request.args.get('end_date', '')
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = now.replace(day=1).date()
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() + timedelta(days=1)
        except ValueError:
            end_date = (now + timedelta(days=32)).replace(day=1).date()

    usage_unit_id = int(request.args.get('usage_unit_id', '0'))
    category_l1 = int(request.args.get('category_l1', '0'))

    query = db.session.query(
        WorkNumber.id,
        WorkNumber.code,
        WorkNumber.division_name,
        WorkNumber.item_name,
        func.sum(StockOutItem.quantity).label('total_qty'),
        func.sum(StockOutItem.amount).label('total_cost'),
        func.count(StockOut.id).label('order_count')
    ).join(StockOut, StockOut.work_number_id == WorkNumber.id) \
     .join(StockOutItem, StockOutItem.stock_out_id == StockOut.id) \
     .filter(WorkNumber.project_id == project_id) \
     .filter(StockOut.approval_status == 'passed') \
     .filter(StockOut.stock_out_date >= start_date) \
     .filter(StockOut.stock_out_date < end_date)

    if usage_unit_id > 0:
        query = query.filter(StockOut.usage_unit_id == usage_unit_id)
    if category_l1 > 0:
        query = query.join(Material, Material.id == StockOutItem.material_id) \
                     .join(Category, Category.id == Material.category_id) \
                     .filter(Category.id == category_l1)

    query = query.group_by(WorkNumber.id)
    work_number_data = query.all()

    total_cost = sum(float(item.total_cost or 0) for item in work_number_data)

    result = []
    category_chart_data = {}

    for item in work_number_data:
        mat_details = db.session.query(
            Material.name,
            func.sum(StockOutItem.quantity).label('qty'),
            func.avg(StockOutItem.unit_price).label('unit_price'),
            func.sum(StockOutItem.amount).label('amount')
        ).join(StockOutItem, StockOutItem.material_id == Material.id) \
         .join(StockOut, StockOut.id == StockOutItem.stock_out_id) \
         .filter(StockOut.work_number_id == item.id) \
         .filter(StockOut.approval_status == 'passed') \
         .filter(StockOut.stock_out_date >= start_date) \
         .filter(StockOut.stock_out_date < end_date) \
         .group_by(Material.id).all()

        order_details = db.session.query(
            StockOut.code,
            StockOut.stock_out_date,
            UsageUnit.name.label('unit_name'),
            func.sum(StockOutItem.quantity).label('qty'),
            func.sum(StockOutItem.amount).label('amount'),
            StockOut.id.label('stock_out_id')
        ).join(StockOutItem, StockOutItem.stock_out_id == StockOut.id) \
         .outerjoin(UsageUnit, UsageUnit.id == StockOut.usage_unit_id) \
         .filter(StockOut.work_number_id == item.id) \
         .filter(StockOut.approval_status == 'passed') \
         .filter(StockOut.stock_out_date >= start_date) \
         .filter(StockOut.stock_out_date < end_date) \
         .group_by(StockOut.id).all()

        cat_cost = db.session.query(
            Category.name,
            func.sum(StockOutItem.amount).label('amount')
        ).join(StockOutItem, StockOutItem.material_id == Material.id) \
         .join(Category, Category.id == Material.category_id) \
         .join(StockOut, StockOut.id == StockOutItem.stock_out_id) \
         .filter(StockOut.work_number_id == item.id) \
         .filter(StockOut.approval_status == 'passed') \
         .filter(StockOut.stock_out_date >= start_date) \
         .filter(StockOut.stock_out_date < end_date) \
         .group_by(Category.id).all()

        category_chart_data[item.id] = [{'name': c.name, 'value': float(c.amount or 0)} for c in cat_cost]

        quota_amount = 0
        quotas = MaterialQuota.query.filter_by(work_number_id=item.id).all()
        for q in quotas:
            quota_amount += float(q.quota_amount or 0)

        result.append({
            'id': item.id,
            'code': item.code,
            'division_name': item.division_name,
            'item_name': item.item_name,
            'total_qty': float(item.total_qty or 0),
            'total_cost': float(item.total_cost or 0),
            'order_count': item.order_count,
            'quota_amount': quota_amount,
            'material_details': [{'name': m.name, 'qty': float(m.qty or 0), 'unit_price': float(m.unit_price or 0), 'amount': float(m.amount or 0)} for m in mat_details],
            'order_details': [{'code': o.code, 'date_str': o.stock_out_date.strftime('%Y-%m-%d') if o.stock_out_date else '', 'unit_name': o.unit_name, 'qty': float(o.qty or 0), 'amount': float(o.amount or 0), 'stock_out_id': o.stock_out_id} for o in order_details]
        })

    usage_units = UsageUnit.query.filter_by(project_id=project_id).all()
    categories = Category.query.filter_by(project_id=project_id).all()

    return render_template('report/work_number_cost.html',
                           work_number_data=result,
                           total_cost=total_cost,
                           time_range=time_range,
                           start_date=start_date.strftime('%Y-%m-%d'),
                           end_date=(end_date - timedelta(days=1)).strftime('%Y-%m-%d'),
                           usage_unit_id=usage_unit_id,
                           category_l1=category_l1,
                           usage_units=usage_units,
                           categories=categories,
                           category_chart_data=category_chart_data)


@bp.route('/work_number_cost/export')
@login_required
def export_work_number_cost():
    """导出工号成本统计Excel"""
    project_id = session.get('current_project_id')
    from app.models import WorkNumber, StockOut, StockOutItem, Material, UsageUnit
    from datetime import datetime, timedelta

    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    usage_unit_id = int(request.args.get('usage_unit_id', '0'))
    category_l1 = int(request.args.get('category_l1', '0'))

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        start_date = datetime.now().replace(day=1).date()
    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() + timedelta(days=1)
    except ValueError:
        end_date = (datetime.now() + timedelta(days=32)).replace(day=1).date()

    query = db.session.query(
        WorkNumber.code,
        WorkNumber.division_name,
        WorkNumber.item_name,
        func.sum(StockOutItem.quantity).label('total_qty'),
        func.count(StockOut.id).label('order_count'),
        func.sum(StockOutItem.amount).label('total_cost')
    ).join(StockOut, StockOut.work_number_id == WorkNumber.id) \
     .join(StockOutItem, StockOutItem.stock_out_id == StockOut.id) \
     .filter(WorkNumber.project_id == project_id) \
     .filter(StockOut.approval_status == 'passed') \
     .filter(StockOut.stock_out_date >= start_date) \
     .filter(StockOut.stock_out_date < end_date)

    if usage_unit_id > 0:
        query = query.filter(StockOut.usage_unit_id == usage_unit_id)

    query = query.group_by(WorkNumber.id)
    data = query.all()

    total_cost = sum(float(item.total_cost or 0) for item in data)

    headers = ['工号编码', '分部工程名称', '分项工程', '出库总数量', '出库笔数', '出库成本(元)', '占比(%)']
    rows = []
    for item in data:
        percentage = (float(item.total_cost or 0) / total_cost * 100 if total_cost > 0 else 0)
        rows.append([
            item.code,
            item.division_name or '',
            item.item_name or '',
            round(float(item.total_qty or 0), 4),
            item.order_count,
            round(float(item.total_cost or 0), 2),
            round(percentage, 1)
        ])
    rows.append(['合计', '', '', '', '', round(total_cost, 2), 100])

    date_str = datetime.now().strftime('%Y%m%d')
    filename = f'限额执行报表_{date_str}.xlsx'
    data = export_to_excel(headers, rows, '限额执行报表', filename)
    return _make_export_response(data, filename)


@bp.route('/supplier_ledger')
@login_required
def supplier_ledger():
    """供应商往来台账汇总"""
    project_id = session.get('current_project_id')
    from app.models import Supplier, StockIn, Invoice, Payment
    from datetime import datetime, timedelta

    time_range = request.args.get('time_range', 'month')
    now = datetime.now()
    
    if time_range == 'month':
        start_date = now.replace(day=1).date()
        end_date = (now + timedelta(days=32)).replace(day=1).date()
    elif time_range == 'last_month':
        last_month = now.month - 1 if now.month > 1 else 12
        last_year = now.year if now.month > 1 else now.year - 1
        start_date = datetime(last_year, last_month, 1).date()
        end_date = now.replace(day=1).date()
    elif time_range == 'quarter':
        quarter = (now.month - 1) // 3 + 1
        start_month = (quarter - 1) * 3 + 1
        start_date = datetime(now.year, start_month, 1).date()
        end_date = datetime(now.year, start_month + 3, 1).date() if start_month + 3 <= 12 else datetime(now.year + 1, 1, 1).date()
    elif time_range == 'year':
        start_date = datetime(now.year, 1, 1).date()
        end_date = datetime(now.year + 1, 1, 1).date()
    else:
        start_date_str = request.args.get('start_date', '')
        end_date_str = request.args.get('end_date', '')
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = now.replace(day=1).date()
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() + timedelta(days=1)
        except ValueError:
            end_date = (now + timedelta(days=32)).replace(day=1).date()

    status = request.args.get('status', 'all')

    suppliers = Supplier.query.filter_by(project_id=project_id).all()
    
    result = []
    total_opening = 0
    total_in = 0
    total_invoice = 0
    total_payment = 0
    total_closing = 0

    for supplier in suppliers:
        opening_balance = float(supplier.opening_balance or 0)
        
        current_in = db.session.query(func.sum(StockIn.total_amount)) \
            .filter(StockIn.supplier_id == supplier.id) \
            .filter(StockIn.project_id == project_id) \
            .filter(StockIn.approval_status == 'passed') \
            .filter(StockIn.stock_in_date >= start_date) \
            .filter(StockIn.stock_in_date < end_date) \
            .scalar()
        current_in_amount = float(current_in or 0)

        current_invoice = db.session.query(func.sum(Invoice.amount_with_tax)) \
            .filter(Invoice.supplier_id == supplier.id) \
            .filter(Invoice.project_id == project_id) \
            .filter(Invoice.invoice_date >= start_date) \
            .filter(Invoice.invoice_date < end_date) \
            .scalar()
        current_invoice_amount = float(current_invoice or 0)

        current_payment = db.session.query(func.sum(Payment.amount)) \
            .filter(Payment.supplier_id == supplier.id) \
            .filter(Payment.project_id == project_id) \
            .filter(Payment.approval_status == 'passed') \
            .filter(Payment.payment_date >= start_date) \
            .filter(Payment.payment_date < end_date) \
            .scalar()
        current_payment_amount = float(current_payment or 0)

        closing_balance = opening_balance + current_in_amount - current_payment_amount

        aging_days = 0
        last_payment_date = db.session.query(Payment.payment_date) \
            .filter(Payment.supplier_id == supplier.id) \
            .filter(Payment.project_id == project_id) \
            .order_by(Payment.payment_date.desc()) \
            .first()
        if last_payment_date and last_payment_date[0]:
            aging_days = (now.date() - last_payment_date[0]).days
        
        if aging_days <= 30:
            aging = '30'
        elif aging_days <= 90:
            aging = '90'
        else:
            aging = '90+'

        sup_status = '异常' if (closing_balance > 0 and aging_days > 90) else '正常'

        if status == 'owing' and closing_balance <= 0:
            continue
        if status == 'cleared' and closing_balance > 0:
            continue

        result.append({
            'id': supplier.id,
            'code': supplier.code,
            'name': supplier.name,
            'opening_balance': opening_balance,
            'current_in_amount': current_in_amount,
            'current_invoice_amount': current_invoice_amount,
            'current_payment_amount': current_payment_amount,
            'closing_balance': closing_balance,
            'aging': aging,
            'status': sup_status
        })

        total_opening += opening_balance
        total_in += current_in_amount
        total_invoice += current_invoice_amount
        total_payment += current_payment_amount
        total_closing += closing_balance

    return render_template('report/supplier_ledger.html',
                           supplier_data=result,
                           time_range=time_range,
                           status=status,
                           start_date=start_date.strftime('%Y-%m-%d'),
                           end_date=(end_date - timedelta(days=1)).strftime('%Y-%m-%d'),
                           total_opening=total_opening,
                           total_in=total_in,
                           total_invoice=total_invoice,
                           total_payment=total_payment,
                           total_closing=total_closing)


@bp.route('/supplier_ledger/detail/<int:supplier_id>')
@login_required
def supplier_ledger_detail(supplier_id):
    """供应商往来明细"""
    project_id = session.get('current_project_id')
    from app.models import Supplier, StockIn, Invoice, Payment, StockOut
    from datetime import datetime, timedelta

    supplier = Supplier.query.filter_by(id=supplier_id, project_id=project_id).first_or_404()

    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    now = datetime.now()
    
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        start_date = now.replace(day=1).date()
    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() + timedelta(days=1)
    except ValueError:
        end_date = (now + timedelta(days=32)).replace(day=1).date()

    opening_balance = float(supplier.opening_balance or 0)

    stock_ins = db.session.query(
        StockIn.id,
        StockIn.stock_in_date,
        StockIn.code,
        StockIn.total_amount
    ).filter(StockIn.supplier_id == supplier.id) \
     .filter(StockIn.project_id == project_id) \
     .filter(StockIn.approval_status == 'passed') \
     .filter(StockIn.stock_in_date >= start_date) \
     .filter(StockIn.stock_in_date < end_date) \
     .all()

    invoices = db.session.query(
        Invoice.id,
        Invoice.invoice_date,
        Invoice.invoice_number,
        Invoice.amount_with_tax
    ).filter(Invoice.supplier_id == supplier.id) \
     .filter(Invoice.project_id == project_id) \
     .filter(Invoice.invoice_date >= start_date) \
     .filter(Invoice.invoice_date < end_date) \
     .all()

    payments = db.session.query(
        Payment.id,
        Payment.payment_date,
        Payment.payment_code,
        Payment.amount
    ).filter(Payment.supplier_id == supplier.id) \
     .filter(Payment.project_id == project_id) \
     .filter(Payment.approval_status == 'passed') \
     .filter(Payment.payment_date >= start_date) \
     .filter(Payment.payment_date < end_date) \
     .all()

    ledger_data = []
    ledger_data.append({
        'date_str': start_date.strftime('%Y-%m-%d'),
        'type': '期初',
        'code': '-',
        'summary': '上年结转',
        'in_amount': 0,
        'invoice_amount': 0,
        'payment_amount': 0,
        'balance': opening_balance,
        'ref_id': None
    })

    all_records = []
    for si in stock_ins:
        all_records.append({'date': si[1], 'type': '入库单', 'code': si[2], 'amount': float(si[3] or 0), 'ref_id': si[0]})
    for inv in invoices:
        all_records.append({'date': inv[1], 'type': '发票', 'code': inv[2], 'amount': float(inv[3] or 0), 'ref_id': inv[0]})
    for pay in payments:
        all_records.append({'date': pay[1], 'type': '付款', 'code': pay[2], 'amount': float(pay[3] or 0), 'ref_id': pay[0]})

    all_records.sort(key=lambda x: (x['date'] or datetime.min.date(), x['type']))

    balance = opening_balance
    for record in all_records:
        date_str = record['date'].strftime('%Y-%m-%d') if record['date'] else ''
        in_amount = record['amount'] if record['type'] == '入库单' else 0
        invoice_amount = record['amount'] if record['type'] == '发票' else 0
        payment_amount = record['amount'] if record['type'] == '付款' else 0
        
        balance += in_amount - payment_amount

        ledger_data.append({
            'date_str': date_str,
            'type': record['type'],
            'code': record['code'],
            'summary': record['type'] + '业务',
            'in_amount': in_amount,
            'invoice_amount': invoice_amount,
            'payment_amount': payment_amount,
            'balance': balance,
            'ref_id': record['ref_id']
        })

    closing_balance = balance

    return render_template('report/supplier_ledger_detail.html',
                           supplier=supplier,
                           ledger_data=ledger_data,
                           start_date=start_date_str,
                           end_date=end_date_str,
                           closing_balance=closing_balance)


@bp.route('/supplier_ledger/export')
@login_required
def export_supplier_ledger():
    """导出供应商往来台账Excel"""
    project_id = session.get('current_project_id')
    from app.models import Supplier, StockIn, Invoice, Payment
    from datetime import datetime, timedelta

    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    status = request.args.get('status', 'all')

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        start_date = datetime.now().replace(day=1).date()
    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() + timedelta(days=1)
    except ValueError:
        end_date = (datetime.now() + timedelta(days=32)).replace(day=1).date()

    suppliers = Supplier.query.filter_by(project_id=project_id).all()
    
    headers = ['供应商编码', '供应商名称', '期初余额(元)', '本期入库(元)', '本期开票(元)', '本期付款(元)', '期末余额(元)', '状态']
    rows = []
    
    total_opening = 0
    total_in = 0
    total_invoice = 0
    total_payment = 0
    total_closing = 0

    for supplier in suppliers:
        opening_balance = float(supplier.opening_balance or 0)
        
        current_in = db.session.query(func.sum(StockIn.total_amount)) \
            .filter(StockIn.supplier_id == supplier.id) \
            .filter(StockIn.project_id == project_id) \
            .filter(StockIn.approval_status == 'passed') \
            .filter(StockIn.stock_in_date >= start_date) \
            .filter(StockIn.stock_in_date < end_date) \
            .scalar()
        current_in_amount = float(current_in or 0)

        current_invoice = db.session.query(func.sum(Invoice.amount_with_tax)) \
            .filter(Invoice.supplier_id == supplier.id) \
            .filter(Invoice.project_id == project_id) \
            .filter(Invoice.invoice_date >= start_date) \
            .filter(Invoice.invoice_date < end_date) \
            .scalar()
        current_invoice_amount = float(current_invoice or 0)

        current_payment = db.session.query(func.sum(Payment.amount)) \
            .filter(Payment.supplier_id == supplier.id) \
            .filter(Payment.project_id == project_id) \
            .filter(Payment.approval_status == 'passed') \
            .filter(Payment.payment_date >= start_date) \
            .filter(Payment.payment_date < end_date) \
            .scalar()
        current_payment_amount = float(current_payment or 0)

        closing_balance = opening_balance + current_in_amount - current_payment_amount

        if status == 'owing' and closing_balance <= 0:
            continue
        if status == 'cleared' and closing_balance > 0:
            continue

        sup_status = '异常' if closing_balance > 0 else '正常'

        rows.append([
            supplier.code or '',
            supplier.name,
            round(opening_balance, 2),
            round(current_in_amount, 2),
            round(current_invoice_amount, 2),
            round(current_payment_amount, 2),
            round(closing_balance, 2),
            sup_status
        ])

        total_opening += opening_balance
        total_in += current_in_amount
        total_invoice += current_invoice_amount
        total_payment += current_payment_amount
        total_closing += closing_balance

    rows.append(['合计', '', round(total_opening, 2), round(total_in, 2), round(total_invoice, 2), round(total_payment, 2), round(total_closing, 2), ''])

    date_str = datetime.now().strftime('%Y%m%d')
    filename = f'供应商往来台账_{date_str}.xlsx'
    data = export_to_excel(headers, rows, '供应商往来台账', filename)
    return _make_export_response(data, filename)


@bp.route('/supplier_ledger/detail/<int:supplier_id>/export')
@login_required
def export_supplier_detail(supplier_id):
    """导出单个供应商明细账Excel"""
    project_id = session.get('current_project_id')
    from app.models import Supplier, StockIn, Invoice, Payment, StockOut
    from datetime import datetime, timedelta

    supplier = Supplier.query.filter_by(id=supplier_id, project_id=project_id).first_or_404()

    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    now = datetime.now()
    
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        start_date = now.replace(day=1).date()
    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() + timedelta(days=1)
    except ValueError:
        end_date = (now + timedelta(days=32)).replace(day=1).date()

    opening_balance = float(supplier.opening_balance or 0)

    stock_ins = db.session.query(StockIn.stock_in_date, StockIn.code, StockIn.total_amount) \
        .filter(StockIn.supplier_id == supplier.id, StockIn.project_id == project_id, StockIn.approval_status == 'passed',
                StockIn.stock_in_date >= start_date, StockIn.stock_in_date < end_date).all()

    invoices = db.session.query(Invoice.invoice_date, Invoice.invoice_number, Invoice.amount_with_tax) \
        .filter(Invoice.supplier_id == supplier.id, Invoice.project_id == project_id,
                Invoice.invoice_date >= start_date, Invoice.invoice_date < end_date).all()

    payments = db.session.query(Payment.payment_date, Payment.payment_code, Payment.amount) \
        .filter(Payment.supplier_id == supplier.id, Payment.project_id == project_id, Payment.approval_status == 'passed',
                Payment.payment_date >= start_date, Payment.payment_date < end_date).all()

    headers = ['日期', '单据类型', '单据号', '摘要', '入库金额(元)', '开票金额(元)', '付款金额(元)', '余额(元)']
    rows = []
    
    rows.append([start_date.strftime('%Y-%m-%d'), '期初', '-', '上年结转', '', '', '', round(opening_balance, 2)])

    balance = opening_balance
    all_records = []
    for si in stock_ins:
        all_records.append({'date': si.stock_in_date, 'type': '入库单', 'code': si.code, 'amount': float(si.total_amount or 0)})
    for inv in invoices:
        all_records.append({'date': inv.invoice_date, 'type': '发票', 'code': inv.invoice_number, 'amount': float(inv.amount_with_tax or 0)})
    for pay in payments:
        all_records.append({'date': pay.payment_date, 'type': '付款', 'code': pay.payment_code, 'amount': float(pay.amount or 0)})

    all_records.sort(key=lambda x: (x['date'] or datetime.min.date(), x['type']))

    for record in all_records:
        date_str = record['date'].strftime('%Y-%m-%d') if record['date'] else ''
        in_amount = record['amount'] if record['type'] == '入库单' else ''
        invoice_amount = record['amount'] if record['type'] == '发票' else ''
        payment_amount = record['amount'] if record['type'] == '付款' else ''
        
        if record['type'] == '入库单':
            balance += record['amount']
        elif record['type'] == '付款':
            balance -= record['amount']

        rows.append([date_str, record['type'], record['code'], record['type'] + '业务',
                     round(in_amount, 2) if isinstance(in_amount, (int, float)) else '',
                     round(invoice_amount, 2) if isinstance(invoice_amount, (int, float)) else '',
                     round(payment_amount, 2) if isinstance(payment_amount, (int, float)) else '',
                     round(balance, 2)])

    date_str = datetime.now().strftime('%Y%m%d')
    filename = f'{supplier.name}_往来明细_{date_str}.xlsx'
    data = export_to_excel(headers, rows, f'{supplier.name}往来明细账', filename)
    return _make_export_response(data, filename)
