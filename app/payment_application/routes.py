import os
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from flask import session

from app.payment_application import bp
from app import db
from app.models import (PaymentApplication, Payment, Reconciliation, Supplier,
                       Contract, User)
from app.decorators import editor_required, log_audit
from app.utils import to_decimal, apply_data_scope

ALLOWED_ATTACHMENT = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx'}


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
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    new_filename = f"{timestamp}_{__import__('uuid').uuid4().hex[:8]}.{ext}"
    file_storage.save(os.path.join(upload_dir, new_filename))
    return os.path.join('uploads', sub_dir, new_filename)


def _gen_application_code(project_id):
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"FKSQ-{project_id}-{today}-"
    existing = PaymentApplication.query.filter(
        PaymentApplication.application_code.like(f"{prefix}%")
    ).count()
    return f"{prefix}{existing + 1:03d}"


STATUS_MAP = {
    'draft': ('草稿', 'secondary'),
    'pending': ('待审批', 'warning'),
    'approving': ('审批中', 'warning'),
    'passed': ('已通过', 'success'),
    'rejected': ('已驳回', 'danger'),
    'withdrawn': ('已撤回', 'secondary'),
}


@bp.route('/')
@login_required
def index():
    project_id = session.get('current_project_id')
    # 全部数据权限用户在"全部项目"模式下不限制项目
    if not project_id:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    tab = request.args.get('tab', 'mine', type=str)
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', '', type=str)
    supplier_id = request.args.get('supplier_id', 0, type=int)
    keyword = request.args.get('keyword', '', type=str)

    query = PaymentApplication.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, PaymentApplication)

    if tab == 'mine':
        query = query.filter_by(applicant_id=current_user.id)
    elif tab == 'pending':
        from app.approval.service import get_my_pending_approvals
        pending_instances = get_my_pending_approvals(current_user.id)
        pending_ids = [inst.biz_id for inst in pending_instances if inst.biz_type == 'payment_application']
        query = query.filter(PaymentApplication.id.in_(pending_ids) if pending_ids else PaymentApplication.id == 0)

    if status:
        query = query.filter_by(status=status)
    if supplier_id:
        query = query.filter_by(supplier_id=supplier_id)
    if keyword:
        query = query.filter(PaymentApplication.application_code.like(f'%{keyword}%'))

    pagination = query.order_by(PaymentApplication.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)

    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    return render_template('payment_application/index.html', pagination=pagination,
                           suppliers=suppliers, tab=tab, status=status,
                           supplier_id=supplier_id, keyword=keyword,
                           STATUS_MAP=STATUS_MAP)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='payment_application', operation='新增')
def create():
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        supplier_id = request.form.get('supplier_id', type=int)
        contract_id = request.form.get('contract_id', type=int)
        recon_ids = request.form.getlist('reconciliation_ids')
        recon_ids_str = ','.join(recon_ids) if recon_ids else None

        application = PaymentApplication(
            project_id=project_id,
            application_code=_gen_application_code(project_id),
            apply_date=_parse_date(request.form.get('apply_date')) or datetime.now().date(),
            applicant_id=current_user.id,
            applicant_name=current_user.name or current_user.username,
            supplier_id=supplier_id,
            contract_id=contract_id,
            reconciliation_ids=recon_ids_str,
            apply_amount=to_decimal(request.form.get('apply_amount', 0)),
            payment_method=request.form.get('payment_method', '银行转账'),
            expected_payment_date=_parse_date(request.form.get('expected_payment_date')),
            payment_description=request.form.get('payment_description', '').strip() or None,
            remark=request.form.get('remark', '').strip() or None
        )

        file = request.files.get('attachment')
        if file and file.filename:
            application.attachment = _save_file(file, 'payment_application')

        db.session.add(application)
        db.session.commit()
        flash('付款申请已创建。', 'success')
        return redirect(url_for('payment_application.detail', id=application.id))

    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()

    copy_from_id = request.args.get('copy_from', type=int)
    copy_application = None
    if copy_from_id:
        src = PaymentApplication.query.get(copy_from_id)
        if src and src.project_id == project_id:
            copy_application = src

    return render_template('payment_application/form.html', application=copy_application,
                           suppliers=suppliers, contracts=contracts,
                           is_copy=bool(copy_application))


@bp.route('/<int:id>')
@login_required
def detail(id):
    application = PaymentApplication.query.get_or_404(id)

    recon_list = []
    if application.reconciliation_ids:
        ids = [int(x) for x in application.reconciliation_ids.split(',') if x.isdigit()]
        recon_list = Reconciliation.query.filter(Reconciliation.id.in_(ids)).all() if ids else []

    from app.approval.service import get_instance_by_biz
    approval_instance = get_instance_by_biz('payment_application', application.id)

    can_submit = application.status in ('draft', 'rejected')
    can_withdraw = application.status in ('pending', 'approving') and application.applicant_id == current_user.id
    can_edit = application.status in ('draft', 'rejected')
    can_delete = application.status == 'draft'

    return render_template('payment_application/detail.html', application=application,
                           recon_list=recon_list, approval_instance=approval_instance,
                           can_submit=can_submit, can_withdraw=can_withdraw,
                           can_edit=can_edit, can_delete=can_delete,
                           STATUS_MAP=STATUS_MAP)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='payment_application', operation='编辑')
def edit(id):
    application = PaymentApplication.query.get_or_404(id)
    if application.status not in ('draft', 'rejected'):
        flash('只有草稿或已驳回状态的申请才能修改。', 'danger')
        return redirect(url_for('payment_application.detail', id=application.id))

    if request.method == 'POST':
        application.supplier_id = request.form.get('supplier_id', type=int)
        application.contract_id = request.form.get('contract_id', type=int)
        recon_ids = request.form.getlist('reconciliation_ids')
        application.reconciliation_ids = ','.join(recon_ids) if recon_ids else None
        application.apply_amount = to_decimal(request.form.get('apply_amount', 0))
        application.payment_method = request.form.get('payment_method', '银行转账')
        application.expected_payment_date = _parse_date(request.form.get('expected_payment_date'))
        application.payment_description = request.form.get('payment_description', '').strip() or None
        application.remark = request.form.get('remark', '').strip() or None
        application.apply_date = _parse_date(request.form.get('apply_date')) or application.apply_date

        file = request.files.get('attachment')
        if file and file.filename:
            new_path = _save_file(file, 'payment_application')
            if new_path:
                application.attachment = new_path

        if application.status == 'rejected':
            application.status = 'draft'
            application.approval_status = 'draft'

        db.session.commit()
        flash('付款申请已更新。', 'success')
        return redirect(url_for('payment_application.detail', id=application.id))

    project_id = session.get('current_project_id')
    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
    return render_template('payment_application/form.html', application=application,
                           suppliers=suppliers, contracts=contracts)


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='payment_application', operation='提交审批')
def submit_approval(id):
    application = PaymentApplication.query.get_or_404(id)
    if application.status not in ('draft', 'rejected'):
        flash('该申请不在可提交状态。', 'warning')
        return redirect(url_for('payment_application.detail', id=application.id))

    from app.approval.service import submit_approval, is_approval_enabled
    if not is_approval_enabled('payment_application'):
        # 未启用审批流，直接通过并生成付款
        application.status = 'passed'
        application.approval_status = 'passed'
        _generate_payment(application)
        db.session.commit()
        flash('未启用审批流程，已直接生成付款记录。', 'success')
        return redirect(url_for('payment_application.detail', id=application.id))

    opinion = request.form.get('opinion', '').strip()
    success, msg, _ = submit_approval('payment_application', application.id,
                                      applicant_id=current_user.id, opinion=opinion,
                                      project_id=application.project_id)
    if success:
        application.status = 'pending'
        db.session.commit()
    flash(msg, 'success' if success else 'danger')
    return redirect(url_for('payment_application.detail', id=application.id))


@bp.route('/<int:id>/withdraw', methods=['POST'])
@login_required
@log_audit(module='payment_application', operation='撤回')
def withdraw(id):
    application = PaymentApplication.query.get_or_404(id)
    if application.applicant_id != current_user.id:
        flash('只能撤回自己的申请。', 'danger')
        return redirect(url_for('payment_application.detail', id=application.id))

    from app.approval.service import withdraw as withdraw_approval
    from app.approval.service import get_instance_by_biz
    inst = get_instance_by_biz('payment_application', application.id)
    if not inst:
        flash('未找到审批记录。', 'danger')
        return redirect(url_for('payment_application.detail', id=application.id))

    reason = request.form.get('reason', '').strip()
    success, msg = withdraw_approval(inst.id, applicant_id=current_user.id, reason=reason)
    if success:
        application.status = 'withdrawn'
        db.session.commit()
    flash(msg, 'success' if success else 'danger')
    return redirect(url_for('payment_application.detail', id=application.id))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='payment_application', operation='删除')
def delete(id):
    application = PaymentApplication.query.get_or_404(id)
    if application.status != 'draft':
        flash('只有草稿状态的申请才能删除。', 'danger')
        return redirect(url_for('payment_application.index'))
    db.session.delete(application)
    db.session.commit()
    flash('付款申请已删除。', 'success')
    return redirect(url_for('payment_application.index'))


def _generate_payment(application):
    """审批通过后自动生成付款台账记录"""
    if application.payment_id:
        return

    project_id = application.project_id
    contract = application.contract

    payment = Payment(
        project_id=project_id,
        contract_id=application.contract_id,
        supplier_id=application.supplier_id,
        payment_code=_gen_payment_code(project_id),
        payment_date=datetime.now().date(),
        amount=float(application.apply_amount or 0),
        method=application.payment_method or '银行转账',
        source_application_id=application.id,
        approval_status='passed',
        remark=application.payment_description
    )

    # 不含税金额计算
    if contract and contract.tax_rate:
        rate = float(contract.tax_rate or 0) / 100
        if rate > 0:
            payment.amount_without_tax = round(float(application.apply_amount or 0) / (1 + rate), 2)
            payment.tax_amount = round(float(application.apply_amount or 0) - float(payment.amount_without_tax), 2)
    else:
        payment.amount_without_tax = float(application.apply_amount or 0)
        payment.tax_amount = 0

    db.session.add(payment)
    db.session.flush()
    application.payment_id = payment.id


def _gen_payment_code(project_id):
    """生成付款单号"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"FK-{project_id}-{today}-"
    existing = Payment.query.filter(Payment.payment_code.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


# ---------------- API 端点 ----------------

@bp.route('/api/reconciliations/<int:supplier_id>')
@login_required
def api_reconciliations_by_supplier(supplier_id):
    """获取指定供应商已确认、未全额付款的对账单"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])

    recons = Reconciliation.query.filter_by(
        project_id=project_id, supplier_id=supplier_id, status='已确认'
    ).order_by(Reconciliation.end_date.desc()).all()

    result = []
    for r in recons:
        total_paid = 0
        for p in r.contract.payments.all():
            total_paid += float(p.amount or 0)
        # 简化：所有未付清的都列出
        result.append({
            'id': r.id,
            'code': r.code,
            'contract_id': r.contract_id,
            'contract_code': r.contract.code if r.contract else '',
            'start_date': r.start_date.strftime('%Y-%m-%d') if r.start_date else '',
            'end_date': r.end_date.strftime('%Y-%m-%d') if r.end_date else '',
            'total_amount': float(r.total_amount or 0),
        })
    return jsonify(result)


@bp.route('/api/contract_info/<int:contract_id>')
@login_required
def api_contract_info(contract_id):
    """获取合同信息（已发生金额、已付款金额、剩余应付款）"""
    contract = Contract.query.get_or_404(contract_id)
    from app.utils import get_contract_stats
    stats = get_contract_stats(contract)
    return jsonify({
        'id': contract.id,
        'code': contract.code,
        'supplier_id': contract.supplier_id,
        'supplier_name': contract.supplier.name if contract.supplier else '',
        'amount_with_tax': float(contract.amount_with_tax or 0),
        'occurred_total': stats.get('occurred_total', 0),
        'paid_total': stats.get('paid_total', 0),
        'remaining': stats.get('remaining', 0),
    })
