import os
import json
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, current_app, jsonify, session
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import or_, func

from app.contract import bp
from app import db
from app.models import (Contract, ContractItem, Invoice, Payment, Supplier,
                       Material, StockIn, PaymentApplication)
from app.decorators import editor_required, log_audit
from app.utils import (gen_contract_code, gen_payment_code, calc_without_tax,
                       calc_unit_price_without_tax, get_contract_stats, to_decimal,
                       record_changes, get_change_logs, model_to_dict, get_field_label,
                       apply_data_scope, get_project_materials, get_project_suppliers,
                       ConfigCache)


def _ai_vision_enabled():
    """判断AI视觉识别是否启用"""
    try:
        return (ConfigCache.get('ai_enabled', 'false') == 'true'
                and ConfigCache.get('ai_vision_enabled', 'false') == 'true')
    except Exception:
        return False

ALLOWED_ATTACHMENT = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx'}


def update_contract_amounts(contract_id):
    """更新合同的含税金额和不含税金额（根据明细汇总）"""
    contract = Contract.query.get(contract_id)
    if not contract:
        return
    items = ContractItem.query.filter_by(contract_id=contract_id).all()
    amount_with_tax = sum(float(item.amount_with_tax or 0) for item in items)
    amount_without_tax = sum(float(item.unit_price_without_tax or 0) * float(item.quantity or 0) for item in items)
    contract.amount_with_tax = amount_with_tax
    contract.amount_without_tax = amount_without_tax
    db.session.commit()


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        return None


def _allowed_attachment(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_ATTACHMENT


def _save_file(file_storage, sub_dir):
    if not file_storage or not file_storage.filename:
        return None
    if not _allowed_attachment(file_storage.filename):
        return None
    filename = secure_filename(file_storage.filename)
    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], sub_dir)
    os.makedirs(upload_dir, exist_ok=True)
    file_storage.save(os.path.join(upload_dir, filename))
    return os.path.join('uploads', sub_dir, filename)


def _gen_unique_code(model, project_id, prefix, field_name='code'):
    """生成唯一单号

    委托 utils._gen_code_with_seq 处理,内置进程锁+重试机制避免并发产生重复单号。
    注意: 此处 prefix 使用 'HT' 等无分隔符格式,与项目历史单号风格一致。
    """
    from app.utils import _gen_code_with_seq
    return _gen_code_with_seq(prefix, project_id, model, code_field=field_name)


@bp.route('/')
@login_required
def index():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    supplier_id = request.args.get('supplier_id', 0, type=int)
    status = request.args.get('status', '', type=str)
    deleted_filter = request.args.get('deleted', 'normal', type=str)

    query = Contract.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, Contract)
    
    if deleted_filter == 'normal':
        query = query.filter_by(is_deleted=False)
    elif deleted_filter == 'deleted':
        query = query.filter_by(is_deleted=True)
    
    if keyword:
        query = query.filter(or_(Contract.code.contains(keyword), Contract.name.contains(keyword)))
    if supplier_id:
        query = query.filter_by(supplier_id=supplier_id)
    if status:
        query = query.filter_by(status=status)

    pagination = query.order_by(Contract.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)

    # 附加统计字段
    for c in pagination.items:
        c.stats = get_contract_stats(c)

    suppliers = get_project_suppliers(project_id, common_only=True).all()
    return render_template('contract/index.html', pagination=pagination, keyword=keyword,
                           supplier_id=supplier_id, status=status, suppliers=suppliers)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        attachment = _save_file(request.files.get('attachment'), 'contracts')
        tax_rate = to_decimal(request.form.get('tax_rate'), 13)

        code = request.form.get('code', '').strip()
        if not code:
            code = _gen_unique_code(Contract, project_id, 'HT')

        contract = Contract(
            project_id=project_id,
            code=code,
            name=request.form.get('name', '').strip(),
            supplier_id=request.form.get('supplier_id', type=int),
            contract_type=request.form.get('contract_type', '').strip() or None,
            business_type=request.form.get('business_type', '').strip() or None,
            procurement_method=request.form.get('procurement_method', '').strip() or None,
            sign_date=_parse_date(request.form.get('sign_date')),
            amount_with_tax=0,
            tax_rate=tax_rate,
            amount_without_tax=0,
            status=request.form.get('status', '正常履约'),
            is_final_settled=bool(request.form.get('is_final_settled')),
            is_litigated=bool(request.form.get('is_litigated')),
            attachment=attachment,
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(contract)
        db.session.commit()
        flash('合同创建成功。', 'success')
        return redirect(url_for('contract.detail', id=contract.id))

    suppliers = get_project_suppliers(project_id, common_only=True).all()
    default_code = _gen_unique_code(Contract, project_id, 'HT')

    copy_from_id = request.args.get('copy_from', type=int)
    copy_contract = None
    if copy_from_id:
        src = Contract.query.get(copy_from_id)
        if src and src.project_id == project_id:
            copy_contract = src

    return render_template('contract/form.html', contract=copy_contract,
                           suppliers=suppliers, default_code=default_code,
                           is_copy=bool(copy_contract))


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='编辑')
def edit(id):
    contract = Contract.query.get_or_404(id)
    if request.method == 'POST':
        old_data = model_to_dict(contract)

        attachment = _save_file(request.files.get('attachment'), 'contracts')
        if attachment:
            contract.attachment = attachment

        contract.code = request.form.get('code', '').strip()
        contract.name = request.form.get('name', '').strip()
        contract.supplier_id = request.form.get('supplier_id', type=int)
        contract.contract_type = request.form.get('contract_type', '').strip() or None
        contract.business_type = request.form.get('business_type', '').strip() or None
        contract.procurement_method = request.form.get('procurement_method', '').strip() or None
        contract.sign_date = _parse_date(request.form.get('sign_date'))
        contract.tax_rate = to_decimal(request.form.get('tax_rate'), 13)
        contract.status = request.form.get('status', '正常履约')
        contract.is_final_settled = bool(request.form.get('is_final_settled'))
        contract.is_litigated = bool(request.form.get('is_litigated'))
        contract.remark = request.form.get('remark', '').strip()

        new_data = model_to_dict(contract)
        record_changes('contracts', contract.id, old_data, new_data,
                       changed_by=current_user.name or current_user.username)

        db.session.commit()
        flash('合同更新成功。', 'success')
        return redirect(url_for('contract.detail', id=contract.id))

    suppliers = get_project_suppliers(contract.project_id, common_only=True).all()
    return render_template('contract/form.html', contract=contract, suppliers=suppliers)


@bp.route('/<int:id>')
@login_required
def detail(id):
    contract = Contract.query.get_or_404(id)
    stats = get_contract_stats(contract)
    items = contract.items.order_by(ContractItem.id).all()
    invoices = contract.invoices.order_by(Invoice.invoice_date.desc()).all()
    payments = contract.payments.order_by(Payment.payment_date.desc()).all()
    materials = get_project_materials(contract.project_id, common_only=True).all()

    from app.models import StockInItem, StockIn
    over_inbounds = db.session.query(StockInItem, StockIn).join(
        StockIn, StockInItem.stock_in_id == StockIn.id
    ).filter(
        StockIn.contract_id == contract.id,
        StockInItem.is_over_contract == True
    ).order_by(StockIn.stock_in_date.desc()).all()

    change_logs = get_change_logs('contracts', contract.id)

    return render_template('contract/detail.html', contract=contract, stats=stats,
                           items=items, invoices=invoices, payments=payments,
                           materials=materials, over_inbounds=over_inbounds,
                           change_logs=change_logs, get_field_label=get_field_label)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='作废')
def delete(id):
    contract = Contract.query.get_or_404(id)
    
    has_related = False
    if contract.items.count() > 0 or contract.invoices.count() > 0 or contract.payments.count() > 0:
        has_related = True
    
    contract.is_deleted = True
    contract.status = '已作废'
    db.session.commit()
    
    if has_related:
        flash('合同已作废，关联数据保留但不再参与业务统计。', 'success')
    else:
        flash('合同已作废。', 'success')
    return redirect(url_for('contract.index'))


@bp.route('/<int:id>/restore', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='恢复')
def restore(id):
    contract = Contract.query.get_or_404(id)
    contract.is_deleted = False
    contract.status = '正常履约'
    db.session.commit()
    flash('合同已恢复正常状态。', 'success')
    return redirect(url_for('contract.index'))


# ---------------- 合同明细 ----------------
@bp.route('/<int:id>/items/create', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='新增明细')
def create_item(id):
    contract = Contract.query.get_or_404(id)
    material_id = request.form.get('material_id', type=int)
    material = Material.query.get_or_404(material_id)
    price_type = request.form.get('price_type', '固定单价')
    quantity = to_decimal(request.form.get('quantity'))
    tax_rate = to_decimal(request.form.get('tax_rate'), contract.tax_rate)
    unit_price_with_tax = to_decimal(request.form.get('unit_price_with_tax'))
    unit_price_without_tax = calc_unit_price_without_tax(unit_price_with_tax, tax_rate)
    amount_with_tax = float(quantity) * float(unit_price_with_tax)

    item = ContractItem(
        contract_id=contract.id,
        material_id=material_id,
        price_type=price_type,
        quantity=quantity,
        tax_rate=tax_rate,
        unit_price_with_tax=unit_price_with_tax,
        unit_price_without_tax=unit_price_without_tax,
        amount_with_tax=amount_with_tax,
        remark=request.form.get('remark', '').strip()
    )
    db.session.add(item)
    db.session.commit()
    update_contract_amounts(contract.id)
    flash('合同明细已添加。', 'success')
    return redirect(url_for('contract.detail', id=contract.id))


@bp.route('/items/<int:item_id>/edit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='编辑明细')
def edit_item(item_id):
    item = ContractItem.query.get_or_404(item_id)
    item.price_type = request.form.get('price_type', '固定单价')
    item.quantity = to_decimal(request.form.get('quantity'))
    item.tax_rate = to_decimal(request.form.get('tax_rate'), 13)
    item.unit_price_with_tax = to_decimal(request.form.get('unit_price_with_tax'))
    item.unit_price_without_tax = calc_unit_price_without_tax(item.unit_price_with_tax, item.tax_rate)
    item.amount_with_tax = float(item.quantity) * float(item.unit_price_with_tax)
    item.remark = request.form.get('remark', '').strip()
    db.session.commit()
    update_contract_amounts(item.contract_id)
    flash('合同明细已更新。', 'success')
    return redirect(url_for('contract.detail', id=item.contract_id))


@bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='删除明细')
def delete_item(item_id):
    item = ContractItem.query.get_or_404(item_id)
    contract_id = item.contract_id
    db.session.delete(item)
    db.session.commit()
    update_contract_amounts(contract_id)
    flash('合同明细已删除。', 'success')
    return redirect(url_for('contract.detail', id=contract_id))


@bp.route('/<int:id>/items/batch_create', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='批量新增明细')
def batch_create_items(id):
    """批量新增合同明细：接收逗号分隔的物资ID列表，每个物资创建一条明细"""
    contract = Contract.query.get_or_404(id)
    material_ids_raw = request.form.get('material_ids', '')
    material_ids = [int(mid) for mid in material_ids_raw.split(',') if mid.strip().isdigit()]
    count = 0
    for mid in material_ids:
        if ContractItem.query.filter_by(contract_id=contract.id, material_id=mid).first():
            continue
        item = ContractItem(
            contract_id=contract.id,
            material_id=mid,
            price_type='固定单价',
            quantity=0,
            tax_rate=contract.tax_rate,
            unit_price_with_tax=0,
            unit_price_without_tax=0,
            amount_with_tax=0,
            remark=''
        )
        db.session.add(item)
        count += 1
    db.session.commit()
    update_contract_amounts(contract.id)
    flash(f'成功批量添加 {count} 条合同明细。', 'success')
    return redirect(url_for('contract.detail', id=contract.id))


@bp.route('/api/batch_update_price_type', methods=['POST'])
@login_required
@editor_required
def batch_update_price_type():
    """批量更新合同明细单价类型"""
    data = request.get_json()
    item_ids = data.get('item_ids', [])
    price_type = data.get('price_type', '')
    if not item_ids or not price_type:
        return jsonify({'success': False, 'message': '参数错误'})
    try:
        ContractItem.query.filter(ContractItem.id.in_(item_ids)).update(
            {'price_type': price_type}, synchronize_session=False)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


# ---------------- 发票 ----------------
@bp.route('/invoices')
@login_required
def invoices():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    supplier_id = request.args.get('supplier_id', 0, type=int)
    contract_id = request.args.get('contract_id', 0, type=int)

    query = Invoice.query.filter_by(project_id=project_id)
    if supplier_id:
        query = query.filter_by(supplier_id=supplier_id)
    if contract_id:
        query = query.filter_by(contract_id=contract_id)
    pagination = query.order_by(Invoice.invoice_date.desc()).paginate(
        page=page, per_page=10, error_out=False)

    suppliers = get_project_suppliers(project_id, common_only=True).all()
    contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
    return render_template('contract/invoices.html', pagination=pagination,
                           suppliers=suppliers, contracts=contracts,
                           supplier_id=supplier_id, contract_id=contract_id)


@bp.route('/invoices/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='新增发票')
def create_invoice():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        contract = Contract.query.get_or_404(request.form.get('contract_id', type=int))
        amount_with_tax = to_decimal(request.form.get('amount_with_tax'))
        tax_rate = to_decimal(request.form.get('tax_rate'), 13)
        amount_without_tax = calc_without_tax(amount_with_tax, tax_rate)
        file_path = _save_file(request.files.get('file'), 'invoices')

        invoice = Invoice(
            project_id=project_id,
            contract_id=contract.id,
            supplier_id=contract.supplier_id,
            invoice_code=request.form.get('invoice_code', '').strip(),
            invoice_number=request.form.get('invoice_number', '').strip(),
            invoice_date=_parse_date(request.form.get('invoice_date')),
            amount_with_tax=amount_with_tax,
            tax_rate=tax_rate,
            amount_without_tax=amount_without_tax,
            file_path=file_path,
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(invoice)
        db.session.commit()
        flash('发票已添加。', 'success')
        return redirect(url_for('contract.invoices'))

    contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
    pre_contract_id = request.args.get('contract_id', type=int)
    return render_template('contract/invoice_form.html', invoice=None, contracts=contracts,
                           pre_contract_id=pre_contract_id,
                           ai_vision_enabled=_ai_vision_enabled())


@bp.route('/invoices/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='编辑发票')
def edit_invoice(id):
    invoice = Invoice.query.get_or_404(id)
    if request.method == 'POST':
        file_path = _save_file(request.files.get('file'), 'invoices')
        if file_path:
            invoice.file_path = file_path
        contract = Contract.query.get_or_404(request.form.get('contract_id', type=int))
        invoice.contract_id = contract.id
        invoice.supplier_id = contract.supplier_id
        invoice.invoice_code = request.form.get('invoice_code', '').strip()
        invoice.invoice_number = request.form.get('invoice_number', '').strip()
        invoice.invoice_date = _parse_date(request.form.get('invoice_date'))
        invoice.amount_with_tax = to_decimal(request.form.get('amount_with_tax'))
        invoice.tax_rate = to_decimal(request.form.get('tax_rate'), 13)
        invoice.amount_without_tax = calc_without_tax(invoice.amount_with_tax, invoice.tax_rate)
        invoice.remark = request.form.get('remark', '').strip()
        db.session.commit()
        flash('发票已更新。', 'success')
        return redirect(url_for('contract.invoices'))

    contracts = Contract.query.filter_by(project_id=invoice.project_id).order_by(Contract.code).all()
    return render_template('contract/invoice_form.html', invoice=invoice, contracts=contracts,
                           ai_vision_enabled=_ai_vision_enabled())


@bp.route('/invoices/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='删除发票')
def delete_invoice(id):
    invoice = Invoice.query.get_or_404(id)
    db.session.delete(invoice)
    db.session.commit()
    flash('发票已删除。', 'success')
    return redirect(url_for('contract.invoices'))


# ---------------- 付款 ----------------
@bp.route('/payments')
@login_required
def payments():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    supplier_id = request.args.get('supplier_id', 0, type=int)
    contract_id = request.args.get('contract_id', 0, type=int)

    query = Payment.query.filter_by(project_id=project_id)
    if supplier_id:
        query = query.filter_by(supplier_id=supplier_id)
    if contract_id:
        query = query.filter_by(contract_id=contract_id)
    pagination = query.order_by(Payment.payment_date.desc()).paginate(
        page=page, per_page=10, error_out=False)

    suppliers = get_project_suppliers(project_id, common_only=True).all()
    contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
    return render_template('contract/payments.html', pagination=pagination,
                           suppliers=suppliers, contracts=contracts,
                           supplier_id=supplier_id, contract_id=contract_id)


@bp.route('/payments/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='新增付款')
def create_payment():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    application_id = request.args.get('application_id', type=int)
    pre_application = None
    if application_id:
        pre_application = PaymentApplication.query.get(application_id)

    if request.method == 'POST':
        contract = Contract.query.get_or_404(request.form.get('contract_id', type=int))
        source_application_id = request.form.get('source_application_id', type=int) or None
        payment = Payment(
            project_id=project_id,
            contract_id=contract.id,
            supplier_id=contract.supplier_id,
            payment_code=_gen_unique_code(Payment, project_id, 'FK', 'payment_code'),
            payment_date=_parse_date(request.form.get('payment_date')),
            amount=to_decimal(request.form.get('amount')),
            method=request.form.get('method', '银行转账'),
            source_application_id=source_application_id,
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(payment)
        db.session.commit()
        flash('付款记录已添加。', 'success')
        return redirect(url_for('contract.payments'))

    contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
    pre_contract_id = request.args.get('contract_id', type=int)
    applications = PaymentApplication.query.filter_by(
        project_id=project_id, status='passed'
    ).order_by(PaymentApplication.apply_date.desc()).all()
    return render_template('contract/payment_form.html', payment=None, contracts=contracts,
                           pre_contract_id=pre_contract_id,
                           pre_application=pre_application,
                           applications=applications)


@bp.route('/payments/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='编辑付款')
def edit_payment(id):
    payment = Payment.query.get_or_404(id)
    if request.method == 'POST':
        contract = Contract.query.get_or_404(request.form.get('contract_id', type=int))
        source_application_id = request.form.get('source_application_id', type=int) or None
        payment.contract_id = contract.id
        payment.supplier_id = contract.supplier_id
        payment.payment_date = _parse_date(request.form.get('payment_date'))
        payment.amount = to_decimal(request.form.get('amount'))
        payment.method = request.form.get('method', '银行转账')
        payment.source_application_id = source_application_id
        payment.remark = request.form.get('remark', '').strip()
        db.session.commit()
        flash('付款记录已更新。', 'success')
        return redirect(url_for('contract.payments'))

    contracts = Contract.query.filter_by(project_id=payment.project_id).order_by(Contract.code).all()
    applications = PaymentApplication.query.filter_by(
        project_id=payment.project_id, status='passed'
    ).order_by(PaymentApplication.apply_date.desc()).all()
    return render_template('contract/payment_form.html', payment=payment, contracts=contracts,
                           applications=applications)


@bp.route('/payments/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='删除付款')
def delete_payment(id):
    payment = Payment.query.get_or_404(id)
    db.session.delete(payment)
    db.session.commit()
    flash('付款记录已删除。', 'success')
    return redirect(url_for('contract.payments'))


# ---------------- API 端点（供入库模块调用） ----------------
@bp.route('/api/by_project')
@login_required
def api_contracts_by_project():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])
    contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
    return jsonify([{
        'id': c.id,
        'code': c.code,
        'name': c.name,
        'supplier_id': c.supplier_id,
        'supplier_name': c.supplier_id and Supplier.query.get(c.supplier_id).name or '',
        'tax_rate': float(c.tax_rate or 0),
        'amount_with_tax': float(c.amount_with_tax or 0)
    } for c in contracts])


@bp.route('/api/<int:id>/items')
@login_required
def api_contract_items(id):
    items = ContractItem.query.filter_by(contract_id=id).all()
    result = []
    for it in items:
        m = Material.query.get(it.material_id)
        result.append({
            'id': it.id,
            'material_id': it.material_id,
            'material_name': m.name if m else '',
            'specification': m.specification if m else '',
            'unit': m.unit if m else '',
            'unit_price_with_tax': float(it.unit_price_with_tax or 0),
            'tax_rate': float(it.tax_rate or 0)
        })
    return jsonify(result)


@bp.route('/api/materials_by_category/<int:category_id>')
@login_required
def api_materials_by_category(category_id):
    """按分类获取物资列表（供合同明细批量选择弹窗使用）

    限定返回项目常用物资，确保业务单据只能从本项目常用列表中选择。
    """
    from flask import session
    project_id = session.get('current_project_id')
    materials = get_project_materials(project_id, common_only=True).filter(
        Material.category_id == category_id
    ).order_by(Material.code).all()
    return jsonify([{
        'id': m.id,
        'code': m.code or '',
        'name': m.name,
        'specification': m.specification or '',
        'unit': m.unit
    } for m in materials])


@bp.route('/batch_delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='contract', operation='批量作废')
def batch_delete():
    project_id = session.get('current_project_id')
    ids = request.form.get('ids', '')
    id_list = [int(x) for x in ids.split(',') if x.strip().isdigit()]
    if not id_list:
        flash('请选择要作废的合同。', 'warning')
        return redirect(url_for('contract.index'))

    success_count = 0
    fail_count = 0
    for cid in id_list:
        contract = Contract.query.get(cid)
        if not contract or contract.project_id != project_id:
            fail_count += 1
            continue
        try:
            contract.is_deleted = True
            contract.status = '已作废'
            success_count += 1
        except Exception:
            db.session.rollback()
            fail_count += 1

    db.session.commit()
    if fail_count > 0:
        flash(f'批量删除完成：成功{success_count}条，失败{fail_count}条。', 'warning')
    else:
        flash(f'批量删除成功，共{success_count}条。', 'success')
    return redirect(url_for('contract.index'))


@bp.route('/batch_export')
@login_required
def batch_export():
    from io import BytesIO
    from openpyxl import Workbook
    from flask import make_response
    project_id = session.get('current_project_id')
    ids_str = request.args.get('ids', '')
    id_list = [int(x) for x in ids_str.split(',') if x.strip().isdigit()]

    query = Contract.query.filter_by(project_id=project_id)
    if id_list:
        query = query.filter(Contract.id.in_(id_list))
    contracts = query.order_by(Contract.code).all()

    wb = Workbook()
    ws = wb.active
    ws.title = '合同'
    headers = ['合同编号', '合同名称', '供应商', '合同类型', '签订日期', '含税金额', '不含税金额', '状态', '备注']
    ws.append(headers)
    for c in contracts:
        ws.append([
            c.code,
            c.name or '',
            c.supplier.name if c.supplier else '',
            c.contract_type or '',
            str(c.sign_date or ''),
            float(c.amount_with_tax or 0),
            float(c.amount_without_tax or 0),
            c.status or '',
            c.remark or ''
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = f'attachment; filename=contracts_{datetime.now().strftime("%Y%m%d%H%M%S")}.xlsx'
    return resp
