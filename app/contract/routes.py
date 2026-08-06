import os
import json
from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, current_app, jsonify, session, send_file
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
    from app.utils import validate_file_extension
    from flask import abort
    ok, err = validate_file_extension(file_storage.filename)
    if not ok:
        abort(400, err)
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
    contracts = Contract.query.filter_by(project_id=project_id).filter(Contract.deleted_at.is_(None)).order_by(Contract.code).all()
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
# ============================================================
# 合同模板管理路由（新增）
# 以下路由追加到 app/contract/routes.py 末尾
# ============================================================

from app.services.contract_service import (
    # render_template 已移除（与Flask冲突）
    render_template_html,
    render_table_data,
    generate_word_document,
    generate_contract_no,
    validate_variables,
    generate_contract_instance,
)
from app.models import ContractTemplate, ContractInstance
import json
import os


@bp.route('/templates')
@login_required
def contract_templates():
    """合同模板列表页面"""
    category = request.args.get('category', '')
    keyword = request.args.get('keyword', '')

    query = ContractTemplate.query
    if category:
        query = query.filter(ContractTemplate.category == category)
    if keyword:
        query = query.filter(
            db.or_(
                ContractTemplate.name.contains(keyword),
                ContractTemplate.code.contains(keyword),
            )
        )
    query = query.filter(ContractTemplate.is_active == True)
    templates = query.order_by(ContractTemplate.created_at.desc()).all()

    # 获取所有分类
    categories = db.session.query(ContractTemplate.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]

    return render_template('contract/templates.html',
                           templates=templates,
                           categories=categories,
                           current_category=category,
                           keyword=keyword)


@bp.route('/templates/<int:template_id>')
@login_required
def contract_template_detail(template_id):
    """模板详情页面（显示变量表单）"""
    template = ContractTemplate.query.get_or_404(template_id)

    # 解析变量schema
    try:
        variables_schema = json.loads(template.variables_schema) if template.variables_schema else []
    except (json.JSONDecodeError, TypeError):
        variables_schema = []

    # 获取项目列表（供选择）
    projects = Project.query.filter_by(is_deleted=False).all() if 'Project' in dir() else []

    return render_template('contract/template_generate.html',
                           template=template,
                           variables_schema=variables_schema,
                           projects=projects)


@bp.route('/templates/generate', methods=['POST'])
@login_required
def contract_template_generate():
    """基于模板生成合同实例"""
    template_id = request.form.get('template_id', type=int)
    if not template_id:
        flash('模板ID不能为空', 'danger')
        return redirect(url_for('contract.contract_templates'))

    template = ContractTemplate.query.get_or_404(template_id)
    if not template.is_active:
        flash('该模板已禁用，无法使用', 'danger')
        return redirect(url_for('contract.contract_templates'))

    # 解析变量schema
    try:
        variables_schema = json.loads(template.variables_schema) if template.variables_schema else []
    except (json.JSONDecodeError, TypeError):
        variables_schema = []

    # 收集表单数据
    variable_values = {}
    for var in variables_schema:
        var_name = var['name']
        var_type = var.get('type', 'text')

        if var_type == 'table':
            # 表格型变量 - 从JSON字符串解析
            table_json = request.form.get(var_name, '[]')
            try:
                variable_values[var_name] = json.loads(table_json)
            except (json.JSONDecodeError, TypeError):
                variable_values[var_name] = []
        else:
            value = request.form.get(var_name, '')
            if value:
                variable_values[var_name] = value
            elif var.get('default'):
                variable_values[var_name] = var['default']

    # 获取项目ID
    project_id = request.form.get('project_id', type=int)

    # 自动生成合同编号（如果未提供）
    if not variable_values.get('contract_no'):
        variable_values['contract_no'] = generate_contract_no(project_id, template.code)

    # 校验必填字段
    is_valid, missing = validate_variables(variables_schema, variable_values)
    if not is_valid:
        flash('以下必填字段未填写：{}'.format(', '.join(missing)), 'danger')
        return redirect(url_for('contract.contract_template_detail', template_id=template_id))

    # 创建合同实例
    instance = generate_contract_instance(
        template=template,
        variable_values=variable_values,
        project_id=project_id,
        created_by=current_user.id,
    )

    flash('合同生成成功！合同编号：{}'.format(instance.contract_no), 'success')
    return redirect(url_for('contract.contract_instance_detail', instance_id=instance.id))


@bp.route('/instances')
@login_required
def contract_instances():
    """合同实例列表页面"""
    page = request.args.get('page', 1, type=int)
    per_page = 15
    status = request.args.get('status', '')
    keyword = request.args.get('keyword', '')

    query = ContractInstance.query
    if status:
        query = query.filter(ContractInstance.status == status)
    if keyword:
        query = query.filter(
            db.or_(
                ContractInstance.contract_no.contains(keyword),
                ContractInstance.title.contains(keyword),
            )
        )
    query = query.order_by(ContractInstance.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    instances = pagination.items

    return render_template('contract/instances.html',
                           instances=instances,
                           pagination=pagination,
                           current_status=status,
                           keyword=keyword)


@bp.route('/instances/<int:instance_id>')
@login_required
def contract_instance_detail(instance_id):
    """合同实例详情页（HTML预览 + 下载Word按钮）"""
    instance = ContractInstance.query.get_or_404(instance_id)
    template = instance.template

    # 获取变量值
    variable_values = instance.get_variable_values()

    # 解析variables_schema
    try:
        variables_schema = json.loads(template.variables_schema) if template.variables_schema else []
    except (json.JSONDecodeError, TypeError):
        variables_schema = []

    # 渲染表格数据
    table_data = render_table_data(variables_schema, variable_values)
    all_values = {**variable_values, **table_data}

    # 生成HTML预览
    preview_html = render_template_html(template.template_content, all_values)

    return render_template('contract/instance_detail.html',
                           instance=instance,
                           template=template,
                           preview_html=preview_html)


@bp.route('/instances/<int:instance_id>/download')
@login_required
def contract_instance_download(instance_id):
    """下载Word文档"""
    instance = ContractInstance.query.get_or_404(instance_id)
    template = instance.template

    # 生成文件路径
    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    contracts_dir = os.path.join(upload_folder, 'contract_documents')
    filename = '{}.docx'.format(instance.contract_no or instance.id)
    file_path = os.path.join(contracts_dir, filename)

    # 如果文件不存在或需要重新生成
    if not instance.generated_file_path or not os.path.exists(instance.generated_file_path):
        # 获取变量值
        variable_values = instance.get_variable_values()

        # 解析variables_schema
        try:
            variables_schema = json.loads(template.variables_schema) if template.variables_schema else []
        except (json.JSONDecodeError, TypeError):
            variables_schema = []

        # 渲染表格数据
        table_data = render_table_data(variables_schema, variable_values)
        all_values = {**variable_values, **table_data}

        # 生成Word文档
        generate_word_document(
            template_content=template.template_content,
            variables=all_values,
            output_path=file_path,
            template_name=template.name,
        )

        # 更新实例的文件路径
        instance.generated_file_path = file_path
        db.session.commit()

    # 返回文件
    if os.path.exists(instance.generated_file_path):
        return send_file(
            instance.generated_file_path,
            as_attachment=True,
            download_name='{}.docx'.format(instance.title or instance.contract_no),
        )
    else:
        flash('文件生成失败', 'danger')
        return redirect(url_for('contract.contract_instance_detail', instance_id=instance_id))


@bp.route('/instances/<int:instance_id>/submit', methods=['POST'])
@login_required
def contract_instance_submit(instance_id):
    """提交审批"""
    instance = ContractInstance.query.get_or_404(instance_id)

    if instance.status not in ('draft', 'rejected'):
        flash('当前状态不允许提交', 'warning')
        return redirect(url_for('contract.contract_instance_detail', instance_id=instance_id))

    # 校验必填字段
    template = instance.template
    try:
        variables_schema = json.loads(template.variables_schema) if template.variables_schema else []
    except (json.JSONDecodeError, TypeError):
        variables_schema = []

    variable_values = instance.get_variable_values()
    is_valid, missing = validate_variables(variables_schema, variable_values)
    if not is_valid:
        flash('以下必填字段未填写：{}'.format(', '.join(missing)), 'danger')
        return redirect(url_for('contract.contract_instance_detail', instance_id=instance_id))

    instance.status = 'submitted'
    instance.updated_at = datetime.now()
    db.session.commit()

    # 如果没有配置审批流程，直接标记为已审批并同步到合同台账
    from app.approval.service import is_approval_enabled
    if not is_approval_enabled('contract_instance', instance.project_id):
        instance.status = 'approved'
        instance.updated_at = datetime.now()
        db.session.commit()
        try:
            _sync_instance_to_contract(instance)
            flash('合同已生成并同步到合同台账', 'success')
        except Exception as e:
            import logging
            logging.error(f'合同同步失败: {e}')
            flash('合同已提交，但同步到台账失败，请手动同步', 'warning')
    else:
        flash('合同已提交审批', 'success')
    return redirect(url_for('contract.contract_instance_detail', instance_id=instance_id))


# ============================================================
# 合同模板管理 - 扩展路由（预览 + 新增模板）
# ============================================================





def sync_contract_instance_to_contract(instance_id):
    """公开接口：将合同实例同步到传统合同台账
    
    Args:
        instance_id: ContractInstance的ID
        
    Returns:
        Contract: 创建或已关联的Contract记录
        
    Raises:
        ValueError: 当合同实例不存在或状态不允许同步时
    """
    instance = ContractInstance.query.get(instance_id)
    if not instance:
        raise ValueError(f'合同实例 #{instance_id} 不存在')
    
    if instance.status not in ('approved', 'executed'):
        raise ValueError(f'合同实例 #{instance_id} 状态为 {instance.status}，仅已审批/已执行的实例可同步')
    
    return _sync_instance_to_contract(instance)


def _sync_instance_to_contract(instance):
    """合同实例审批通过时，自动同步到传统合同台账"""
    from app.models import Contract
    # 检查是否已关联
    existing = Contract.query.filter_by(contract_instance_id=instance.id).first()
    if existing:
        return existing
    
    # 从变量值中提取关键信息
    variables = instance.get_variable_values()
    
    # 尝试从变量中提取供应商名称、合同金额等
    supplier_name = variables.get('supplier_name', '') or variables.get('乙方名称', '') or variables.get('party_b', '')
    contract_amount_str = variables.get('contract_amount', '') or variables.get('total_amount', '') or variables.get('合同金额', '') or variables.get('金额', '0')
    sign_date_str = variables.get('sign_date', '') or variables.get('签订日期', '') or variables.get('签署日期', '')
    
    # 查找供应商
    supplier_id = None
    if supplier_name:
        from app.models import Supplier
        supplier = Supplier.query.filter_by(name=supplier_name).first()
        if supplier:
            supplier_id = supplier.id
    
    # 解析金额
    try:
        import re as _re
        amount_str = _re.sub(r'[^\d.]', '', str(contract_amount_str))
        contract_amount = float(amount_str) if amount_str else 0
    except (ValueError, TypeError):
        contract_amount = 0
    
    # 解析日期
    from datetime import datetime
    sign_date = None
    if sign_date_str:
        for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日'):
            try:
                sign_date = datetime.strptime(str(sign_date_str), fmt).date()
                break
            except ValueError:
                continue
    
    # 确定合同类型
    template_category = instance.template.category if instance.template else '采购类'
    type_map = {
        '采购类': '采购合同',
        '租赁类': '租赁合同',
        '运输类': '运输合同',
        '处置类': '处置合同',
    }
    contract_type = type_map.get(template_category, '其他')
    
    # 生成合同编号
    from datetime import datetime as dt
    code = f"HT-{dt.now().strftime('%Y%m%d')}-{instance.id:04d}"
    
    # 创建Contract记录
    contract = Contract(
        project_id=instance.project_id,
        code=code,
        name=instance.title or f"合同实例-{instance.contract_no or instance.id}",
        supplier_id=supplier_id or 0,
        contract_type=contract_type,
        business_type=template_category,
        sign_date=sign_date,
        amount_with_tax=contract_amount,
        tax_rate=13,
        status='正常履约',
        approval_status='passed',
        contract_instance_id=instance.id,
        remark=f"由合同模板实例 #{instance.id} 自动同步生成",
    )
    db.session.add(contract)
    db.session.commit()
    return contract


@bp.route('/templates/preview', methods=['POST'])
@login_required
def contract_template_preview():
    """AJAX预览合同（实时渲染）"""
    from app.services.contract_service import render_template_html, render_table_data
    import json as _json

    template_id = request.form.get('template_id', type=int)
    if not template_id:
        return jsonify({'success': False, 'message': '模板ID不能为空'})

    template = ContractTemplate.query.get_or_404(template_id)

    # 解析变量schema
    try:
        variables_schema = _json.loads(template.variables_schema) if template.variables_schema else []
    except (_json.JSONDecodeError, TypeError):
        variables_schema = []

    # 收集表单数据
    variable_values = {}
    for var in variables_schema:
        var_name = var['name']
        var_type = var.get('type', 'text')
        if var_type == 'table':
            table_json = request.form.get(var_name, '[]')
            try:
                variable_values[var_name] = _json.loads(table_json)
            except (_json.JSONDecodeError, TypeError):
                variable_values[var_name] = []
        else:
            value = request.form.get(var_name, '').strip()
            if value:
                variable_values[var_name] = value
            elif var.get('default'):
                variable_values[var_name] = var['default']

    # 渲染表格数据
    table_data = render_table_data(variables_schema, variable_values)
    all_values = {**variable_values, **table_data}

    # 添加甲方固定信息
    from app.services.contract_service import PARTY_A_INFO
    for k, v in PARTY_A_INFO.items():
        if k not in all_values:
            all_values[k] = v

    # 生成HTML预览
    preview_html = render_template_html(template.template_content, all_values)

    return jsonify({'success': True, 'html': preview_html})


@bp.route('/templates/new')
@login_required
def contract_template_new():
    """新增合同模板页面"""
    return render_template('contract/template_create.html')


@bp.route('/templates/create', methods=['POST'])
@login_required
def contract_template_create():
    """创建新合同模板"""
    import json as _json

    name = request.form.get('name', '').strip()
    code = request.form.get('code', '').strip()
    if not name or not code:
        flash('模板名称和编号不能为空', 'danger')
        return redirect(url_for('contract.contract_template_new'))

    # 检查code是否重复
    existing = ContractTemplate.query.filter_by(code=code).first()
    if existing:
        flash(f'模板编号 {code} 已存在', 'danger')
        return redirect(url_for('contract.contract_template_new'))

    category = request.form.get('category', '').strip()
    description = request.form.get('description', '').strip()
    version = request.form.get('version', '1.0').strip()
    template_content = request.form.get('template_content', '')
    variables_schema_str = request.form.get('variables_schema', '[]')

    # 验证variables_schema是合法JSON
    try:
        variables_schema = _json.loads(variables_schema_str)
    except (_json.JSONDecodeError, TypeError):
        variables_schema = []

    # 添加甲方固定信息变量到模板内容（如果不存在）
    from app.services.contract_service import PARTY_A_INFO
    for key in PARTY_A_INFO:
        placeholder = '{{' + key + '}}'
        if placeholder not in template_content:
            # 不强制添加，用户可以手动使用
            pass

    template = ContractTemplate(
        code=code,
        name=name,
        description=description,
        template_content=template_content,
        variables_schema=_json.dumps(variables_schema, ensure_ascii=False),
        category=category,
        is_active=True,
        version=version
    )
    db.session.add(template)
    db.session.commit()

    flash(f'合同模板「{name}」创建成功！', 'success')
    return redirect(url_for('contract.contract_templates'))



@bp.route('/templates/<int:template_id>/edit', methods=['GET', 'POST'])
@login_required
def contract_template_edit(template_id):
    """编辑合同模板"""
    template = ContractTemplate.query.get_or_404(template_id)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip()
        if not name or not code:
            flash('模板名称和编号不能为空', 'danger')
            return redirect(url_for('contract.contract_template_edit', template_id=template_id))

        # Check code uniqueness (excluding current)
        existing = ContractTemplate.query.filter(
            ContractTemplate.code == code,
            ContractTemplate.id != template_id
        ).first()
        if existing:
            flash(f'模板编号 {code} 已存在', 'danger')
            return redirect(url_for('contract.contract_template_edit', template_id=template_id))

        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        version = request.form.get('version', '1.0').strip()
        template_content = request.form.get('template_content', '')
        variables_schema_str = request.form.get('variables_schema', '[]')

        # Auto increment minor version (e.g., 1.0 -> 1.1)
        try:
            parts = version.split('.')
            if len(parts) >= 2:
                minor = int(parts[-1]) + 1
                parts[-1] = str(minor)
                new_version = '.'.join(parts)
            else:
                new_version = version + '.1'
        except (ValueError, IndexError):
            new_version = version

        # Validate variables_schema
        try:
            variables_schema = json.loads(variables_schema_str)
        except (json.JSONDecodeError, TypeError):
            variables_schema = []

        template.name = name
        template.code = code
        template.description = description
        template.template_content = template_content
        template.variables_schema = json.dumps(variables_schema, ensure_ascii=False)
        template.category = category
        template.version = new_version
        template.updated_at = datetime.now()

        db.session.commit()
        flash(f'合同模板「{name}」编辑成功！版本已更新至 v{new_version}', 'success')
        return redirect(url_for('contract.contract_templates'))

    # GET - render edit form
    try:
        variables_schema = json.loads(template.variables_schema) if template.variables_schema else []
    except (json.JSONDecodeError, TypeError):
        variables_schema = []

    return render_template('contract/template_edit.html',
                           template=template,
                           variables_schema=variables_schema)


@bp.route('/templates/<int:template_id>/delete', methods=['POST'])
@login_required
def contract_template_delete(template_id):
    """删除合同模板（软删除）"""
    template = ContractTemplate.query.get_or_404(template_id)

    # Check if any contract instances are using this template
    instance_count = ContractInstance.query.filter_by(template_id=template_id).count()
    if instance_count > 0:
        flash(f'无法删除模板「{template.name}」，有 {instance_count} 个合同实例正在使用此模板', 'danger')
        return redirect(url_for('contract.contract_templates'))

    # Soft delete - set is_active to False
    template_name = template.name
    template.is_active = False
    template.updated_at = datetime.now()
    db.session.commit()

    flash(f'合同模板「{template_name}」已删除', 'success')
    return redirect(url_for('contract.contract_templates'))


@bp.route('/templates/import-word', methods=['POST'])
@login_required
def contract_template_import_word():
    """导入Word文档，解析内容和变量"""
    from docx import Document
    import io
    import re

    file = request.files.get('word_file')
    if not file or not file.filename:
        return jsonify({'success': False, 'message': '请选择Word文件'}), 400

    if not file.filename.endswith('.docx'):
        return jsonify({'success': False, 'message': '仅支持 .docx 格式文件'}), 400

    try:
        file_stream = io.BytesIO(file.read())
        doc = Document(file_stream)

        # Extract text content from paragraphs
        lines = []
        for paragraph in doc.paragraphs:
            lines.append(paragraph.text)

        # Extract table content as markdown-style
        for table in doc.tables:
            for row in table.rows:
                row_data = [cell.text for cell in row.cells]
                lines.append('| ' + ' | '.join(row_data) + ' |')

        content = '\n'.join(lines)

        # Extract {{variable}} placeholders
        var_pattern = r'\{\{(\w+)\}\}'
        variables = list(set(re.findall(var_pattern, content)))

        return jsonify({
            'success': True,
            'content': content,
            'variables': variables
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'解析失败：{str(e)}'}), 500


@bp.route('/instances/<int:instance_id>/sync_contract', methods=['POST'])
@login_required
def contract_instance_sync(instance_id):
    """手动将合同实例同步到传统合同台账"""
    instance = ContractInstance.query.get_or_404(instance_id)
    if instance.status not in ('approved', 'executed'):
        return jsonify({'success': False, 'message': '仅已审批的合同实例可同步'}), 400
    
    contract = _sync_instance_to_contract(instance)
    return jsonify({
        'success': True, 
        'message': '同步成功',
        'contract_id': contract.id,
        'contract_code': contract.code
    })



# ========== P2: 合同变更/补充协议管理 ==========

@bp.route('/<int:id>/change', methods=['GET', 'POST'])
@login_required
@editor_required
def contract_change(id):
    """合同变更/补充协议"""
    contract = Contract.query.get_or_404(id)
    if request.method == 'POST':
        change_type = request.form.get('change_type', 'change')  # change/supplement
        change_content = request.form.get('change_content', '').strip()
        change_amount = request.form.get('change_amount', type=float) or 0
        change_reason = request.form.get('change_reason', '').strip()
        change_date_str = request.form.get('change_date')
        try:
            change_date = datetime.strptime(change_date_str, '%Y-%m-%d').date() if change_date_str else date.today()
        except Exception:
            change_date = date.today()
        
        # 创建变更记录（使用合同的备注字段记录变更历史）
        change_record = f"[{change_date.strftime('%Y-%m-%d')}] {'补充协议' if change_type == 'supplement' else '合同变更'}: {change_content}"
        if change_amount:
            change_record += f" 金额变动: {change_amount}"
        if change_reason:
            change_record += f" 原因: {change_reason}"
        
        if contract.remark:
            contract.remark = contract.remark + "\n" + change_record
        else:
            contract.remark = change_record
        
        # 如果有金额变动，更新合同金额
        if change_amount and change_type == 'supplement':
            contract.amount_with_tax = (float(contract.amount_with_tax or 0)) + change_amount
        
        db.session.commit()
        flash('合同变更记录已保存。', 'success')
        return redirect(url_for('contract.detail', id=id))
    
    return render_template('contract/change.html', contract=contract,
                           today_str=date.today().strftime('%Y-%m-%d'))


@bp.route('/<int:id>/changes')
@login_required
def contract_changes(id):
    """合同变更历史"""
    contract = Contract.query.get_or_404(id)
    # 从备注中解析变更记录
    changes = []
    if contract.remark:
        for line in contract.remark.split('\n'):
            if line.startswith('[') and ('变更' in line or '补充协议' in line):
                changes.append(line)
    return jsonify({'changes': changes})


@bp.route('/expiry-check')
@login_required
def expiry_check():
    """合同到期检查 - P2"""
    from flask import session
    from datetime import timedelta
    project_id = session.get('current_project_id')
    
    today = date.today()
    warning_days = 30  # 30天内到期预警
    
    query = Contract.query.filter(Contract.is_deleted == False)
    if project_id:
        query = query.filter_by(project_id=project_id)
    
    # 查找有结束日期的合同（从备注或其他字段推断，或使用sign_date + 合同期）
    contracts = query.filter(Contract.status == '正常履约').all()
    
    expiry_list = []
    for c in contracts:
        # 如果合同金额大于0且有签订日期，假设合同期为1年（简化逻辑）
        # 实际应根据合同条款字段判断
        if c.sign_date:
            estimated_end = c.sign_date + timedelta(days=365)
            days_remaining = (estimated_end - today).days
            if days_remaining <= warning_days and days_remaining >= -30:
                expiry_list.append({
                    'id': c.id,
                    'name': c.name,
                    'code': c.code,
                    'sign_date': c.sign_date.strftime('%Y-%m-%d') if c.sign_date else '',
                    'estimated_end': estimated_end.strftime('%Y-%m-%d'),
                    'days_remaining': days_remaining,
                    'status': 'urgent' if days_remaining <= 7 else ('warning' if days_remaining <= 30 else 'normal')
                })
    
    # 按剩余天数排序
    expiry_list.sort(key=lambda x: x['days_remaining'])
    
    return jsonify({'contracts': expiry_list, 'total': len(expiry_list)})
