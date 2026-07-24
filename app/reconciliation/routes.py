from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_, func

from app.reconciliation import bp
from app import db
from app.models import (Reconciliation, ReconciliationItem, StockIn, StockInItem,
                       Supplier, Contract, Material, ContractItem, PriceFormula, Inventory)
from app.decorators import editor_required, log_audit
from app.utils import to_decimal, PriceCalculator, apply_data_scope
from app.services.inventory_cost import InventoryCostService
from app.services.business_logger import BusinessLogger


def _gen_reconciliation_code(project_id):
    """生成唯一对账单号

    委托 utils._gen_code_with_seq 处理,内置进程锁+重试机制避免并发产生重复单号。
    """
    from app.utils import _gen_code_with_seq
    return _gen_code_with_seq('DZ', project_id, Reconciliation)


def _num_to_chinese(num):
    num = round(float(num), 2)
    units = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']
    big_units = ['', '拾', '佰', '仟']
    units2 = ['万', '亿']
    decimal_units = ['角', '分']
    
    num_str = f"{num:.2f}"
    integer_part, decimal_part = num_str.split('.')
    
    if integer_part == '0':
        integer_chinese = '零'
    else:
        groups = []
        while integer_part:
            groups.append(integer_part[-4:])
            integer_part = integer_part[:-4]
        groups.reverse()
        
        group_chinese = []
        for i, group in enumerate(groups):
            group_str = ''
            for j, digit in enumerate(group):
                d = int(digit)
                if d == 0:
                    if j < len(group) - 1 and int(group[j+1]) != 0:
                        group_str += '零'
                else:
                    group_str += units[d]
                    group_str += big_units[len(group) - j - 1]
            if group_str:
                group_str += units2[len(groups) - i - 1]
            group_chinese.append(group_str)
        
        integer_chinese = ''.join(group_chinese)
        if integer_chinese.endswith('零'):
            integer_chinese = integer_chinese[:-1]
    
    decimal_chinese = ''
    if int(decimal_part[0]) != 0:
        decimal_chinese += units[int(decimal_part[0])] + '角'
    if int(decimal_part[1]) != 0:
        decimal_chinese += units[int(decimal_part[1])] + '分'
    if not decimal_chinese:
        decimal_chinese = '整'
    
    return f"{integer_chinese}元{decimal_chinese}"


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
    supplier_id = request.args.get('supplier_id', 0, type=int)
    status = request.args.get('status', '', type=str)

    query = Reconciliation.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, Reconciliation)
    if supplier_id:
        query = query.filter_by(supplier_id=supplier_id)
    if status:
        query = query.filter_by(status=status)

    pagination = query.order_by(Reconciliation.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)

    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    return render_template('reconciliation/index.html', pagination=pagination,
                           suppliers=suppliers, supplier_id=supplier_id, status=status)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='reconciliation', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        except Exception:
            start_date = None
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None
        except Exception:
            end_date = None

        reconciliation = Reconciliation(
            project_id=project_id,
            code=_gen_reconciliation_code(project_id),
            supplier_id=request.form.get('supplier_id', type=int),
            contract_id=request.form.get('contract_id', type=int),
            price_formula_id=request.form.get('price_formula_id', type=int) or None,
            start_date=start_date,
            end_date=end_date,
            remark=request.form.get('remark', '').strip(),
            status='draft'
        )
        db.session.add(reconciliation)
        db.session.commit()

        action = request.form.get('action', 'save')
        if action == 'submit':
            from app.approval.service import submit_approval, is_approval_enabled
            if not is_approval_enabled('reconciliation', project_id):
                flash('对账单保存成功，当前未启用审批流，请手动确认。', 'success')
                return redirect(url_for('reconciliation.detail', id=reconciliation.id))
            success, msg, instance = submit_approval('reconciliation', reconciliation.id,
                                                      applicant_id=current_user.id,
                                                      project_id=project_id)
            if success:
                reconciliation.status = 'pending'
                db.session.commit()
                flash('对账单已提交审批。', 'success')
            else:
                flash(f'提交审批失败：{msg}', 'danger')
            return redirect(url_for('reconciliation.detail', id=reconciliation.id))

        flash('对账单创建成功。', 'success')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    formulas = PriceFormula.query.filter_by(project_id=project_id, status='启用').all()
    from app.approval.service import is_approval_enabled
    approval_enabled = is_approval_enabled('reconciliation', project_id)
    return render_template('reconciliation/form.html', reconciliation=None, suppliers=suppliers, formulas=formulas, approval_enabled=approval_enabled)


@bp.route('/<int:id>')
@login_required
def detail(id):
    reconciliation = Reconciliation.query.get_or_404(id)
    items = reconciliation.items.all()
    # 合同明细映射（用于基准价取合同价）
    contract_items_map = {}
    if reconciliation.contract_id:
        for ci in ContractItem.query.filter_by(contract_id=reconciliation.contract_id).all():
            if ci.material_id:
                contract_items_map[ci.material_id] = ci
    # 计算明细展示
    calc_details = {}
    if reconciliation.price_formula:
        for it in items:
            if it.calc_detail:
                import json
                try:
                    calc_details[it.id] = json.loads(it.calc_detail)
                except Exception:
                    calc_details[it.id] = {}
    return render_template('reconciliation/detail.html', reconciliation=reconciliation, items=items,
                           num_to_chinese=_num_to_chinese, contract_items_map=contract_items_map,
                           calc_details=calc_details)


@bp.route('/<int:id>/pull_data', methods=['POST'])
@login_required
@editor_required
@log_audit(module='reconciliation', operation='拉取数据')
def pull_data(id):
    reconciliation = Reconciliation.query.get_or_404(id)
    if reconciliation.status != 'draft':
        flash('只有草稿状态的对账单才能拉取数据。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    for item in list(reconciliation.items):
        db.session.delete(item)

    stock_in_items = db.session.query(
        StockInItem.material_id,
        Material.name,
        Material.specification,
        Material.unit,
        func.sum(StockInItem.quantity).label('total_qty')
    ).join(Material, Material.id == StockInItem.material_id).join(
        StockIn, StockIn.id == StockInItem.stock_in_id
    ).filter(
        StockIn.project_id == reconciliation.project_id,
        StockIn.supplier_id == reconciliation.supplier_id,
        StockIn.contract_id == reconciliation.contract_id,
        StockIn.stock_in_date >= reconciliation.start_date,
        StockIn.stock_in_date <= reconciliation.end_date,
        StockIn.is_reconciled == False
    ).group_by(StockInItem.material_id, Material.name, Material.specification, Material.unit).all()

    # 单独查询每个物资对应的入库单ID列表,在 Python 端聚合
    # 避免使用 SQL group_concat 函数,该函数在 SQLite/MySQL 方言下行为和长度限制不同
    material_ids = [row[0] for row in stock_in_items if row[0]]
    si_ids_map = {}
    if material_ids:
        si_id_rows = db.session.query(
            StockInItem.material_id, StockIn.id
        ).join(
            StockIn, StockIn.id == StockInItem.stock_in_id
        ).filter(
            StockIn.project_id == reconciliation.project_id,
            StockIn.supplier_id == reconciliation.supplier_id,
            StockIn.contract_id == reconciliation.contract_id,
            StockIn.stock_in_date >= reconciliation.start_date,
            StockIn.stock_in_date <= reconciliation.end_date,
            StockIn.is_reconciled == False,
            StockInItem.material_id.in_(material_ids)
        ).distinct().all()
        for m_id, si_id in si_id_rows:
            si_ids_map.setdefault(m_id, []).append(str(si_id))

    contract_items_map = {}
    if reconciliation.contract_id:
        contract_items = ContractItem.query.filter_by(contract_id=reconciliation.contract_id).all()
        for ci in contract_items:
            if ci.material_id:
                contract_items_map[ci.material_id] = ci

    total_amount = 0
    for m_id, m_name, m_spec, m_unit, qty in stock_in_items:
        si_ids = ','.join(si_ids_map.get(m_id, []))
        ci = contract_items_map.get(m_id)
        price_type = ci.price_type if ci else '浮动单价'
        if ci and ci.price_type == '固定单价':
            base_price = float(ci.unit_price_with_tax) if ci.unit_price_with_tax else 0
        else:
            base_price = float(ci.unit_price_with_tax) if ci and ci.unit_price_with_tax else 0

        # 动态计算资金占用天数：供货日期到对账结束日期的自然天数
        capital_days = None
        if si_ids:
            si_id_list = [int(x) for x in si_ids.split(',') if x.strip()]
            if si_id_list:
                earliest = db.session.query(func.min(StockIn.stock_in_date)).filter(
                    StockIn.id.in_(si_id_list)
                ).scalar()
                if earliest and reconciliation.end_date:
                    capital_days = (reconciliation.end_date - earliest).days
                    if capital_days < 0:
                        capital_days = 0

        # 如果有价格方案，自动计算结算单价
        settlement_price = base_price
        amount_without_tax = 0
        tax_amount = 0
        amount_with_tax = 0
        calc_detail_json = None
        if reconciliation.price_formula and base_price > 0:
            result = PriceCalculator.calculate(base_price, reconciliation.price_formula,
                                               capital_fee_days_override=capital_days)
            settlement_price = result['settlement_price']
            qty_f = float(qty or 0)
            amount_with_tax = round(settlement_price * qty_f, 2)
            amount_without_tax = round(result['price_without_tax'] * qty_f, 2)
            tax_amount = round(amount_with_tax - amount_without_tax, 2)
            import json
            calc_detail_json = json.dumps(result['detail'], ensure_ascii=False)

        amount = float(qty) * settlement_price
        item = ReconciliationItem(
            reconciliation_id=reconciliation.id,
            material_id=m_id,
            quantity=qty,
            unit_price=settlement_price,
            amount=amount,
            base_price=base_price,
            settlement_price=settlement_price,
            amount_without_tax=amount_without_tax,
            tax_amount=tax_amount,
            amount_with_tax=amount_with_tax if amount_with_tax else amount,
            calc_detail=calc_detail_json,
            capital_fee_days=capital_days,
            specification=m_spec,
            unit=m_unit,
            price_type=price_type,
            source_stock_in_ids=si_ids
        )
        db.session.add(item)
        total_amount += amount

    reconciliation.total_amount = total_amount
    db.session.commit()
    flash(f'成功拉取 {len(stock_in_items)} 条入库明细。', 'success')
    return redirect(url_for('reconciliation.detail', id=reconciliation.id))


@bp.route('/<int:id>/apply_formula', methods=['POST'])
@login_required
@editor_required
@log_audit(module='reconciliation', operation='应用价格方案')
def apply_formula(id):
    """批量应用价格方案计算所有行的结算单价"""
    reconciliation = Reconciliation.query.get_or_404(id)
    if reconciliation.status != 'draft':
        flash('只有草稿状态的对账单才能操作。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    formula = reconciliation.price_formula
    if not formula:
        flash('未配置价格方案，请先在对账单创建时选择。', 'warning')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    import json
    total_amount = 0
    total_without_tax = 0
    total_tax = 0
    for item in reconciliation.items:
        base_price = float(item.base_price or 0)
        if base_price <= 0:
            # 没有基准价，跳过
            total_amount += float(item.amount or 0)
            continue
        # 使用每行动态资金占用天数（优先取已保存的值）
        cap_days = item.capital_fee_days if item.capital_fee_days is not None else None
        result = PriceCalculator.calculate(base_price, formula, capital_fee_days_override=cap_days)
        item.settlement_price = result['settlement_price']
        item.unit_price = result['settlement_price']
        qty = float(item.quantity or 0)
        item.amount_with_tax = round(result['settlement_price'] * qty, 2)
        item.amount_without_tax = round(result['price_without_tax'] * qty, 2)
        item.tax_amount = round(item.amount_with_tax - item.amount_without_tax, 2)
        item.amount = item.amount_with_tax
        item.calc_detail = json.dumps(result['detail'], ensure_ascii=False)
        total_amount += float(item.amount)
        total_without_tax += float(item.amount_without_tax)
        total_tax += float(item.tax_amount)

    reconciliation.total_amount = total_amount
    reconciliation.total_amount_without_tax = total_without_tax
    reconciliation.total_tax_amount = total_tax
    db.session.commit()
    flash('已按价格方案批量计算结算单价。', 'success')
    return redirect(url_for('reconciliation.detail', id=reconciliation.id))


@bp.route('/<int:id>/save_prices', methods=['POST'])
@login_required
@editor_required
@log_audit(module='reconciliation', operation='保存单价')
def save_prices(id):
    reconciliation = Reconciliation.query.get_or_404(id)
    if reconciliation.status != 'draft':
        flash('只有草稿状态的对账单才能修改。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    total_amount = 0
    total_without_tax = 0
    total_tax = 0
    for item in reconciliation.items:
        cost_subject = request.form.get(f'cost_subject_{item.id}', '').strip()
        item.cost_subject = cost_subject if cost_subject else None

        # 基准价
        base_price_str = request.form.get(f'base_price_{item.id}', '0')
        base_price = float(to_decimal(base_price_str))
        item.base_price = base_price

        # 资金占用天数（可手动修改）
        cap_days_str = request.form.get(f'capital_days_{item.id}', '').strip()
        if cap_days_str:
            try:
                item.capital_fee_days = int(float(cap_days_str))
            except (TypeError, ValueError):
                pass
        cap_days = item.capital_fee_days if item.capital_fee_days is not None else None

        # 结算单价（可手动覆盖）
        price_str = request.form.get(f'price_{item.id}', '0')
        price = float(to_decimal(price_str))
        qty = float(item.quantity) if item.quantity else 0

        # 如果有价格方案且基准价有值，用计算结果
        if reconciliation.price_formula and base_price > 0 and not price_str:
            result = PriceCalculator.calculate(base_price, reconciliation.price_formula,
                                               capital_fee_days_override=cap_days)
            price = result['settlement_price']
            import json
            item.calc_detail = json.dumps(result['detail'], ensure_ascii=False)
            item.amount_without_tax = round(result['price_without_tax'] * qty, 2)
            item.tax_amount = round(price * qty - item.amount_without_tax, 2)
        else:
            # 手动输入的结算价，按税率反算不含税
            rate = float(reconciliation.price_formula.tax_rate or 13) if reconciliation.price_formula else 13
            item.amount_without_tax = round(price * qty / (1 + rate / 100), 2) if rate > 0 else round(price * qty, 2)
            item.tax_amount = round(price * qty - item.amount_without_tax, 2)

        item.settlement_price = price
        item.unit_price = price
        amount_val = round(price * qty, 2)
        item.amount = amount_val
        item.amount_with_tax = amount_val
        total_amount += amount_val
        total_without_tax += float(item.amount_without_tax)
        total_tax += float(item.tax_amount)

    reconciliation.total_amount = total_amount
    reconciliation.total_amount_without_tax = total_without_tax
    reconciliation.total_tax_amount = total_tax
    db.session.commit()
    flash('单价已保存并重新计算金额。', 'success')
    return redirect(url_for('reconciliation.detail', id=reconciliation.id))


@bp.route('/<int:id>/confirm', methods=['POST'])
@login_required
@editor_required
@log_audit(module='reconciliation', operation='确认')
def confirm(id):
    import time as _time
    _start = _time.time()
    reconciliation = Reconciliation.query.get_or_404(id)
    if reconciliation.status != 'draft':
        BusinessLogger.log_api_call(
            module='reconciliation', action='confirm',
            request_data={'id': id, 'code': reconciliation.code, 'status': reconciliation.status},
            status='fail', error='对账单状态不允许确认',
            duration_ms=int((_time.time() - _start) * 1000)
        )
        flash('只有草稿状态的对账单才能确认。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    reconciliation.status = 'approved'
    reconciliation.confirmed_at = datetime.now()
    reconciliation.confirmed_by = current_user.name or current_user.username

    # 回填入库单：按物资汇总对账单价，更新对应入库明细的实际单价
    # item.source_stock_in_ids 是逗号分隔的入库单ID
    material_settlement = {}  # material_id -> (settlement_price, price_without_tax)
    for item in reconciliation.items:
        if item.material_id and item.settlement_price:
            material_settlement[item.material_id] = (float(item.settlement_price), float(item.amount_without_tax or 0))

    stock_in_ids_set = set()
    for item in reconciliation.items:
        if item.source_stock_in_ids:
            for sid in item.source_stock_in_ids.split(','):
                stock_in_ids_set.add(int(sid))

    # 更新入库单：标记已对账、回填实际单价和实际金额、价格状态改为已确认
    for sid in stock_in_ids_set:
        si = StockIn.query.get(sid)
        if si:
            si.is_reconciled = True
            actual_total = 0
            for si_item in si.items:
                if si_item.material_id in material_settlement:
                    settle_price, settle_amount_without_tax = material_settlement[si_item.material_id]
                    # 保存原始值用于撤销恢复
                    if not hasattr(si_item, '_original_unit_price'):
                        si_item._original_unit_price = si_item.unit_price
                        si_item._original_amount = si_item.amount
                        si_item._original_price_status = si_item.price_status
                    # 计算本次对账的差异(新金额 - 原暂估金额)
                    original_amount = float(si_item.amount or 0)
                    si_item.unit_price = settle_price
                    si_item.price_status = 'confirmed'
                    item_amount = round(float(si_item.quantity or 0) * settle_price, 2)
                    si_item.amount = item_amount
                    actual_total += item_amount
                    # 委托统一服务进行库存金额调整: 暂估转实际(差额调整)
                    # 服务内部使用行级锁,保证并发安全
                    if si_item.material_id:
                        # 记录变更前快照
                        before_inv = Inventory.query.filter_by(
                            project_id=si.project_id, material_id=si_item.material_id
                        ).first()
                        before_amount = (to_decimal(before_inv.actual_amount if before_inv else 0)
                                         + to_decimal(before_inv.estimated_amount if before_inv else 0))

                        InventoryCostService.adjust_reconciliation(
                            project_id=si.project_id,
                            material_id=si_item.material_id,
                            original_amount=original_amount,
                            new_amount=item_amount
                        )

                        # 记录变更后快照
                        after_inv = Inventory.query.filter_by(
                            project_id=si.project_id, material_id=si_item.material_id
                        ).first()
                        after_amount = (to_decimal(after_inv.actual_amount if after_inv else 0)
                                        + to_decimal(after_inv.estimated_amount if after_inv else 0))

                        BusinessLogger.log_inventory_change(
                            project_id=si.project_id, material_id=si_item.material_id,
                            change_type='reconciliation_adjust',
                            before_qty=to_decimal(before_inv.quantity if before_inv else 0),
                            after_qty=to_decimal(after_inv.quantity if after_inv else 0),
                            before_amount=before_amount, after_amount=after_amount,
                            reason=f"对账确认 {reconciliation.code} 入库单 {si.code}"
                        )
            si.actual_amount = actual_total

    db.session.commit()
    BusinessLogger.log_api_call(
        module='reconciliation', action='confirm',
        request_data={'id': id, 'code': reconciliation.code},
        response_data={'status': '已确认', 'stock_in_count': len(stock_in_ids_set)},
        status='success', duration_ms=int((_time.time() - _start) * 1000)
    )
    flash('对账单已确认，关联入库单已回填实际单价并标记为已对账。', 'success')
    return redirect(url_for('reconciliation.detail', id=reconciliation.id))


@bp.route('/<int:id>/cancel_confirm', methods=['POST'])
@login_required
@log_audit(module='reconciliation', operation='撤销确认')
def cancel_confirm(id):
    reconciliation = Reconciliation.query.get_or_404(id)
    if reconciliation.status != 'approved':
        flash('只有已通过状态的对账单才能撤销。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    from app.models import Invoice, Payment
    has_invoice = Invoice.query.filter_by(contract_id=reconciliation.contract_id).count() > 0
    has_payment = Payment.query.filter_by(contract_id=reconciliation.contract_id).count() > 0
    if has_invoice or has_payment:
        flash('已关联发票或付款记录，无法撤销对账。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    has_permission = current_user.is_admin() or \
                     current_user.get_role_code() in ['project_admin', 'material_manager', 'finance_manager'] or \
                     current_user.has_permission('reconciliation:cancel') or \
                     (reconciliation.confirmed_by == current_user.name or reconciliation.confirmed_by == current_user.username)
    
    if not has_permission:
        flash('您没有权限撤销此对账单。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    reconciliation.status = 'draft'
    reconciliation.confirmed_at = None
    reconciliation.confirmed_by = None

    # 构建物料结算信息映射,用于还原库存
    material_settlement = {}
    for item in reconciliation.items:
        if item.material_id:
            material_settlement[item.material_id] = (float(item.settlement_price), float(item.amount_without_tax or 0))

    stock_in_ids_set = set()
    for item in reconciliation.items:
        if item.source_stock_in_ids:
            for sid in item.source_stock_in_ids.split(','):
                stock_in_ids_set.add(int(sid))

    # 撤销时还原入库单和库存
    for sid in stock_in_ids_set:
        si = StockIn.query.get(sid)
        if si:
            si.is_reconciled = False
            # 还原入库单明细
            actual_total = 0
            for si_item in si.items:
                if si_item.material_id in material_settlement:
                    settle_price, _ = material_settlement[si_item.material_id]
                    # 计算当前(结算后)金额
                    current_amount = float(si_item.amount or 0)
                    # 还原 price_status
                    si_item.price_status = 'estimated'
                    # 还原 unit_price 和 amount 为原始值(如果有保存)
                    if hasattr(si_item, '_original_unit_price') and si_item._original_unit_price is not None:
                        si_item.unit_price = si_item._original_unit_price
                        si_item.amount = si_item._original_amount
                    else:
                        # 无快照,用结算价反推(不精确,但避免数据丢失)
                        si_item.unit_price = settle_price
                        si_item.amount = round(float(si_item.quantity or 0) * settle_price, 2)
                    # 委托统一服务还原库存金额: actual 冲回, estimated 加回
                    # 服务内部使用行级锁,保证并发安全
                    if si_item.material_id:
                        # 记录变更前快照
                        before_inv = Inventory.query.filter_by(
                            project_id=si.project_id, material_id=si_item.material_id
                        ).first()
                        before_amount = (to_decimal(before_inv.actual_amount if before_inv else 0)
                                         + to_decimal(before_inv.estimated_amount if before_inv else 0))

                        InventoryCostService.reverse_reconciliation(
                            project_id=si.project_id,
                            material_id=si_item.material_id,
                            settled_amount=current_amount,
                            original_amount=float(si_item.amount or 0)
                        )

                        # 记录变更后快照
                        after_inv = Inventory.query.filter_by(
                            project_id=si.project_id, material_id=si_item.material_id
                        ).first()
                        after_amount = (to_decimal(after_inv.actual_amount if after_inv else 0)
                                        + to_decimal(after_inv.estimated_amount if after_inv else 0))

                        BusinessLogger.log_inventory_change(
                            project_id=si.project_id, material_id=si_item.material_id,
                            change_type='reconciliation_reverse',
                            before_qty=to_decimal(before_inv.quantity if before_inv else 0),
                            after_qty=to_decimal(after_inv.quantity if after_inv else 0),
                            before_amount=before_amount, after_amount=after_amount,
                            reason=f"撤销对账 {reconciliation.code} 入库单 {si.code}"
                        )
                    actual_total += float(si_item.amount or 0)
            si.actual_amount = actual_total

    db.session.commit()
    flash('对账单已撤销为草稿状态,关联入库单和库存金额已还原。', 'success')
    return redirect(url_for('reconciliation.detail', id=reconciliation.id))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='reconciliation', operation='删除')
def delete(id):
    reconciliation = Reconciliation.query.get_or_404(id)
    if reconciliation.status != 'draft':
        flash('只有草稿状态的对账单才能删除。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))

    db.session.delete(reconciliation)
    db.session.commit()
    flash('对账单已删除。', 'success')
    return redirect(url_for('reconciliation.index'))


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='reconciliation', operation='提交审批')
def submit(id):
    reconciliation = Reconciliation.query.get_or_404(id)
    if reconciliation.status != 'draft':
        flash('只有草稿状态的对账单才能提交审批。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))
    from app.approval.service import submit_approval, is_approval_enabled
    project_id = reconciliation.project_id
    if not is_approval_enabled('reconciliation', project_id):
        flash('当前未启用审批流，请手动确认。', 'warning')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))
    success, msg, instance = submit_approval('reconciliation', reconciliation.id,
                                              applicant_id=current_user.id,
                                              project_id=project_id)
    if success:
        reconciliation.status = 'pending'
        db.session.commit()
        flash('对账单已提交审批。', 'success')
    else:
        flash(f'提交审批失败：{msg}', 'danger')
    return redirect(url_for('reconciliation.detail', id=reconciliation.id))


@bp.route('/<int:id>/withdraw', methods=['POST'])
@login_required
@editor_required
@log_audit(module='reconciliation', operation='撤回')
def withdraw(id):
    reconciliation = Reconciliation.query.get_or_404(id)
    if reconciliation.status != 'pending':
        flash('只有待审批状态的对账单才能撤回。', 'danger')
        return redirect(url_for('reconciliation.detail', id=reconciliation.id))
    reconciliation.status = 'draft'
    db.session.commit()
    flash('对账单已撤回为草稿状态。', 'success')
    return redirect(url_for('reconciliation.detail', id=reconciliation.id))


@bp.route('/<int:id>/print')
@login_required
def print_reconciliation(id):
    import time as _time
    _start = _time.time()
    reconciliation = Reconciliation.query.get_or_404(id)
    items = reconciliation.items.all()
    now_date = datetime.now().strftime('%Y-%m-%d')
    try:
        result = render_template('reconciliation/print.html', reconciliation=reconciliation,
                                  items=items, num_to_chinese=_num_to_chinese, now_date=now_date)
        BusinessLogger.log_print(
            module='reconciliation', doc_id=reconciliation.id, doc_code=reconciliation.code,
            status='success', duration_ms=int((_time.time() - _start) * 1000)
        )
        return result
    except Exception as e:
        BusinessLogger.log_print(
            module='reconciliation', doc_id=reconciliation.id, doc_code=reconciliation.code,
            status='fail', error=str(e), duration_ms=int((_time.time() - _start) * 1000)
        )
        raise


@bp.route('/api/contracts_by_supplier/<int:supplier_id>')
@login_required
def api_contracts_by_supplier(supplier_id):
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])
    contracts = Contract.query.filter_by(project_id=project_id, supplier_id=supplier_id).order_by(Contract.code).all()
    return jsonify([{
        'id': c.id,
        'code': c.code,
        'name': c.name
    } for c in contracts])
