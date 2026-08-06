from datetime import datetime, date
from decimal import Decimal
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, func

from app.stock_out import bp
from app import db
from app.models import (StockOut, StockOutItem, Inventory, Material, UsageUnit,
                       WorkNumber, Project, UnitTeam)
from app.decorators import editor_required, log_audit
from app.utils import to_decimal, apply_data_scope, get_project_materials
from app.services.inventory_cost import InventoryCostService
from app.services.business_logger import BusinessLogger


ALLOW_NEGATIVE_STOCK = False


def _allow_negative_stock(project_id, material_id):
    """判断某物资是否允许负库存（按分类逐级向上查找）"""
    from app.utils import ConfigCache
    global_setting = ConfigCache.get('allow_negative_stock') == 'true'

    mat = Material.query.get(material_id)
    if not mat or not mat.category_id:
        return global_setting

    cat = mat.category
    current = cat
    while current:
        if current.negative_stock_policy == 'allow':
            return True
        if current.negative_stock_policy == 'forbid':
            return False
        if current.parent_id is not None:
            current = current.parent
        else:
            break

    return global_setting


def _gen_stock_out_code(project_id):
    """生成唯一出库单号

    委托 utils._gen_code_with_seq 处理,内置进程锁+重试机制避免并发产生重复单号。
    """
    from app.utils import _gen_code_with_seq
    return _gen_code_with_seq('CK', project_id, StockOut)




def _allocate_fifo_batches(project_id, material_id, quantity):
    """P2: 按FIFO(先进先出)自动分配批次

    返回: [(batch_no, allocated_qty), ...] 列表
    """
    from app.models import InventoryBatch
    remaining = float(quantity)
    allocations = []

    # 按入库日期升序查询有库存的批次
    batches = InventoryBatch.query.filter(
        InventoryBatch.project_id == project_id,
        InventoryBatch.material_id == material_id,
        InventoryBatch.quantity > 0
    ).order_by(
        InventoryBatch.stock_in_date.asc(),
        InventoryBatch.created_at.asc()
    ).all()

    for batch in batches:
        if remaining <= 0:
            break
        available = float(batch.quantity)
        if available <= 0:
            continue
        alloc = min(available, remaining)
        allocations.append((batch.batch_no, alloc))
        remaining -= alloc

    # 如果批次不够，返回未分配的剩余量（不指定批次）
    if remaining > 0.001:
        allocations.append((None, remaining))

    return allocations


def _process_transfer_in(stock_out):
    """P2: 调拨出库审批通过后，自动在目标项目创建入库单"""
    from app.models import StockIn, StockInItem, Project
    from datetime import date as date_cls

    if not stock_out.destination_project_id:
        return False, "未设置调拨目标项目"

    dest_project = Project.query.get(stock_out.destination_project_id)
    if not dest_project:
        return False, "调拨目标项目不存在"

    # 检查是否已创建过调拨入库（防止重复）
    existing = StockIn.query.filter(
        StockIn.remark.contains(f"调拨入库:{stock_out.code}"),
        StockIn.project_id == stock_out.destination_project_id
    ).first()
    if existing:
        return False, "调拨入库单已存在"

    # 生成入库单号
    from app.stock_in.routes import _gen_stock_in_code
    try:
        in_code = _gen_stock_in_code(stock_out.destination_project_id)
    except Exception:
        in_code = f"TR-IN-{stock_out.code}"

    stock_in = StockIn(
        project_id=stock_out.destination_project_id,
        code=in_code,
        stock_in_date=date_cls.today(),
        stock_in_type='调拨入库',
        operator=stock_out.operator,
        remark=f"调拨入库:{stock_out.code}"
    )
    db.session.add(stock_in)
    db.session.flush()

    total_qty = 0
    total_amount = 0
    for out_item in stock_out.items:
        in_item = StockInItem(
            stock_in_id=stock_in.id,
            material_id=out_item.material_id,
            quantity=out_item.quantity,
            unit_price=out_item.unit_price,
            amount=out_item.amount,
            batch_no=out_item.batch_no
        )
        db.session.add(in_item)
        total_qty += float(out_item.quantity)
        total_amount += float(out_item.amount)

        # 增加目标项目库存
        _apply_stock(stock_out.destination_project_id, out_item.material_id, float(out_item.quantity))

    stock_in.total_quantity = total_qty
    stock_in.total_amount = total_amount
    stock_in.status = 'approved'
    stock_in.approval_status = 'passed'

    db.session.flush()
    return True, f"调拨入库单 {in_code} 已创建"


def _get_current_stock(project_id, material_id):
    inv = Inventory.query.filter_by(project_id=project_id, material_id=material_id).first()
    return float(inv.quantity) if inv else 0


def _apply_stock(project_id, material_id, quantity):
    """扣减库存,同时按加权平均法扣减库存金额

    统一委托 InventoryCostService 处理,保证:
    - 行级锁避免并发更新丢失
    - Decimal 精度避免浮点误差
    - 加权平均成本扣减,金额与数量同步
    - 全链路业务日志,便于偶现问题定位

    quantity 符号约定: 正数=退库回滚(增加库存), 负数=出库(扣减库存)
    """
    qty_decimal = to_decimal(quantity)

    # 负库存校验(在调用服务前进行,失败时给出明确提示)
    inv_before = Inventory.query.filter_by(
        project_id=project_id, material_id=material_id
    ).first()
    before_qty = to_decimal(inv_before.quantity if inv_before else 0)
    before_amount = (to_decimal(inv_before.actual_amount if inv_before else 0)
                     + to_decimal(inv_before.estimated_amount if inv_before else 0))

    new_qty = before_qty + qty_decimal
    if not _allow_negative_stock(project_id, material_id) and new_qty < 0:
        raise ValueError(
            f"库存不足，当前库存 {float(before_qty)}，出库 {abs(float(quantity))}"
        )

    if qty_decimal < 0:
        # 出库: 使用统一服务进行加权平均扣减
        InventoryCostService.apply_outbound(
            project_id=project_id,
            material_id=material_id,
            quantity=abs(qty_decimal)
        )
        change_type = 'outbound'
    elif qty_decimal > 0:
        # 出库撤销/退库回滚: 数量加回,金额按当前加权平均价估算
        # 注意: 此场景在撤销出库单时使用,库存数量恢复,
        # 金额也按当前加权平均价恢复(避免金额丢失)
        avg_price = InventoryCostService.get_weighted_average_price(
            project_id=project_id, material_id=material_id
        )
        restore_amount = avg_price * qty_decimal
        InventoryCostService.apply_inbound(
            project_id=project_id,
            material_id=material_id,
            quantity=qty_decimal,
            amount=restore_amount,
            price_status='estimated'
        )
        change_type = 'outbound_reverse'
    else:
        return float(before_qty)

    # 记录变更后快照
    inv_after = Inventory.query.filter_by(
        project_id=project_id, material_id=material_id
    ).first()
    after_qty = to_decimal(inv_after.quantity if inv_after else 0)
    after_amount = (to_decimal(inv_after.actual_amount if inv_after else 0)
                    + to_decimal(inv_after.estimated_amount if inv_after else 0))

    BusinessLogger.log_inventory_change(
        project_id=project_id, material_id=material_id,
        change_type=change_type,
        before_qty=before_qty, after_qty=after_qty,
        before_amount=before_amount, after_amount=after_amount,
        reason=f"出库扣减 {abs(float(quantity))}"
    )

    return float(after_qty)


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
    usage_unit_id = request.args.get('usage_unit_id', 0, type=int)
    stock_out_type = request.args.get('stock_out_type', '', type=str)
    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)
    status = request.args.get('status', '', type=str)
    approval_status = request.args.get('approval_status', '', type=str)

    query = StockOut.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    query = apply_data_scope(query, StockOut)
    query = query.filter(StockOut.status != 'voided')
    if keyword:
        query = query.filter(or_(StockOut.code.contains(keyword), StockOut.remark.contains(keyword)))
    if usage_unit_id:
        query = query.filter_by(usage_unit_id=usage_unit_id)
    if stock_out_type:
        query = query.filter_by(stock_out_type=stock_out_type)
    if status:
        query = query.filter_by(status=status)
    if approval_status:
        query = query.filter_by(approval_status=approval_status)
    if date_from:
        try:
            query = query.filter(StockOut.stock_out_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            query = query.filter(StockOut.stock_out_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass

    # P2: 预加载关联数据，避免 N+1 查询
    query = query.options(
        joinedload(StockOut.usage_unit)
    )
    pagination = query.order_by(StockOut.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)

    usage_units = UsageUnit.query.filter_by(project_id=project_id).order_by(UsageUnit.name).all()
    return render_template('stock_out/index.html', pagination=pagination, keyword=keyword,
                           usage_units=usage_units, usage_unit_id=usage_unit_id,
                           stock_out_type=stock_out_type, date_from=date_from, date_to=date_to,
                           status=status, approval_status=approval_status)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='stock_out', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        usage_unit_id = request.form.get('usage_unit_id', type=int) or None
        work_number_id = request.form.get('work_number_id', type=int) or None
        team_id = request.form.get('team_id', type=int) or None

        stock_out_date_str = request.form.get('stock_out_date')
        try:
            stock_out_date = datetime.strptime(stock_out_date_str, '%Y-%m-%d').date()
        except Exception:
            stock_out_date = date.today()

        stock_out_type = request.form.get('stock_out_type', '工程领用')
        dest_project_id = request.form.get('destination_project_id', type=int) if stock_out_type == '调拨出库' else None

        stock_out = StockOut(
            project_id=project_id,
            code=_gen_stock_out_code(project_id),
            stock_out_date=stock_out_date,
            stock_out_type=stock_out_type,
            destination_project_id=dest_project_id,
            usage_unit_id=usage_unit_id,
            work_number_id=work_number_id,
            team_id=team_id,
            issue_location=request.form.get('issue_location', '').strip(),
            purpose=request.form.get('purpose', '').strip(),
            operator=request.form.get('operator', '').strip() or current_user.name or current_user.username,
            remark=request.form.get('remark', '').strip()
        )
        db.session.add(stock_out)
        db.session.flush()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')

        total_qty = 0
        total_amount = 0
        errors = []

        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
                price = to_decimal(unit_prices[idx] if idx < len(unit_prices) else 0)
            except Exception:
                continue
            if qty <= 0:
                continue

            current_stock = _get_current_stock(project_id, int(mid))
            if float(qty) > current_stock and not ALLOW_NEGATIVE_STOCK:
                m = Material.query.get(mid)
                errors.append(f"{m.name if m else '物资'}库存不足（当前{current_stock}，出库{float(qty)}）")
                continue
            # === P2: 限额领料控制 ===
            if work_number_id:
                from app.models import MaterialQuota
                quota = MaterialQuota.query.filter_by(
                    work_number_id=work_number_id,
                    material_id=int(mid)
                ).first()
                if quota and quota.quota_type == 'quantity' and float(quota.quota_quantity) > 0:
                    # 查询该工号下该物资已出库总量
                    from sqlalchemy import func as _func
                    used = db.session.query(_func.coalesce(_func.sum(StockOutItem.quantity), 0)).join(
                        StockOut, StockOut.id == StockOutItem.stock_out_id
                    ).filter(
                        StockOut.work_number_id == work_number_id,
                        StockOutItem.material_id == int(mid),
                        StockOut.status.in_(['approved', 'completed'])
                    ).scalar() or 0
                    remaining = float(quota.quota_quantity) - float(used)
                    if float(qty) > remaining:
                        m = Material.query.get(mid)
                        errors.append(f"{m.name if m else '物资'}超出限额（限额{float(quota.quota_quantity)}，已用{float(used)}，剩余{remaining}，本次申请{float(qty)}）")
                        continue

            amount = float(qty) * float(price)

            # P2: FIFO批次自动分配
            from app.models import SystemModule
            batch_enabled = False
            try:
                proj_module = SystemModule.query.filter_by(project_id=project_id).first()
                batch_enabled = proj_module and proj_module.batch_management
            except Exception:
                pass

            if batch_enabled:
                # 按FIFO分配批次
                allocations = _allocate_fifo_batches(project_id, int(mid), float(qty))
                for batch_no, alloc_qty in allocations:
                    si_item = StockOutItem(
                        stock_out_id=stock_out.id,
                        material_id=int(mid),
                        quantity=alloc_qty,
                        unit_price=price,
                        amount=float(alloc_qty) * float(price),
                        batch_no=batch_no
                    )
                    db.session.add(si_item)
            else:
                si_item = StockOutItem(
                    stock_out_id=stock_out.id,
                    material_id=int(mid),
                    quantity=qty,
                    unit_price=price,
                    amount=amount
                )
                db.session.add(si_item)

            total_qty = to_decimal(total_qty) + qty
            total_amount = to_decimal(total_amount) + to_decimal(amount)

            # P2-FIX: 草稿/提交不扣减库存，仅在审批通过时扣减

        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('stock_out.create'))

        stock_out.total_quantity = total_qty
        stock_out.total_amount = total_amount

        action = request.form.get('action', 'save')
        if action == 'save':
            stock_out.status = 'draft'
            stock_out.approval_status = 'draft'
        elif action == 'submit':
            if not usage_unit_id:
                db.session.rollback()
                flash('请选择领料单位。', 'danger')
                return redirect(url_for('stock_out.create'))
            if not stock_out.items:
                db.session.rollback()
                flash('请至少添加一条物资明细。', 'danger')
                return redirect(url_for('stock_out.create'))
            stock_out.status = 'pending'
            stock_out.approval_status = 'pending'
            db.session.commit()

            from app.approval.service import submit_approval, is_project_approval_enabled
            success, message, instance = submit_approval('stockout', stock_out.id, project_id=project_id)
            if success:
                flash('出库单已提交审批。', 'success')
            elif is_project_approval_enabled(project_id) is False or '已关闭审批模块' in message:
                for item in stock_out.items:
                    _apply_stock(project_id, item.material_id, -float(item.quantity))
                stock_out.status = 'approved'
                stock_out.approval_status = 'passed'
                db.session.commit()
                flash('出库单已直接生效（项目未启用审批模块）。', 'success')
            else:
                flash(message, 'danger')
            return redirect(url_for('stock_out.detail', id=stock_out.id))
        elif action == 'approve_save':
            stock_out.status = 'approved'
            stock_out.approval_status = 'passed'
            # P2-FIX: approve_save 直接审批通过，需要扣减库存
            for item in stock_out.items:
                _apply_stock(project_id, item.material_id, -float(item.quantity))
            # P2: 调拨出库自动创建入库单
            if stock_out.stock_out_type == '调拨出库' and stock_out.destination_project_id:
                success, msg = _process_transfer_in(stock_out)
                if success:
                    flash(f'调拨入库已自动创建: {msg}', 'info')

        db.session.commit()
        flash('出库单创建成功。', 'success')
        if stock_out.approval_status == 'draft':
            flash('当前出库单为草稿状态，请提交审批后生效。', 'info')
        return redirect(url_for('stock_out.detail', id=stock_out.id))

    usage_units = UsageUnit.query.filter_by(project_id=project_id).order_by(UsageUnit.name).all()
    work_numbers = WorkNumber.query.filter(WorkNumber.project_id == project_id, WorkNumber.parent_id.isnot(None)).order_by(WorkNumber.code).all()
    materials = get_project_materials(project_id, common_only=True).all()

    copy_from_id = request.args.get('copy_from', type=int)
    copy_stock_out = None
    if copy_from_id:
        src = StockOut.query.get(copy_from_id)
        if src and src.project_id == project_id:
            copy_stock_out = src

    from app.approval.service import is_approval_enabled
    all_projects = Project.query.order_by(Project.name).all()
    return render_template('stock_out/form.html', stock_out=copy_stock_out, usage_units=usage_units,
                           work_numbers=work_numbers, materials=materials,
                           default_code=_gen_stock_out_code(project_id),
                           is_copy=bool(copy_stock_out),
                           approval_enabled=is_approval_enabled('stockout', project_id=project_id), projects=all_projects)


@bp.route('/<int:id>')
@login_required
def detail(id):
    stock_out = StockOut.query.filter(StockOut.status != 'voided').filter_by(id=id).first_or_404()

    from app.models import ContractItem, ReconciliationItem, StockInItem
    from app.approval.service import get_instance_by_biz

    for item in stock_out.items:
        contract_item = ContractItem.query.filter(
            ContractItem.material_id == item.material_id,
            ContractItem.contract.has(project_id=stock_out.project_id)
        ).order_by(ContractItem.id.desc()).first()

        price_type = None
        unit_price = float(item.unit_price or 0)

        if contract_item:
            price_type = contract_item.price_type
            if price_type == '固定单价':
                unit_price = float(contract_item.unit_price_with_tax or 0)
            else:
                if stock_out.is_reconciled:
                    recon_item = ReconciliationItem.query.filter(
                        ReconciliationItem.material_id == item.material_id
                    ).order_by(ReconciliationItem.id.desc()).first()
                    if recon_item and recon_item.unit_price:
                        unit_price = float(recon_item.unit_price)
                else:
                    unit_price = float(contract_item.unit_price_with_tax or 0)

        item.display_unit_price = Decimal(str(unit_price))
        item.price_type = price_type
        item.display_amount = Decimal(str(unit_price)) * Decimal(str(item.quantity or 0))

    approval_instance = get_instance_by_biz('stockout', stock_out.id)
    return render_template('stock_out/detail.html', stock_out=stock_out,
                           approval_instance=approval_instance)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='stock_out', operation='编辑')
def edit(id):
    stock_out = StockOut.query.get_or_404(id)
    if stock_out.is_reconciled:
        flash('已对账的出库单禁止编辑。', 'danger')
        return redirect(url_for('stock_out.detail', id=stock_out.id))

    if stock_out.status not in ('draft', 'rejected'):
        flash('仅草稿或已驳回状态的单据可编辑。', 'danger')
        return redirect(url_for('stock_out.detail', id=stock_out.id))

    if request.method == 'POST':
        # P2-FIX: 草稿状态未扣减库存，无需回滚

        # 删除原明细
        for item in list(stock_out.items):
            db.session.delete(item)
        db.session.flush()

        usage_unit_id = request.form.get('usage_unit_id', type=int) or None
        work_number_id = request.form.get('work_number_id', type=int) or None
        team_id = request.form.get('team_id', type=int) or None

        stock_out_date_str = request.form.get('stock_out_date')
        try:
            stock_out.stock_out_date = datetime.strptime(stock_out_date_str, '%Y-%m-%d').date()
        except Exception:
            stock_out.stock_out_date = date.today()
        stock_out.stock_out_type = request.form.get('stock_out_type', '工程领用')
        stock_out.usage_unit_id = usage_unit_id
        stock_out.work_number_id = work_number_id
        stock_out.team_id = team_id
        stock_out.issue_location = request.form.get('issue_location', '').strip()
        stock_out.purpose = request.form.get('purpose', '').strip()
        stock_out.operator = request.form.get('operator', '').strip() or current_user.name or current_user.username
        stock_out.remark = request.form.get('remark', '').strip()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')

        total_qty = 0
        total_amount = 0
        errors = []

        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
                price = to_decimal(unit_prices[idx] if idx < len(unit_prices) else 0)
            except Exception:
                continue
            if qty <= 0:
                continue

            current_stock = _get_current_stock(stock_out.project_id, int(mid))
            if float(qty) > current_stock and not ALLOW_NEGATIVE_STOCK:
                m = Material.query.get(mid)
                errors.append(f"{m.name if m else '物资'}库存不足（当前{current_stock}，出库{float(qty)}）")
                continue

            amount = float(qty) * float(price)
            si_item = StockOutItem(
                stock_out_id=stock_out.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=price,
                amount=amount
            )
            db.session.add(si_item)
            total_qty = to_decimal(total_qty) + qty
            total_amount = to_decimal(total_amount) + to_decimal(amount)

            # P2-FIX: 草稿/提交不扣减库存，仅在审批通过时扣减

        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('stock_out.edit', id=stock_out.id))

        stock_out.total_quantity = total_qty
        stock_out.total_amount = total_amount

        action = request.form.get('action', 'save')
        if action == 'save':
            stock_out.status = 'draft'
            stock_out.approval_status = 'draft'
        elif action == 'submit':
            if not usage_unit_id:
                db.session.rollback()
                flash('请选择领料单位。', 'danger')
                return redirect(url_for('stock_out.edit', id=stock_out.id))
            if not stock_out.items:
                db.session.rollback()
                flash('请至少添加一条物资明细。', 'danger')
                return redirect(url_for('stock_out.edit', id=stock_out.id))
            stock_out.status = 'pending'
            stock_out.approval_status = 'pending'
            db.session.commit()

            from app.approval.service import submit_approval, is_project_approval_enabled
            success, message, instance = submit_approval('stockout', stock_out.id, project_id=stock_out.project_id)
            if success:
                flash('出库单已提交审批。', 'success')
            elif is_project_approval_enabled(stock_out.project_id) is False or '已关闭审批模块' in message:
                for item in stock_out.items:
                    _apply_stock(stock_out.project_id, item.material_id, -float(item.quantity))
                stock_out.status = 'approved'
                stock_out.approval_status = 'passed'
                db.session.commit()
                flash('出库单已直接生效（项目未启用审批模块）。', 'success')
            else:
                flash(message, 'danger')
            return redirect(url_for('stock_out.detail', id=stock_out.id))
        elif action == 'approve_save':
            stock_out.status = 'approved'
            stock_out.approval_status = 'passed'
            # P2-FIX: approve_save 直接审批通过，需要扣减库存
            for item in stock_out.items:
                _apply_stock(stock_out.project_id, item.material_id, -float(item.quantity))

        db.session.commit()
        flash('出库单更新成功。', 'success')
        return redirect(url_for('stock_out.detail', id=stock_out.id))

    usage_units = UsageUnit.query.filter_by(project_id=stock_out.project_id).order_by(UsageUnit.name).all()
    work_numbers = WorkNumber.query.filter(WorkNumber.project_id == stock_out.project_id, WorkNumber.parent_id.isnot(None)).order_by(WorkNumber.code).all()
    materials = get_project_materials(stock_out.project_id, common_only=True).all()
    from app.approval.service import is_approval_enabled
    return render_template('stock_out/form.html', stock_out=stock_out, usage_units=usage_units,
                           work_numbers=work_numbers, materials=materials,
                           approval_enabled=is_approval_enabled('stockout', project_id=stock_out.project_id), projects=Project.query.order_by(Project.name).all())


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_out', operation='作废')
def delete(id):
    stock_out = StockOut.query.get_or_404(id)
    if stock_out.is_reconciled:
        flash('已对账的出库单禁止作废。', 'danger')
        return redirect(url_for('stock_out.detail', id=stock_out.id))

    if stock_out.status != 'draft':
        flash('仅草稿状态的单据可作废。', 'danger')
        return redirect(url_for('stock_out.detail', id=stock_out.id))

    # P2-FIX: 草稿状态未扣减库存，无需回滚

    stock_out.is_deleted = True
    stock_out.status = 'voided'
    db.session.commit()
    flash('出库单已作废。', 'success')
    return redirect(url_for('stock_out.index'))


@bp.route('/<int:id>/submit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_out', operation='提交审批')
def submit(id):
    """将草稿提交审批"""
    stock_out = StockOut.query.get_or_404(id)
    if stock_out.status != 'draft':
        flash('仅草稿状态可提交审批。', 'warning')
        return redirect(url_for('stock_out.detail', id=id))
    if stock_out.is_reconciled:
        flash('已对账的出库单禁止提交审批。', 'danger')
        return redirect(url_for('stock_out.detail', id=stock_out.id))

    from app.approval.service import submit_approval, is_project_approval_enabled
    success, message, instance = submit_approval('stockout', stock_out.id, project_id=stock_out.project_id)
    if success:
        flash('已提交审批。', 'success')
    elif is_project_approval_enabled(stock_out.project_id) is False or '已关闭审批模块' in message:
        for item in stock_out.items:
            _apply_stock(stock_out.project_id, item.material_id, -float(item.quantity))
        stock_out.status = 'approved'
        stock_out.approval_status = 'passed'
        db.session.commit()
        flash('出库单已直接生效（项目未启用审批模块）。', 'success')
    else:
        flash(message, 'danger')
    return redirect(url_for('stock_out.detail', id=stock_out.id))


@bp.route('/<int:id>/withdraw', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_out', operation='撤回审批')
def withdraw(id):
    """撤回审批（回到草稿）"""
    stock_out = StockOut.query.get_or_404(id)
    if stock_out.status != 'pending':
        flash('仅待审批状态可撤回。', 'warning')
        return redirect(url_for('stock_out.detail', id=id))

    from app.approval.service import get_instance_by_biz, withdraw as approval_withdraw
    instance = get_instance_by_biz('stockout', stock_out.id)
    if not instance or instance.status not in ('pending', 'approving'):
        flash('未找到有效的审批实例。', 'warning')
        return redirect(url_for('stock_out.detail', id=id))

    success, message = approval_withdraw(instance.id)
    if success:
        flash('已撤回审批。', 'success')
    else:
        flash(message, 'danger')
    return redirect(url_for('stock_out.detail', id=stock_out.id))


@bp.route('/<int:id>/restore', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_out', operation='恢复')
def restore(id):
    stock_out = StockOut.query.get_or_404(id)
    stock_out.is_deleted = False
    db.session.commit()
    flash('出库单已恢复。', 'success')
    return redirect(url_for('stock_out.index'))


@bp.route('/<int:id>/print')
@login_required
def print_stock_out(id):
    """打印出库单"""
    import time as _time
    _start = _time.time()
    stock_out = StockOut.query.get_or_404(id)
    now_date = datetime.now().strftime('%Y-%m-%d')
    try:
        result = render_template('stock_out/print.html', stock_out=stock_out, now_date=now_date)
        BusinessLogger.log_print(
            module='stock_out', doc_id=stock_out.id, doc_code=stock_out.code,
            status='success', duration_ms=int((_time.time() - _start) * 1000)
        )
        return result
    except Exception as e:
        BusinessLogger.log_print(
            module='stock_out', doc_id=stock_out.id, doc_code=stock_out.code,
            status='fail', error=str(e), duration_ms=int((_time.time() - _start) * 1000)
        )
        raise


@bp.route('/export')
@login_required
def export():
    """导出出库单列表Excel"""
    from flask import session, Response
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('stock_out.index'))
    
    keyword = request.args.get('keyword', '')
    query = StockOut.query.filter_by(project_id=project_id).filter(StockOut.deleted_at.is_(None))
    if keyword:
        query = query.filter(StockOut.code.contains(keyword))
    
    stock_outs = query.order_by(StockOut.stock_out_date.desc()).all()
    
    headers = ['序号', '出库单号', '出库日期', '出库类型', '领料单位', '发料部位', '总数量', '总金额', '经办人']
    rows = []
    for idx, so in enumerate(stock_outs, 1):
        rows.append([
            idx, so.code, str(so.stock_out_date or ''), so.stock_out_type,
            so.usage_unit.name if so.usage_unit else '', so.issue_location or '',
            float(so.total_quantity or 0), float(so.total_amount or 0), so.operator or ''
        ])
    
    from datetime import datetime
    from app.utils import export_to_excel
    filename = f'出库单列表_{datetime.now().strftime("%Y%m%d")}.xlsx'
    data = export_to_excel(headers, rows, '出库单列表', filename)
    
    return Response(data, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})


@bp.route('/api/materials_with_stock')
@login_required
def api_materials_with_stock():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])
    materials = get_project_materials(project_id, common_only=True).all()
    result = []
    for m in materials:
        stock = _get_current_stock(project_id, m.id)
        result.append({
            'id': m.id,
            'name': m.name,
            'code': m.code or '',
            'specification': m.specification or '',
            'unit': m.unit,
            'current_stock': stock
        })
    return jsonify(result)


@bp.route('/api/work_number/<int:id>')
@login_required
def api_work_number_detail(id):
    wn = WorkNumber.query.get_or_404(id)
    return jsonify({
        'issue_location': wn.division_name or '',
        'purpose': wn.item_name or ''
    })


@bp.route('/<int:unit_id>/teams')
@login_required
def api_unit_teams(unit_id):
    """返回指定用料单位下的班组JSON列表"""
    unit = UsageUnit.query.get_or_404(unit_id)
    teams = unit.teams.order_by(UnitTeam.created_at.desc()).all()
    return jsonify([
        {
            'id': t.id,
            'team_name': t.team_name,
            'picker_name': t.picker_name or '',
            'phone': t.phone or ''
        }
        for t in teams
    ])


@bp.route('/batch_delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='stock_out', operation='批量作废')
def batch_delete():
    project_id = session.get('current_project_id')
    ids = request.form.get('ids', '')
    id_list = [int(x) for x in ids.split(',') if x.strip().isdigit()]
    if not id_list:
        flash('请选择要作废的出库单。', 'warning')
        return redirect(url_for('stock_out.index'))

    success_count = 0
    fail_count = 0
    for sid in id_list:
        stock_out = StockOut.query.get(sid)
        if not stock_out or stock_out.project_id != project_id:
            fail_count += 1
            continue
        if stock_out.is_reconciled:
            fail_count += 1
            continue
        if stock_out.status != 'draft':
            fail_count += 1
            continue
        try:
            # P2-FIX: 草稿状态未扣减库存，无需回滚
            stock_out.is_deleted = True
            stock_out.status = 'voided'
            success_count += 1
        except Exception:
            db.session.rollback()
            fail_count += 1

    db.session.commit()
    if fail_count > 0:
        flash(f'批量作废完成：成功{success_count}条，失败{fail_count}条（可能已对账）。', 'warning')
    else:
        flash(f'批量作废成功，共{success_count}条。', 'success')
    return redirect(url_for('stock_out.index'))


@bp.route('/batch_export')
@login_required
def batch_export():
    from io import BytesIO
    from openpyxl import Workbook
    from flask import make_response
    project_id = session.get('current_project_id')
    ids_str = request.args.get('ids', '')
    id_list = [int(x) for x in ids_str.split(',') if x.strip().isdigit()]

    query = StockOut.query.filter_by(project_id=project_id).filter(StockOut.deleted_at.is_(None))
    if id_list:
        query = query.filter(StockOut.id.in_(id_list))
    stock_outs = query.order_by(StockOut.code).all()

    wb = Workbook()
    ws = wb.active
    ws.title = '出库单'
    headers = ['出库单号', '出库日期', '出库类型', '领料单位', '发料部位', '用途', '经办人', '总数量', '总金额', '状态']
    ws.append(headers)
    for s in stock_outs:
        ws.append([
            s.code,
            str(s.stock_out_date or ''),
            s.stock_out_type or '',
            s.usage_unit.name if s.usage_unit else '',
            s.issue_location or '',
            s.purpose or '',
            s.operator or '',
            float(s.total_quantity or 0),
            float(s.total_amount or 0),
            s.approval_status or ''
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = f'attachment; filename=stock_outs_{datetime.now().strftime("%Y%m%d%H%M%S")}.xlsx'
    return resp
