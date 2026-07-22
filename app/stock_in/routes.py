import json
from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify, session, make_response
from flask_login import login_required, current_user
from sqlalchemy import or_, func

from app.stock_in import bp
from app import db
from app.models import (StockIn, StockInItem, Inventory, Material, Supplier,
                       Contract, ContractItem)
from app.decorators import editor_required, log_audit
from app.utils import (to_decimal, record_changes, get_change_logs, model_to_dict,
                       get_field_label, apply_data_scope,
                       get_project_materials, get_project_suppliers, ConfigCache)
from app.services.inventory_cost import InventoryCostService
from app.services.business_logger import BusinessLogger


def _ai_vision_enabled():
    """判断AI视觉识别是否启用"""
    try:
        return (ConfigCache.get('ai_enabled', 'false') == 'true'
                and ConfigCache.get('ai_vision_enabled', 'false') == 'true')
    except Exception:
        return False


def _gen_stock_in_code(project_id):
    """生成唯一入库单号

    委托 utils._gen_code_with_seq 处理,内置进程锁+重试机制避免并发产生重复单号。
    """
    from app.utils import _gen_code_with_seq
    return _gen_code_with_seq('RK', project_id, StockIn)


def _apply_inventory(stock_in, delta):
    """根据入库明细调整库存（delta=+1 入库，-1 退库/删除）

    统一委托 InventoryCostService 处理库存数量与金额,保证:
    - 行级锁避免并发更新丢失
    - Decimal 精度避免浮点误差
    - 金额按 price_status 分别累加到 estimated_amount/actual_amount
    - 全链路业务日志,便于偶现问题定位
    """
    for item in stock_in.items:
        qty_delta = to_decimal(item.quantity) * delta
        item_amount = to_decimal(item.amount or 0) * delta
        price_status = item.price_status or 'unpriced'

        # 入库(delta>0): 累加; 退库(delta<0): 累减(传负数给服务即可)
        if qty_delta == 0:
            continue

        # 记录变更前快照
        before_inv = Inventory.query.filter_by(
            project_id=stock_in.project_id, material_id=item.material_id
        ).first()
        before_qty = to_decimal(before_inv.quantity if before_inv else 0)
        before_amount = (to_decimal(before_inv.actual_amount if before_inv else 0)
                         + to_decimal(before_inv.estimated_amount if before_inv else 0))

        # 委托统一服务处理(内部已加行级锁)
        InventoryCostService.apply_inbound(
            project_id=stock_in.project_id,
            material_id=item.material_id,
            quantity=qty_delta,
            amount=item_amount,
            price_status=price_status if price_status in ('estimated', 'confirmed') else 'estimated'
        )

        # 记录变更后快照
        after_inv = Inventory.query.filter_by(
            project_id=stock_in.project_id, material_id=item.material_id
        ).first()
        after_qty = to_decimal(after_inv.quantity if after_inv else 0)
        after_amount = (to_decimal(after_inv.actual_amount if after_inv else 0)
                        + to_decimal(after_inv.estimated_amount if after_inv else 0))

        BusinessLogger.log_inventory_change(
            project_id=stock_in.project_id, material_id=item.material_id,
            change_type='inbound' if delta > 0 else 'inbound_reverse',
            before_qty=before_qty, after_qty=after_qty,
            before_amount=before_amount, after_amount=after_amount,
            reason=f"入库单 {stock_in.code} 明细 {item.id}"
        )


def _update_contract_total_in(stock_in, delta):
    """同步更新合同明细累计入库量（delta=+1 入库，-1 删除/退库）"""
    if not stock_in.contract_id:
        return
    for item in stock_in.items:
        if not item.contract_item_id:
            continue
        ci = ContractItem.query.get(item.contract_item_id)
        if ci:
            new_total = to_decimal(ci.total_in_qty or 0) + to_decimal(item.quantity) * delta
            if new_total < 0:
                new_total = to_decimal(0)
            ci.total_in_qty = new_total


def check_contract_over(contract_id, material_id, quantity, contract_item_id=None):
    """
    检查入库是否超合同量
    返回: (is_over, contract_qty, total_in_qty, remaining, over_ratio)
    """
    if not contract_id or not material_id:
        return (False, 0, 0, 0, 0)
    
    if contract_item_id:
        ci = ContractItem.query.get(contract_item_id)
    else:
        ci = ContractItem.query.filter_by(
            contract_id=contract_id, material_id=material_id).first()
    
    if not ci:
        return (False, 0, 0, 0, 0)
    
    contract_qty = float(ci.quantity or 0)
    total_in_qty = float(ci.total_in_qty or 0)
    qty = float(quantity or 0)
    
    if qty <= 0:
        return (False, contract_qty, total_in_qty, contract_qty - total_in_qty, 0)
    
    remaining = contract_qty - total_in_qty
    is_over = qty > remaining
    
    over_ratio = 0
    if contract_qty > 0:
        over_ratio = round((total_in_qty + qty - contract_qty) / contract_qty * 100, 2)
    
    return (is_over, contract_qty, total_in_qty, remaining, max(0, over_ratio))


@bp.route('/')
@login_required
def index():
    from flask import session
    from app.utils import get_project_filter, is_module_enabled
    project_id, project_ids, is_all_projects_mode = get_project_filter()
    # 全部数据权限用户在"全部项目"模式下不限制项目
    if is_all_projects_mode:
        if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
            flash('请先选择项目。', 'warning')
            return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    supplier_id = request.args.get('supplier_id', 0, type=int)
    contract_id = request.args.get('contract_id', 0, type=int)
    stock_in_type = request.args.get('stock_in_type', '', type=str)
    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)
    status = request.args.get('status', '', type=str)
    approval_status = request.args.get('approval_status', '', type=str)
    quality_status = request.args.get('quality_status', '', type=str)
    project_filter = request.args.get('project_filter', 0, type=int)

    enable_quality_check = is_module_enabled('module_quality_check')

    query = StockIn.query
    if is_all_projects_mode:
        # 汇总模式：按项目筛选条件过滤
        if project_filter:
            query = query.filter_by(project_id=project_filter)
        else:
            query = query.filter(StockIn.project_id.in_(project_ids))
    else:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, StockIn)
    query = query.filter(StockIn.status != 'voided')
    if keyword:
        query = query.filter(or_(StockIn.code.contains(keyword), StockIn.remark.contains(keyword)))
    if supplier_id:
        query = query.filter_by(supplier_id=supplier_id)
    if contract_id:
        query = query.filter_by(contract_id=contract_id)
    if stock_in_type:
        query = query.filter_by(stock_in_type=stock_in_type)
    if status:
        query = query.filter_by(status=status)
    if approval_status:
        query = query.filter_by(approval_status=approval_status)
    if quality_status and enable_quality_check:
        query = query.filter_by(quality_status=quality_status)
    if date_from:
        try:
            query = query.filter(StockIn.stock_in_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            query = query.filter(StockIn.stock_in_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass

    pagination = query.order_by(StockIn.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)

    # 筛选用数据
    if is_all_projects_mode:
        from app.models import Project
        filter_projects = Project.query.filter(Project.id.in_(project_ids), Project.is_archived == False).order_by(Project.name).all()
        suppliers = Supplier.query.filter(Supplier.project_id.in_(project_ids)).order_by(Supplier.name).all()
        contracts = Contract.query.filter(Contract.project_id.in_(project_ids)).order_by(Contract.code).all()
    else:
        filter_projects = []
        suppliers = get_project_suppliers(project_id, common_only=True).all()
        contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
    return render_template('stock_in/index.html', pagination=pagination, keyword=keyword,
                           suppliers=suppliers, contracts=contracts,
                           supplier_id=supplier_id, contract_id=contract_id,
                           stock_in_type=stock_in_type, date_from=date_from, date_to=date_to,
                           status=status, approval_status=approval_status, quality_status=quality_status,
                           enable_quality_check=enable_quality_check,
                           is_all_projects_mode=is_all_projects_mode,
                           filter_projects=filter_projects, project_filter=project_filter)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        contract_id = request.form.get('contract_id', type=int) or None
        supplier_id = request.form.get('supplier_id', type=int) or None
        # 如果选了合同，供应商自动带出
        if contract_id and not supplier_id:
            contract = Contract.query.get(contract_id)
            if contract:
                supplier_id = contract.supplier_id

        stock_in_date_str = request.form.get('stock_in_date')
        try:
            stock_in_date = datetime.strptime(stock_in_date_str, '%Y-%m-%d').date()
        except Exception:
            stock_in_date = date.today()

        stock_in = StockIn(
            project_id=project_id,
            code=_gen_stock_in_code(project_id),
            stock_in_date=stock_in_date,
            stock_in_type=request.form.get('stock_in_type', '采购入库'),
            contract_id=contract_id,
            supplier_id=supplier_id,
            operator=request.form.get('operator', '').strip() or None,
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(stock_in)
        db.session.flush()

        # 处理明细
        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        contract_item_ids = request.form.getlist('contract_item_id[]')

        total_qty = 0
        total_amount = 0
        total_estimated = 0
        total_actual = 0
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
                price = to_decimal(unit_prices[idx] if idx < len(unit_prices) else 0)
            except Exception:
                continue
            if qty == 0:
                continue
            amount = float(qty) * float(price)
            ci_id = contract_item_ids[idx] if idx < len(contract_item_ids) else None
            ci_id = int(ci_id) if ci_id and ci_id != '' else None

            if ci_id and float(price or 0) > 0:
                price_status = 'estimated'
            elif float(price or 0) > 0:
                price_status = 'confirmed'
            else:
                price_status = 'unpriced'

            si_item = StockInItem(
                stock_in_id=stock_in.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=price,
                amount=amount,
                contract_item_id=ci_id,
                price_status=price_status
            )
            db.session.add(si_item)
            total_qty = to_decimal(total_qty) + qty
            total_amount = to_decimal(total_amount) + to_decimal(amount)
            if price_status == 'estimated':
                total_estimated = to_decimal(total_estimated) + to_decimal(amount)
            elif price_status == 'confirmed':
                total_actual = to_decimal(total_actual) + to_decimal(amount)

        stock_in.total_quantity = total_qty
        stock_in.total_amount = total_amount
        stock_in.estimated_amount = total_estimated
        stock_in.actual_amount = total_actual

        # 超量检查（仅采购入库且关联合同）
        from flask_login import current_user
        force_over = request.form.get('force_over', '0') == '1'
        if stock_in.contract_id and stock_in.stock_in_type == '采购入库' and not force_over:
            over_items = []
            for item in stock_in.items:
                if not item.contract_item_id:
                    continue
                is_over, contract_qty, total_in_qty, remaining, over_ratio = check_contract_over(
                    contract_id, item.material_id, item.quantity, item.contract_item_id)
                if is_over:
                    mat = Material.query.get(item.material_id)
                    over_items.append({
                        'name': mat.name if mat else '未知物资',
                        'contract_qty': contract_qty,
                        'total_in_qty': total_in_qty,
                        'this_qty': float(item.quantity or 0),
                        'remaining': remaining,
                        'over_ratio': over_ratio
                    })
            if over_items:
                # 回滚事务
                db.session.rollback()
                # 重新获取数据渲染表单
                contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
                suppliers = get_project_suppliers(project_id, common_only=True).all()
                materials = get_project_materials(project_id, common_only=True).all()
                is_admin = current_user.is_admin() if hasattr(current_user, 'is_admin') else False
                from app.approval.service import is_approval_enabled
                return render_template('stock_in/form.html', stock_in=None, contracts=contracts,
                                       suppliers=suppliers, materials=materials,
                                       default_code=_gen_stock_in_code(project_id),
                                       over_items=over_items, form_data=request.form,
                                       is_admin=is_admin,
                                       approval_enabled=is_approval_enabled('stockin', project_id=project_id),
                                       ai_vision_enabled=_ai_vision_enabled())

        # 强制超量入库时标记明细行
        if stock_in.contract_id and stock_in.stock_in_type == '采购入库' and force_over:
            for item in stock_in.items:
                if not item.contract_item_id:
                    continue
                is_over, _, _, _, _ = check_contract_over(
                    contract_id, item.material_id, item.quantity, item.contract_item_id)
                if is_over:
                    item.is_over_contract = True

        from app.approval.service import is_approval_enabled
        from app.utils import ConfigCache
        enable_qc = ConfigCache.get('enable_quality_check') == 'true'
        action = request.form.get('action', 'save')

        if action == 'save':
            stock_in.status = 'draft'
            stock_in.approval_status = 'draft'
            if enable_qc:
                stock_in.quality_status = 'pending'
            else:
                stock_in.quality_status = 'passed'
        elif action == 'submit':
            if not supplier_id:
                db.session.rollback()
                flash('请选择供应商。', 'danger')
                contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
                suppliers = get_project_suppliers(project_id, common_only=True).all()
                materials = get_project_materials(project_id, common_only=True).all()
                is_admin = current_user.is_admin() if hasattr(current_user, 'is_admin') else False
                from app.approval.service import is_approval_enabled
                return render_template('stock_in/form.html', stock_in=None, contracts=contracts,
                                       suppliers=suppliers, materials=materials,
                                       default_code=_gen_stock_in_code(project_id),
                                       form_data=request.form, is_admin=is_admin,
                                       approval_enabled=is_approval_enabled('stockin', project_id=project_id),
                                       ai_vision_enabled=_ai_vision_enabled())
            if not stock_in.items:
                db.session.rollback()
                flash('请至少添加一条物资明细。', 'danger')
                contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
                suppliers = get_project_suppliers(project_id, common_only=True).all()
                materials = get_project_materials(project_id, common_only=True).all()
                is_admin = current_user.is_admin() if hasattr(current_user, 'is_admin') else False
                from app.approval.service import is_approval_enabled
                return render_template('stock_in/form.html', stock_in=None, contracts=contracts,
                                       suppliers=suppliers, materials=materials,
                                       default_code=_gen_stock_in_code(project_id),
                                       form_data=request.form, is_admin=is_admin,
                                       approval_enabled=is_approval_enabled('stockin', project_id=project_id),
                                       ai_vision_enabled=_ai_vision_enabled())
            stock_in.status = 'pending'
            stock_in.approval_status = 'pending'
            db.session.commit()

            from app.approval.service import submit_approval, is_project_approval_enabled
            success, message, instance = submit_approval('stockin', stock_in.id, project_id=project_id)
            if success:
                flash('入库单已提交审批。', 'success')
            elif is_project_approval_enabled(project_id) is False or '已关闭审批模块' in message:
                # 项目关闭了审批模块，单据直接生效
                stock_in.status = 'approved'
                stock_in.approval_status = 'passed'
                stock_in.quality_status = 'passed'
                if stock_in.stock_in_type in ('采购入库', '盘盈入库', '调拨入库'):
                    _apply_inventory(stock_in, 1)
                    _update_contract_total_in(stock_in, 1)
                elif stock_in.stock_in_type == '退货入库':
                    _apply_inventory(stock_in, -1)
                    _update_contract_total_in(stock_in, -1)
                db.session.commit()
                flash('入库单已直接生效（项目未启用审批模块）。', 'success')
            else:
                flash(message, 'danger')
            return redirect(url_for('stock_in.detail', id=stock_in.id))
        elif action == 'approve_save':
            stock_in.status = 'approved'
            stock_in.approval_status = 'passed'
            stock_in.quality_status = 'passed'
            if stock_in.stock_in_type in ('采购入库', '盘盈入库', '调拨入库'):
                _apply_inventory(stock_in, 1)
                _update_contract_total_in(stock_in, 1)
            elif stock_in.stock_in_type == '退货入库':
                _apply_inventory(stock_in, -1)
                _update_contract_total_in(stock_in, -1)

        db.session.commit()
        flash('入库单创建成功。', 'success')
        if enable_qc and stock_in.quality_status == 'pending':
            flash('入库单已提交，等待质量验收。', 'info')
        elif stock_in.approval_status == 'draft':
            flash('当前入库单为草稿状态，请提交审批后生效。', 'info')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    contracts = Contract.query.filter_by(project_id=project_id).order_by(Contract.code).all()
    suppliers = get_project_suppliers(project_id, common_only=True).all()
    materials = get_project_materials(project_id, common_only=True).all()

    # 复制新增
    copy_from_id = request.args.get('copy_from', type=int)
    copy_stock_in = None
    if copy_from_id:
        src = StockIn.query.get(copy_from_id)
        if src and src.project_id == project_id:
            copy_stock_in = src

    from app.approval.service import is_approval_enabled
    return render_template('stock_in/form.html', stock_in=copy_stock_in, contracts=contracts,
                           suppliers=suppliers, materials=materials,
                           default_code=_gen_stock_in_code(project_id),
                           is_copy=bool(copy_stock_in),
                           approval_enabled=is_approval_enabled('stockin', project_id=project_id),
                           ai_vision_enabled=_ai_vision_enabled())


@bp.route('/<int:id>')
@login_required
def detail(id):
    stock_in = StockIn.query.filter(StockIn.status != 'voided').filter_by(id=id).first_or_404()
    from app.approval.service import get_instance_by_biz
    from app.utils import ConfigCache
    approval_instance = get_instance_by_biz('stockin', stock_in.id)
    enable_quality_check = ConfigCache.get('enable_quality_check') == 'true'
    change_logs = get_change_logs('stock_ins', stock_in.id)
    return render_template('stock_in/detail.html', stock_in=stock_in,
                           approval_instance=approval_instance,
                           enable_quality_check=enable_quality_check,
                           change_logs=change_logs, get_field_label=get_field_label)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='编辑')
def edit(id):
    from app.utils import reject_in_all_projects_mode
    if reject_in_all_projects_mode():
        flash('汇总视图下不可编辑，请先切换到具体项目', 'warning')
        return redirect(url_for('stock_in.index'))
    stock_in = StockIn.query.get_or_404(id)
    if stock_in.is_reconciled:
        flash('已对账的入库单禁止编辑。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    if stock_in.status not in ('draft', 'rejected'):
        flash('仅草稿或已驳回状态的单据可编辑。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    from app.utils import ConfigCache
    enable_qc = ConfigCache.get('enable_quality_check') == 'true'
    if enable_qc and stock_in.quality_status in ('pending', 'passed'):
        flash('待检或质检合格的入库单不能直接编辑，请联系质检员处理。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    if request.method == 'POST':
        from flask_login import current_user
        old_data = model_to_dict(stock_in)

        # 先回滚原库存和合同累计入库量
        if stock_in.quality_status == 'passed' or (not enable_qc and stock_in.approval_status == 'passed'):
            if stock_in.stock_in_type in ('采购入库', '盘盈入库', '调拨入库'):
                _apply_inventory(stock_in, -1)
                _update_contract_total_in(stock_in, -1)
            elif stock_in.stock_in_type == '退货入库':
                _apply_inventory(stock_in, 1)
                _update_contract_total_in(stock_in, 1)

        # 删除原明细
        for item in list(stock_in.items):
            db.session.delete(item)
        db.session.flush()

        contract_id = request.form.get('contract_id', type=int) or None
        supplier_id = request.form.get('supplier_id', type=int) or None
        if contract_id and not supplier_id:
            contract = Contract.query.get(contract_id)
            if contract:
                supplier_id = contract.supplier_id

        stock_in_date_str = request.form.get('stock_in_date')
        try:
            stock_in.stock_in_date = datetime.strptime(stock_in_date_str, '%Y-%m-%d').date()
        except Exception:
            stock_in.stock_in_date = date.today()
        stock_in.stock_in_type = request.form.get('stock_in_type', '采购入库')
        stock_in.contract_id = contract_id
        stock_in.supplier_id = supplier_id
        stock_in.operator = request.form.get('operator', '').strip() or None
        stock_in.remark = request.form.get('remark', '').strip()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        contract_item_ids = request.form.getlist('contract_item_id[]')

        total_qty = 0
        total_amount = 0
        total_estimated = 0
        total_actual = 0
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
                price = to_decimal(unit_prices[idx] if idx < len(unit_prices) else 0)
            except Exception:
                continue
            if qty == 0:
                continue
            amount = float(qty) * float(price)
            ci_id = contract_item_ids[idx] if idx < len(contract_item_ids) else None
            ci_id = int(ci_id) if ci_id and ci_id != '' else None

            if ci_id and float(price or 0) > 0:
                price_status = 'estimated'
            elif float(price or 0) > 0:
                price_status = 'confirmed'
            else:
                price_status = 'unpriced'

            si_item = StockInItem(
                stock_in_id=stock_in.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=price,
                amount=amount,
                contract_item_id=ci_id,
                price_status=price_status
            )
            db.session.add(si_item)
            total_qty = to_decimal(total_qty) + qty
            total_amount = to_decimal(total_amount) + to_decimal(amount)
            if price_status == 'estimated':
                total_estimated = to_decimal(total_estimated) + to_decimal(amount)
            elif price_status == 'confirmed':
                total_actual = to_decimal(total_actual) + to_decimal(amount)

        stock_in.total_quantity = total_qty
        stock_in.total_amount = total_amount
        stock_in.estimated_amount = total_estimated
        stock_in.actual_amount = total_actual

        action = request.form.get('action', 'save')
        if action == 'save':
            stock_in.status = 'draft'
            stock_in.approval_status = 'draft'
            if enable_qc:
                stock_in.quality_status = 'pending'
                stock_in.quality_checker = None
                stock_in.quality_check_time = None
                stock_in.quality_remark = None
            else:
                stock_in.quality_status = 'passed'
        elif action == 'submit':
            if not supplier_id:
                db.session.rollback()
                flash('请选择供应商。', 'danger')
                return redirect(url_for('stock_in.edit', id=stock_in.id))
            if not stock_in.items:
                db.session.rollback()
                flash('请至少添加一条物资明细。', 'danger')
                return redirect(url_for('stock_in.edit', id=stock_in.id))
            stock_in.status = 'pending'
            stock_in.approval_status = 'pending'
            new_data = model_to_dict(stock_in)
            record_changes('stock_ins', stock_in.id, old_data, new_data,
                           changed_by=current_user.name or current_user.username)
            db.session.commit()

            from app.approval.service import submit_approval, is_project_approval_enabled
            success, message, instance = submit_approval('stockin', stock_in.id, project_id=stock_in.project_id)
            if success:
                flash('入库单已提交审批。', 'success')
            elif is_project_approval_enabled(stock_in.project_id) is False or '已关闭审批模块' in message:
                stock_in.status = 'approved'
                stock_in.approval_status = 'passed'
                stock_in.quality_status = 'passed'
                if stock_in.stock_in_type in ('采购入库', '盘盈入库', '调拨入库'):
                    _apply_inventory(stock_in, 1)
                    _update_contract_total_in(stock_in, 1)
                elif stock_in.stock_in_type == '退货入库':
                    _apply_inventory(stock_in, -1)
                    _update_contract_total_in(stock_in, -1)
                db.session.commit()
                flash('入库单已直接生效（项目未启用审批模块）。', 'success')
            else:
                flash(message, 'danger')
            return redirect(url_for('stock_in.detail', id=stock_in.id))
        elif action == 'approve_save':
            stock_in.status = 'approved'
            stock_in.approval_status = 'passed'
            stock_in.quality_status = 'passed'
            if stock_in.stock_in_type in ('采购入库', '盘盈入库', '调拨入库'):
                _apply_inventory(stock_in, 1)
                _update_contract_total_in(stock_in, 1)
            elif stock_in.stock_in_type == '退货入库':
                _apply_inventory(stock_in, -1)
                _update_contract_total_in(stock_in, -1)

        new_data = model_to_dict(stock_in)
        record_changes('stock_ins', stock_in.id, old_data, new_data,
                       changed_by=current_user.name or current_user.username)

        db.session.commit()
        flash('入库单更新成功。', 'success')
        if enable_qc and action == 'save':
            flash('已重新提交质量验收。', 'info')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    contracts = Contract.query.filter_by(project_id=stock_in.project_id).order_by(Contract.code).all()
    suppliers = get_project_suppliers(stock_in.project_id, common_only=True).all()
    materials = get_project_materials(stock_in.project_id, common_only=True).all()
    from app.approval.service import is_approval_enabled
    return render_template('stock_in/form.html', stock_in=stock_in, contracts=contracts,
                           suppliers=suppliers, materials=materials,
                           approval_enabled=is_approval_enabled('stockin', project_id=stock_in.project_id),
                           ai_vision_enabled=_ai_vision_enabled())


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='作废')
def delete(id):
    from app.utils import reject_in_all_projects_mode
    if reject_in_all_projects_mode():
        flash('汇总视图下不可删除，请先切换到具体项目', 'warning')
        return redirect(url_for('stock_in.index'))
    stock_in = StockIn.query.get_or_404(id)
    if stock_in.is_reconciled:
        flash('已对账的入库单禁止作废。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    if stock_in.status != 'draft':
        flash('仅草稿状态的单据可作废。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    from app.utils import ConfigCache
    enable_qc = ConfigCache.get('enable_quality_check') == 'true'
    if enable_qc and stock_in.quality_status == 'passed':
        flash('质检合格的入库单禁止作废。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    # 仅在已生效的才回滚库存
    if stock_in.status == 'approved' or stock_in.quality_status == 'passed' or (not enable_qc and stock_in.approval_status == 'passed'):
        if stock_in.stock_in_type in ('采购入库', '盘盈入库', '调拨入库'):
            _apply_inventory(stock_in, -1)
            _update_contract_total_in(stock_in, -1)
        elif stock_in.stock_in_type == '退货入库':
            _apply_inventory(stock_in, 1)
            _update_contract_total_in(stock_in, 1)

    stock_in.is_deleted = True
    stock_in.status = 'voided'
    db.session.commit()
    flash('入库单已作废。', 'success')
    return redirect(url_for('stock_in.index'))


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='提交审批')
def submit(id):
    """将草稿提交审批"""
    stock_in = StockIn.query.get_or_404(id)
    if stock_in.status != 'draft':
        flash('仅草稿状态可提交审批。', 'warning')
        return redirect(url_for('stock_in.detail', id=id))
    if stock_in.is_reconciled:
        flash('已对账的入库单禁止提交审批。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    from app.utils import ConfigCache
    enable_qc = ConfigCache.get('enable_quality_check') == 'true'
    if enable_qc and stock_in.quality_status != 'passed':
        flash('请先完成质量验收后再提交审批。', 'warning')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    from app.approval.service import submit_approval, is_project_approval_enabled
    success, message, instance = submit_approval('stockin', stock_in.id, project_id=stock_in.project_id)
    if success:
        flash('已提交审批。', 'success')
    elif is_project_approval_enabled(stock_in.project_id) is False or '已关闭审批模块' in message:
        stock_in.status = 'approved'
        stock_in.approval_status = 'passed'
        stock_in.quality_status = 'passed'
        if stock_in.stock_in_type in ('采购入库', '盘盈入库', '调拨入库'):
            _apply_inventory(stock_in, 1)
            _update_contract_total_in(stock_in, 1)
        elif stock_in.stock_in_type == '退货入库':
            _apply_inventory(stock_in, -1)
            _update_contract_total_in(stock_in, -1)
        db.session.commit()
        flash('入库单已直接生效（项目未启用审批模块）。', 'success')
    else:
        flash(message, 'danger')
    return redirect(url_for('stock_in.detail', id=stock_in.id))


@bp.route('/<int:id>/withdraw', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='撤回审批')
def withdraw(id):
    """撤回审批（回到草稿）"""
    stock_in = StockIn.query.get_or_404(id)
    if stock_in.status != 'pending':
        flash('仅待审批状态可撤回。', 'warning')
        return redirect(url_for('stock_in.detail', id=id))

    from app.approval.service import get_instance_by_biz, withdraw as approval_withdraw
    instance = get_instance_by_biz('stockin', stock_in.id)
    if not instance or instance.status not in ('pending', 'approving'):
        flash('未找到有效的审批实例。', 'warning')
        return redirect(url_for('stock_in.detail', id=id))

    success, message = approval_withdraw(instance.id)
    if success:
        flash('已撤回审批。', 'success')
    else:
        flash(message, 'danger')
    return redirect(url_for('stock_in.detail', id=id))


@bp.route('/<int:id>/restore', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='恢复')
def restore(id):
    stock_in = StockIn.query.get_or_404(id)
    stock_in.is_deleted = False
    db.session.commit()
    flash('入库单已恢复。', 'success')
    return redirect(url_for('stock_in.index'))


@bp.route('/<int:id>/quality_pass', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='质检合格')
def quality_pass(id):
    """质检合格"""
    stock_in = StockIn.query.get_or_404(id)
    from app.utils import ConfigCache
    enable_qc = ConfigCache.get('enable_quality_check') == 'true'
    if not enable_qc:
        flash('未启用入库质检流程。', 'warning')
        return redirect(url_for('stock_in.detail', id=stock_in.id))
    if stock_in.quality_status != 'pending':
        flash('当前入库单不是待检状态，无法进行质检。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    from flask_login import current_user
    stock_in.quality_status = 'passed'
    stock_in.quality_checker = current_user.name or current_user.username
    stock_in.quality_check_time = datetime.now()
    stock_in.quality_remark = request.form.get('quality_remark', '').strip() or None

    from app.approval.service import is_approval_enabled
    if not is_approval_enabled('stockin', project_id=stock_in.project_id):
        stock_in.approval_status = 'passed'
        if stock_in.stock_in_type in ('采购入库', '盘盈入库', '调拨入库'):
            _apply_inventory(stock_in, 1)
            _update_contract_total_in(stock_in, 1)
        elif stock_in.stock_in_type == '退货入库':
            _apply_inventory(stock_in, -1)
            _update_contract_total_in(stock_in, -1)

    db.session.commit()
    flash('质检合格。', 'success')
    if not is_approval_enabled('stockin', project_id=stock_in.project_id):
        flash('入库单已生效，库存已更新。', 'info')
    else:
        flash('请提交审批以完成入库。', 'info')
    return redirect(url_for('stock_in.detail', id=stock_in.id))


@bp.route('/<int:id>/quality_reject', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='质检退回')
def quality_reject(id):
    """质检退回"""
    stock_in = StockIn.query.get_or_404(id)
    from app.utils import ConfigCache
    enable_qc = ConfigCache.get('enable_quality_check') == 'true'
    if not enable_qc:
        flash('未启用入库质检流程。', 'warning')
        return redirect(url_for('stock_in.detail', id=stock_in.id))
    if stock_in.quality_status != 'pending':
        flash('当前入库单不是待检状态，无法进行质检。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    from flask_login import current_user
    stock_in.quality_status = 'rejected'
    stock_in.quality_checker = current_user.name or current_user.username
    stock_in.quality_check_time = datetime.now()
    stock_in.quality_remark = request.form.get('quality_remark', '').strip() or None

    db.session.commit()
    flash('质检已退回。', 'warning')
    return redirect(url_for('stock_in.detail', id=stock_in.id))


@bp.route('/<int:id>/quality_resubmit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='重新提交质检')
def quality_resubmit(id):
    """重新提交质检"""
    stock_in = StockIn.query.get_or_404(id)
    from app.utils import ConfigCache
    enable_qc = ConfigCache.get('enable_quality_check') == 'true'
    if not enable_qc:
        flash('未启用入库质检流程。', 'warning')
        return redirect(url_for('stock_in.detail', id=stock_in.id))
    if stock_in.quality_status not in ('rejected', 'draft'):
        flash('当前状态无法重新提交质检。', 'danger')
        return redirect(url_for('stock_in.detail', id=stock_in.id))

    stock_in.quality_status = 'pending'
    stock_in.quality_checker = None
    stock_in.quality_check_time = None
    stock_in.quality_remark = None

    db.session.commit()
    flash('已重新提交质量验收。', 'info')
    return redirect(url_for('stock_in.detail', id=stock_in.id))


@bp.route('/<int:id>/print')
@login_required
def print_stock_in(id):
    """打印入库单"""
    import time as _time
    _start = _time.time()
    stock_in = StockIn.query.get_or_404(id)
    now_date = datetime.now().strftime('%Y-%m-%d')
    try:
        result = render_template('stock_in/print.html', stock_in=stock_in, now_date=now_date)
        BusinessLogger.log_print(
            module='stock_in', doc_id=stock_in.id, doc_code=stock_in.code,
            status='success', duration_ms=int((_time.time() - _start) * 1000)
        )
        return result
    except Exception as e:
        BusinessLogger.log_print(
            module='stock_in', doc_id=stock_in.id, doc_code=stock_in.code,
            status='fail', error=str(e), duration_ms=int((_time.time() - _start) * 1000)
        )
        raise


@bp.route('/export')
@login_required
def export():
    """导出入库单列表Excel"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('stock_in.index'))
    
    keyword = request.args.get('keyword', '')
    query = StockIn.query.filter_by(project_id=project_id)
    if keyword:
        query = query.filter(StockIn.code.contains(keyword))
    
    stock_ins = query.order_by(StockIn.stock_in_date.desc()).all()
    
    headers = ['序号', '入库单号', '入库日期', '入库类型', '供应商', '合同', '总数量', '总金额', '经办人']
    rows = []
    for idx, si in enumerate(stock_ins, 1):
        rows.append([
            idx, si.code, str(si.stock_in_date or ''), si.stock_in_type,
            si.supplier.name if si.supplier else '', si.contract.code if si.contract else '',
            float(si.total_quantity or 0), float(si.total_amount or 0), si.operator or ''
        ])
    
    from datetime import datetime
    from app.utils import export_to_excel
    filename = f'入库单列表_{datetime.now().strftime("%Y%m%d")}.xlsx'
    data = export_to_excel(headers, rows, '入库单列表', filename)
    
    return Response(data, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})


# ---------------- API ----------------
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
        'supplier_id': c.supplier_id
    } for c in contracts])


@bp.route('/api/materials_by_project')
@login_required
def api_materials_by_project():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])
    materials = get_project_materials(project_id, common_only=True).all()
    return jsonify([{
        'id': m.id,
        'name': m.name,
        'code': m.code or '',
        'specification': m.specification or '',
        'unit': m.unit,
        'category_id': m.category_id
    } for m in materials])


@bp.route('/api/contract/<int:id>/items')
@login_required
def api_contract_items(id):
    items = ContractItem.query.filter_by(contract_id=id).all()
    result = []
    for it in items:
        m = Material.query.get(it.material_id)
        if not m:
            continue
        result.append({
            'contract_item_id': it.id,
            'material_id': m.id,
            'material_name': m.name,
            'specification': m.specification or '',
            'unit': m.unit,
            'unit_price_with_tax': float(it.unit_price_with_tax or 0)
        })
    return jsonify(result)


def _calc_historical_avg_price(project_id, material_id, months=3):
    """计算某物资最近N个月已对账入库加权平均价"""
    from datetime import timedelta
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=months * 30)

    result = db.session.query(
        func.coalesce(func.sum(StockInItem.quantity), 0),
        func.coalesce(func.sum(StockInItem.amount), 0)
    ).join(StockIn, StockInItem.stock_in_id == StockIn.id).filter(
        StockIn.project_id == project_id,
        StockInItem.material_id == material_id,
        StockIn.stock_in_date >= start_date,
        StockIn.stock_in_date <= end_date,
        StockIn.is_reconciled == True,
        StockIn.approval_status == 'passed'
    ).first()

    total_qty = float(result[0] or 0)
    total_amt = float(result[1] or 0)
    if total_qty <= 0:
        return None
    return round(total_amt / total_qty, 4)


@bp.route('/api/price_check')
@login_required
def api_price_check():
    """API：单价异常校验"""
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'error': '未选择项目'}), 400

    material_id = request.args.get('material_id', type=int)
    unit_price = request.args.get('unit_price', type=float) or 0

    if not material_id or unit_price <= 0:
        return jsonify({'enabled': False})

    from app.utils import ConfigCache
    enable_check = ConfigCache.get('enable_price_check') == 'true'
    if not enable_check:
        return jsonify({'enabled': False})

    avg_price = _calc_historical_avg_price(project_id, material_id)
    if avg_price is None or avg_price <= 0:
        return jsonify({'enabled': True, 'has_history': False})

    threshold = float(ConfigCache.get('price_deviation_threshold', '20'))
    force_block = ConfigCache.get('price_check_force_block') == 'true'

    deviation = (unit_price - avg_price) / avg_price * 100
    abs_deviation = abs(deviation)

    result = {
        'enabled': True,
        'has_history': True,
        'avg_price': avg_price,
        'current_price': unit_price,
        'deviation': round(deviation, 2),
        'threshold': threshold,
        'force_block': force_block,
        'is_abnormal': abs_deviation > threshold,
        'direction': 'high' if deviation > 0 else 'low'
    }

    if abs_deviation > threshold:
        result['message'] = f'该物资历史平均单价{avg_price}元，当前单价{unit_price}元，偏差{round(deviation, 2)}%，请确认'

    return jsonify(result)


@bp.route('/batch_delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_in', operation='批量作废')
def batch_delete():
    project_id = request.form.get('project_id', type=int)
    if not project_id:
        project_id = session.get('current_project_id')
    ids = request.form.get('ids', '')
    id_list = [int(x) for x in ids.split(',') if x.strip().isdigit()]
    if not id_list:
        flash('请选择要作废的入库单。', 'warning')
        return redirect(url_for('stock_in.index'))

    success_count = 0
    fail_count = 0
    for sid in id_list:
        stock_in = StockIn.query.get(sid)
        if not stock_in or stock_in.project_id != project_id:
            fail_count += 1
            continue
        if stock_in.is_reconciled:
            fail_count += 1
            continue
        if stock_in.status != 'draft':
            fail_count += 1
            continue
        try:
            if stock_in.status == 'approved' or stock_in.quality_status == 'passed' or (stock_in.approval_status == 'passed' and not stock_in.stock_in_type):
                _apply_inventory(stock_in, -1)
                _update_contract_total_in(stock_in, -1)
            elif stock_in.approval_status == 'passed' and stock_in.stock_in_type == '退货入库':
                _apply_inventory(stock_in, 1)
                _update_contract_total_in(stock_in, 1)
            stock_in.is_deleted = True
            stock_in.status = 'voided'
            success_count += 1
        except Exception:
            db.session.rollback()
            fail_count += 1

    db.session.commit()
    if fail_count > 0:
        flash(f'批量作废完成：成功{success_count}条，失败{fail_count}条（可能已对账/非草稿状态）。', 'warning')
    else:
        flash(f'批量作废成功，共{success_count}条。', 'success')
    return redirect(url_for('stock_in.index'))


@bp.route('/batch_export')
@login_required
def batch_export():
    from io import BytesIO
    from openpyxl import Workbook
    project_id = session.get('current_project_id')
    ids_str = request.args.get('ids', '')
    id_list = [int(x) for x in ids_str.split(',') if x.strip().isdigit()]

    query = StockIn.query.filter_by(project_id=project_id)
    if id_list:
        query = query.filter(StockIn.id.in_(id_list))
    stock_ins = query.order_by(StockIn.code).all()

    wb = Workbook()
    ws = wb.active
    ws.title = '入库单'
    headers = ['入库单号', '入库日期', '入库类型', '合同号', '供应商', '经办人', '总数量', '总金额', '状态']
    ws.append(headers)
    for s in stock_ins:
        ws.append([
            s.code,
            str(s.stock_in_date or ''),
            s.stock_in_type or '',
            s.contract.code if s.contract else '',
            s.supplier.name if s.supplier else '',
            s.operator or '',
            float(s.total_quantity or 0),
            float(s.total_amount or 0),
            s.approval_status or ''
        ])

    from flask import make_response
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = f'attachment; filename=stock_ins_{datetime.now().strftime("%Y%m%d%H%M%S")}.xlsx'
    return resp
