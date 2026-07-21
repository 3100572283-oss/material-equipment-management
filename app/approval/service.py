"""审批流程引擎 - 核心服务层"""
from datetime import datetime
from flask import session, flash
from flask_login import current_user
from app import db
from app.models import (ApprovalFlow, ApprovalNode, ApprovalBranch, ApprovalBranchCondition,
                        ApprovalInstance, ApprovalRecord,
                        Contract, StockIn, StockOut, Reconciliation, Payment, User,
                        PurchaseRequisition, MaterialTransfer, PaymentApplication,
                        MaterialScrap)
from .condition_engine import ConditionEngine

# 业务类型 → 模型 映射
BIZ_MODELS = {
    'contract': Contract,
    'stockin': StockIn,
    'stockout': StockOut,
    'reconcile': Reconciliation,
    'payment': Payment,
    'purchase_requisition': PurchaseRequisition,
    'material_transfer': MaterialTransfer,
    'payment_application': PaymentApplication,
    'scrap': MaterialScrap,
}

# 业务类型 → 中文名
BIZ_NAMES = {
    'contract': '合同',
    'stockin': '入库单',
    'stockout': '出库单',
    'reconcile': '对账单',
    'payment': '付款单',
    'purchase_requisition': '采购申请',
    'material_transfer': '调拨单',
    'payment_application': '付款申请',
    'scrap': '物资报废',
}

# 审批状态 → 中文 + 颜色
STATUS_MAP = {
    'draft': ('草稿', 'secondary'),
    'pending': ('待审批', 'warning'),
    'approving': ('审批中', 'warning'),
    'passed': ('已通过', 'success'),
    'rejected': ('已驳回', 'danger'),
    'withdrawn': ('已撤回', 'secondary'),
}

# 条件字段映射
CONDITION_FIELDS = {
    'contract': ['total_amount', 'contract_type', 'supplier'],
    'stockin': ['total_amount', 'stock_in_type', 'supplier'],
    'stockout': ['total_amount', 'stock_out_type', 'usage_unit'],
    'payment': ['amount'],
    'purchase_requisition': ['total_amount'],
    'payment_application': ['apply_amount', 'supplier'],
    'scrap': ['total_amount', 'reason'],
}


def get_flow_by_biz_type(biz_type, project_id=None):
    """获取业务类型对应的启用流程（支持项目级匹配）
    匹配优先级：项目级流程 > 公司级默认流程
    """
    from app.models import Project
    
    if project_id:
        project = Project.query.get(project_id)
        if project and hasattr(project, 'module_approval') and not project.module_approval:
            return None
    
    if project_id:
        project_flows = ApprovalFlow.query.filter(
            ApprovalFlow.biz_type == biz_type,
            ApprovalFlow.enabled == True,
            ApprovalFlow.scope == 'project'
        ).all()
        
        for flow in project_flows:
            project_list = flow.get_project_list()
            if str(project_id) in project_list:
                return flow
    
    company_default = ApprovalFlow.query.filter(
        ApprovalFlow.biz_type == biz_type,
        ApprovalFlow.enabled == True,
        ApprovalFlow.scope == 'company',
        ApprovalFlow.is_default == True
    ).first()
    if company_default:
        return company_default
    
    company_flow = ApprovalFlow.query.filter(
        ApprovalFlow.biz_type == biz_type,
        ApprovalFlow.enabled == True,
        ApprovalFlow.scope == 'company'
    ).first()
    return company_flow


def is_approval_enabled(biz_type, project_id=None):
    """判断该业务类型是否启用了审批流程（支持项目级匹配）"""
    flow = get_flow_by_biz_type(biz_type, project_id)
    return flow is not None and (flow.nodes.count() > 0 or (flow.has_branch and flow.branches.count() > 0))


def is_project_approval_enabled(project_id):
    """判断项目是否启用了审批模块"""
    from app.models import Project
    project = Project.query.get(project_id)
    if not project:
        return True
    if hasattr(project, 'module_approval'):
        return project.module_approval
    return True


def get_biz_title(biz_type, biz_id):
    """获取单据标题"""
    model = BIZ_MODELS.get(biz_type)
    if not model:
        return f'{biz_type}#{biz_id}'
    obj = model.query.get(biz_id)
    if not obj:
        return f'{biz_type}#{biz_id}'
    code = getattr(obj, 'code', None) or getattr(obj, 'payment_code', None) or getattr(obj, 'application_code', None) or getattr(obj, 'pr_no', None) or getattr(obj, 'transfer_no', None) or f'#{biz_id}'
    return f'{BIZ_NAMES.get(biz_type, biz_type)} {code}'


def get_biz_obj(biz_type, biz_id):
    """获取业务对象"""
    model = BIZ_MODELS.get(biz_type)
    if not model:
        return None
    return model.query.get(biz_id)


def get_biz_field_value(biz_type, biz_id, field_name):
    """获取业务单据字段值"""
    obj = get_biz_obj(biz_type, biz_id)
    if not obj:
        return None

    if field_name == 'total_amount':
        if biz_type == 'contract':
            return float(obj.amount_with_tax or 0)
        elif biz_type == 'stockin':
            return float(obj.total_amount or 0)
        elif biz_type == 'stockout':
            return float(obj.total_amount or 0)
        elif biz_type == 'payment':
            return float(obj.amount or 0)
        elif biz_type == 'payment_application':
            return float(obj.apply_amount or 0)
        elif biz_type == 'purchase_requisition':
            total = 0
            for item in obj.items:
                total += float(item.apply_qty or 0) * (float(item.unit_price or 0) if hasattr(item, 'unit_price') else 0)
            return total
        return 0
    elif field_name == 'contract_type':
        return getattr(obj, 'contract_type', None)
    elif field_name == 'supplier':
        if hasattr(obj, 'supplier') and obj.supplier:
            return obj.supplier.name
        return None
    elif field_name == 'stock_in_type':
        return getattr(obj, 'stock_in_type', None)
    elif field_name == 'stock_out_type':
        return getattr(obj, 'stock_out_type', None)
    elif field_name == 'usage_unit':
        if hasattr(obj, 'usage_unit') and obj.usage_unit:
            return obj.usage_unit.name
        return None

    return getattr(obj, field_name, None)


def find_matching_branch(flow, biz_type, biz_id):
    """根据业务数据判断命中哪个分支"""
    if not flow.has_branch:
        return None

    branches = flow.branches.order_by(ApprovalBranch.priority).all()
    default_branch = None

    biz_obj = get_biz_obj(biz_type, biz_id)
    if not biz_obj:
        return None

    for branch in branches:
        if branch.is_default:
            default_branch = branch
            continue

        if ConditionEngine.evaluate_branch(branch, biz_obj):
            return branch

    return default_branch


def submit_approval(biz_type, biz_id, applicant_id=None, opinion='', project_id=None):
    """提交审批
    返回: (success: bool, message: str, instance: ApprovalInstance|None)
    """
    if applicant_id is None:
        applicant_id = current_user.id

    if project_id and not is_project_approval_enabled(project_id):
        return False, '该项目已关闭审批模块，单据直接生效', None

    flow = get_flow_by_biz_type(biz_type, project_id)
    if not flow:
        return False, '未找到启用的审批流程', None

    obj = get_biz_obj(biz_type, biz_id)
    if not obj:
        return False, '业务单据不存在', None

    if biz_type == 'stockin':
        from app.utils import ConfigCache
        enable_qc = ConfigCache.get('enable_quality_check') == 'true'
        if enable_qc and obj.quality_status != 'passed':
            return False, '请先完成质量验收后再提交审批', None

    existing = ApprovalInstance.query.filter_by(
        biz_type=biz_type, biz_id=biz_id
    ).order_by(ApprovalInstance.id.desc()).first()

    if existing and existing.status in ('pending', 'approving'):
        return False, '该单据已在审批中', existing

    branch = find_matching_branch(flow, biz_type, biz_id)

    if branch:
        nodes = branch.branch_nodes.order_by(ApprovalNode.node_order).all()
        branch_id = branch.id
    else:
        nodes = flow.nodes.order_by(ApprovalNode.node_order).all()
        branch_id = None

    if not nodes:
        return False, '审批流程未配置节点', None

    first_node = nodes[0]
    instance = ApprovalInstance(
        flow_id=flow.id,
        branch_id=branch_id,
        biz_type=biz_type,
        biz_id=biz_id,
        biz_title=get_biz_title(biz_type, biz_id),
        applicant_id=applicant_id,
        project_id=project_id,
        submit_time=datetime.now(),
        status='pending',
        current_node_id=first_node.id
    )
    db.session.add(instance)
    db.session.flush()

    obj.approval_status = 'pending'

    record = ApprovalRecord(
        instance_id=instance.id,
        node_id=None,
        approver_id=applicant_id,
        action='submit',
        opinion=opinion or '提交审批',
        approve_time=datetime.now()
    )
    db.session.add(record)

    db.session.commit()

    try:
        from app.notification_service import notify_pending_approval
        approvers = first_node.get_all_approvers()
        for approver_id in approvers:
            approver = User.query.get(approver_id)
            if approver and approver.id != applicant_id:
                approver_name = approver.name or approver.username
                notify_pending_approval(approver_name, BIZ_NAMES.get(biz_type, biz_type),
                                        instance.biz_title or f'#{biz_id}')
    except Exception:
        pass

    return True, '已提交审批', instance


def approve(instance_id, approver_id=None, opinion=''):
    """审批通过
    返回: (success, message)
    """
    if approver_id is None:
        approver_id = current_user.id

    instance = ApprovalInstance.query.get(instance_id)
    if not instance:
        return False, '审批实例不存在'

    if instance.status not in ('pending', 'approving'):
        return False, '该单据不在审批中状态'

    current_node = instance.current_node
    if not current_node:
        return False, '未找到当前审批节点'

    approver = User.query.get(approver_id)
    if not approver:
        return False, '审批人不存在'

    if instance.applicant_id == approver_id:
        return False, '不能审批自己提交的单据'

    if not current_node.can_approve(approver):
        return False, '您没有权限审批此节点'

    record = ApprovalRecord(
        instance_id=instance.id,
        node_id=current_node.id,
        approver_id=approver_id,
        action='approve',
        opinion=opinion or '同意',
        approve_time=datetime.now()
    )
    db.session.add(record)

    if current_node.pass_rule == 'ALL':
        all_approvers = current_node.get_all_approvers()
        approved_ids = set()
        for rec in instance.records.filter(ApprovalRecord.node_id == current_node.id):
            if rec.action == 'approve':
                approved_ids.add(rec.approver_id)

        if len(approved_ids) >= len(all_approvers):
            pass_node = True
        else:
            pass_node = False

        for pending_id in all_approvers:
            if pending_id in approved_ids:
                continue
            existing = instance.records.filter(
                ApprovalRecord.node_id == current_node.id,
                ApprovalRecord.approver_id == pending_id
            ).first()
            if not existing:
                skip_rec = ApprovalRecord(
                    instance_id=instance.id,
                    node_id=current_node.id,
                    approver_id=pending_id,
                    action='pending',
                    opinion='待审批',
                    approve_time=None
                )
                db.session.add(skip_rec)

    else:
        pass_node = True

        all_approvers = current_node.get_all_approvers()
        for pending_id in all_approvers:
            if pending_id == approver_id:
                continue
            existing = instance.records.filter(
                ApprovalRecord.node_id == current_node.id,
                ApprovalRecord.approver_id == pending_id
            ).first()
            if not existing:
                skip_rec = ApprovalRecord(
                    instance_id=instance.id,
                    node_id=current_node.id,
                    approver_id=pending_id,
                    action='skip',
                    opinion='无需审批（或签）',
                    approve_time=datetime.now()
                )
                db.session.add(skip_rec)

    if pass_node:
        if instance.branch_id:
            all_nodes = instance.branch.branch_nodes.order_by(ApprovalNode.node_order).all()
        else:
            all_nodes = instance.flow.nodes.order_by(ApprovalNode.node_order).all()

        current_idx = None
        for i, n in enumerate(all_nodes):
            if n.id == current_node.id:
                current_idx = i
                break

        if current_idx is not None and current_idx < len(all_nodes) - 1:
            next_node = all_nodes[current_idx + 1]
            instance.current_node_id = next_node.id
            instance.status = 'approving'

            try:
                from app.notification_service import notify_pending_approval
                approvers = next_node.get_all_approvers()
                for approver_id in approvers:
                    next_approver = User.query.get(approver_id)
                    if next_approver and next_approver.id != instance.applicant_id:
                        approver_name = next_approver.name or next_approver.username
                        notify_pending_approval(approver_name, BIZ_NAMES.get(instance.biz_type, instance.biz_type),
                                                instance.biz_title or f'#{instance.biz_id}')
            except Exception:
                pass
        else:
            instance.current_node_id = None
            instance.status = 'passed'
            _on_approval_passed(instance)

    db.session.commit()

    try:
        from app.notification_service import notify_approval_result
        applicant = User.query.get(instance.applicant_id)
        applicant_name = applicant.name or applicant.username if applicant else '未知'
        notify_approval_result(applicant_name, BIZ_NAMES.get(instance.biz_type, instance.biz_type),
                                instance.biz_title or f'#{instance.biz_id}', '通过', opinion)
    except Exception:
        pass

    return True, '审批通过'


def reject(instance_id, approver_id=None, reason=''):
    """驳回审批
    返回: (success, message)
    """
    if approver_id is None:
        approver_id = current_user.id

    if not reason or not reason.strip():
        return False, '驳回必须填写原因'

    instance = ApprovalInstance.query.get(instance_id)
    if not instance:
        return False, '审批实例不存在'

    if instance.status not in ('pending', 'approving'):
        return False, '该单据不在审批中状态'

    current_node = instance.current_node
    if not current_node:
        return False, '未找到当前审批节点'

    approver = User.query.get(approver_id)
    if not approver:
        return False, '审批人不存在'

    if not current_node.can_approve(approver):
        return False, '您没有权限审批此节点'

    record = ApprovalRecord(
        instance_id=instance.id,
        node_id=current_node.id,
        approver_id=approver_id,
        action='reject',
        opinion=reason,
        approve_time=datetime.now()
    )
    db.session.add(record)

    instance.status = 'rejected'
    instance.reject_reason = reason
    instance.current_node_id = None

    if current_node.pass_rule == 'ANY':
        all_approvers = current_node.get_all_approvers()
        for pending_id in all_approvers:
            if pending_id == approver_id:
                continue
            existing = instance.records.filter(
                ApprovalRecord.node_id == current_node.id,
                ApprovalRecord.approver_id == pending_id
            ).first()
            if not existing:
                skip_rec = ApprovalRecord(
                    instance_id=instance.id,
                    node_id=current_node.id,
                    approver_id=pending_id,
                    action='skip',
                    opinion='已被驳回',
                    approve_time=datetime.now()
                )
                db.session.add(skip_rec)

    obj = get_biz_obj(instance.biz_type, instance.biz_id)
    if obj:
        obj.approval_status = 'rejected'
        # 付款申请同步状态
        if instance.biz_type == 'payment_application':
            obj.status = 'rejected'

    db.session.commit()

    try:
        from app.notification_service import notify_approval_result
        applicant = User.query.get(instance.applicant_id)
        applicant_name = applicant.name or applicant.username if applicant else '未知'
        notify_approval_result(applicant_name, BIZ_NAMES.get(instance.biz_type, instance.biz_type),
                                instance.biz_title or f'#{instance.biz_id}', '驳回', reason)
    except Exception:
        pass

    return True, '已驳回'


def withdraw(instance_id, applicant_id=None, reason=''):
    """撤回审批
    返回: (success, message)
    """
    if applicant_id is None:
        applicant_id = current_user.id

    instance = ApprovalInstance.query.get(instance_id)
    if not instance:
        return False, '审批实例不存在'

    if instance.applicant_id != applicant_id:
        return False, '只能撤回自己提交的审批'

    if instance.status not in ('pending', 'approving'):
        return False, '该单据不在审批中状态，无法撤回'

    record = ApprovalRecord(
        instance_id=instance.id,
        node_id=instance.current_node_id,
        approver_id=applicant_id,
        action='withdraw',
        opinion=reason or '撤回审批',
        approve_time=datetime.now()
    )
    db.session.add(record)

    instance.status = 'withdrawn'
    instance.current_node_id = None

    obj = get_biz_obj(instance.biz_type, instance.biz_id)
    if obj:
        obj.approval_status = 'withdrawn'
        # 付款申请同步状态
        if instance.biz_type == 'payment_application':
            obj.status = 'withdrawn'

    db.session.commit()
    return True, '已撤回'


def _on_approval_passed(instance):
    """审批通过后的业务逻辑回调"""
    obj = get_biz_obj(instance.biz_type, instance.biz_id)
    if not obj:
        return

    obj.approval_status = 'passed'

    if instance.biz_type == 'stockin':
        from app.stock_in.routes import _apply_inventory, _update_contract_total_in
        if obj.stock_in_type == '退货入库':
            _apply_inventory(obj, -1)
            _update_contract_total_in(obj, -1)
        else:
            _apply_inventory(obj, 1)
            _update_contract_total_in(obj, 1)

    elif instance.biz_type == 'purchase_requisition':
        obj.status = 'passed'

    elif instance.biz_type == 'material_transfer':
        obj.status = 'pending'

    elif instance.biz_type == 'payment_application':
        obj.status = 'passed'
        # 自动生成付款台账记录
        from app.payment_application.routes import _generate_payment
        _generate_payment(obj)

    elif instance.biz_type == 'scrap':
        # 报废通过：扣减库存（加权平均成本）并生成报废出库单
        _apply_scrap_inventory(obj)


def _apply_scrap_inventory(scrap):
    """报废通过后扣减库存并生成出库单（使用移动加权平均成本）"""
    from app.models import (StockOut, StockOutItem, Inventory, User,
                            InventoryBatch)
    from app.services.inventory_cost import InventoryCostService
    from flask_login import current_user
    from app import db
    from datetime import date

    total_amount = 0
    # 1. 扣减库存（按加权平均成本）
    for item in scrap.items:
        amount = InventoryCostService.apply_outbound(
            scrap.project_id, item.material_id, item.quantity)
        item.unit_price = (amount / item.quantity) if float(item.quantity) > 0 else 0
        item.amount = amount
        total_amount += float(amount)
    scrap.total_amount = total_amount

    # 2. 生成报废出库单
    from app.utils import _gen_code_with_seq
    code = _gen_code_with_seq('CK', scrap.project_id, StockOut)
    stock_out = StockOut(
        project_id=scrap.project_id,
        code=code,
        stock_out_date=scrap.scrap_date or date.today(),
        stock_out_type='物资报废',
        usage_unit_id=scrap.usage_unit_id,
        operator=(current_user.name or current_user.username) if current_user.is_authenticated else 'system',
        remark=f'报废单号：{scrap.code}（审批通过自动生成）',
        total_quantity=scrap.total_quantity,
        total_amount=total_amount,
        approval_status='passed',
    )
    db.session.add(stock_out)
    db.session.flush()
    # 3. 出库明细
    for item in scrap.items:
        so_item = StockOutItem(
            stock_out_id=stock_out.id,
            material_id=item.material_id,
            quantity=item.quantity,
            unit_price=item.unit_price,
            amount=item.amount,
        )
        db.session.add(so_item)


def get_my_pending_approvals(user_id):
    """获取待我审批的实例列表"""
    user = User.query.get(user_id)
    if not user:
        return []

    instances = ApprovalInstance.query.filter(
        ApprovalInstance.status.in_(['pending', 'approving'])
    ).order_by(ApprovalInstance.submit_time.desc()).all()

    result = []
    for inst in instances:
        if inst.applicant_id == user_id:
            continue

        node = inst.current_node
        if not node:
            continue

        if not node.can_approve(user):
            continue

        if node.pass_rule == 'ANY':
            existing = inst.records.filter(
                ApprovalRecord.node_id == node.id,
                ApprovalRecord.action.in_(['approve', 'reject'])
            ).first()
            if existing:
                continue

        result.append(inst)

    return result


def get_pending_count(user_id):
    """获取待审批数量"""
    return len(get_my_pending_approvals(user_id))


def get_instance_by_biz(biz_type, biz_id):
    """根据业务单据获取最新审批实例"""
    return ApprovalInstance.query.filter_by(
        biz_type=biz_type, biz_id=biz_id
    ).order_by(ApprovalInstance.id.desc()).first()


def init_default_flows():
    """初始化预置默认审批流程"""
    defaults = [
        ('contract_approval', '合同审批流程', 'contract'),
        ('stockin_approval', '入库单审批流程', 'stockin'),
        ('stockout_approval', '出库单审批流程', 'stockout'),
        ('reconcile_approval', '对账单审批流程', 'reconcile'),
        ('payment_approval', '付款审批流程', 'payment'),
        ('purchase_requisition_approval', '采购申请审批流程', 'purchase_requisition'),
        ('material_transfer_approval', '调拨单审批流程', 'material_transfer'),
        ('scrap_approval', '物资报废审批流程', 'scrap'),
    ]

    for code, name, biz_type in defaults:
        existing = ApprovalFlow.query.filter_by(flow_code=code).first()
        if existing:
            continue

        flow = ApprovalFlow(
            flow_code=code,
            flow_name=name,
            biz_type=biz_type,
            enabled=True,
            description=f'{name}（默认：管理员审批）',
            has_branch=False
        )
        db.session.add(flow)
        db.session.flush()

        node = ApprovalNode(
            flow_id=flow.id,
            node_order=1,
            node_name='管理员审批',
            approve_type='role',
            approve_role='admin',
            pass_rule='ANY'
        )
        db.session.add(node)

    contract_flow = ApprovalFlow.query.filter_by(flow_code='contract_approval').first()
    if contract_flow:
        # 如果已有分支但条件明细表为空，重建分支
        has_branches = contract_flow.branches.count() > 0
        has_conditions = False
        if has_branches:
            has_conditions = any(b.conditions.count() > 0 for b in contract_flow.branches.all())
        
        if not has_branches or not has_conditions:
            # 清空旧分支和节点
            for b in contract_flow.branches.all():
                for n in b.branch_nodes.all():
                    db.session.delete(n)
                for c in b.conditions.all():
                    db.session.delete(c)
                db.session.delete(b)
            # 清空无分支的旧节点
            old_nodes = ApprovalNode.query.filter_by(flow_id=contract_flow.id, branch_id=None).all()
            for n in old_nodes:
                db.session.delete(n)
            db.session.flush()

            contract_flow.has_branch = True
            contract_flow.description = '合同审批流程（按金额分支）'

            # 分支1：小额审批（≤10万）
            branch1 = ApprovalBranch(
                flow_id=contract_flow.id,
                branch_name='小额审批（≤10万）',
                condition_logic='AND',
                is_default=False,
                priority=1
            )
            db.session.add(branch1)
            db.session.flush()
            db.session.add(ApprovalBranchCondition(
                branch_id=branch1.id, sort=1,
                condition_field='total_amount',
                condition_operator='<=',
                condition_value='100000'
            ))
            db.session.add(ApprovalNode(
                flow_id=contract_flow.id, branch_id=branch1.id,
                node_order=1, node_name='部门经理审批',
                approve_type='role', approve_role='editor', pass_rule='ANY'
            ))

            # 分支2：大额审批（10万-50万）
            branch2 = ApprovalBranch(
                flow_id=contract_flow.id,
                branch_name='大额审批（10万-50万）',
                condition_logic='AND',
                is_default=False,
                priority=2
            )
            db.session.add(branch2)
            db.session.flush()
            db.session.add(ApprovalBranchCondition(
                branch_id=branch2.id, sort=1,
                condition_field='total_amount',
                condition_operator='>',
                condition_value='100000'
            ))
            db.session.add(ApprovalBranchCondition(
                branch_id=branch2.id, sort=2,
                condition_field='total_amount',
                condition_operator='<=',
                condition_value='500000'
            ))
            db.session.add(ApprovalNode(
                flow_id=contract_flow.id, branch_id=branch2.id,
                node_order=1, node_name='部门经理审批',
                approve_type='role', approve_role='editor', pass_rule='ANY'
            ))
            db.session.add(ApprovalNode(
                flow_id=contract_flow.id, branch_id=branch2.id,
                node_order=2, node_name='物资部长审批',
                approve_type='role', approve_role='admin', pass_rule='ANY'
            ))

            # 分支3：超大额审批（>50万）- 默认分支
            branch3 = ApprovalBranch(
                flow_id=contract_flow.id,
                branch_name='超大额审批（>50万）',
                condition_logic='AND',
                is_default=True,
                priority=3
            )
            db.session.add(branch3)
            db.session.flush()
            db.session.add(ApprovalBranchCondition(
                branch_id=branch3.id, sort=1,
                condition_field='total_amount',
                condition_operator='>',
                condition_value='500000'
            ))
            db.session.add(ApprovalNode(
                flow_id=contract_flow.id, branch_id=branch3.id,
                node_order=1, node_name='部门经理审批',
                approve_type='role', approve_role='editor', pass_rule='ANY'
            ))
            db.session.add(ApprovalNode(
                flow_id=contract_flow.id, branch_id=branch3.id,
                node_order=2, node_name='物资部长审批',
                approve_type='role', approve_role='admin', pass_rule='ANY'
            ))
            db.session.add(ApprovalNode(
                flow_id=contract_flow.id, branch_id=branch3.id,
                node_order=3, node_name='项目经理审批',
                approve_type='role', approve_role='admin', pass_rule='ANY'
            ))

    # 迁移：将 ApprovalBranch 中旧字段的条件迁移到 ApprovalBranchCondition 明细表
    _migrate_branch_conditions()

    db.session.commit()
    print(f"Default approval flows initialized")


def _migrate_branch_conditions():
    """将 ApprovalBranch 旧字段的单条件迁移到 ApprovalBranchCondition 明细表"""
    branches = ApprovalBranch.query.all()
    for branch in branches:
        cond_count = branch.conditions.count()
        if cond_count == 0 and branch.condition_field and branch.condition_operator and branch.condition_value:
            db.session.add(ApprovalBranchCondition(
                branch_id=branch.id, sort=1,
                condition_field=branch.condition_field,
                condition_operator=branch.condition_operator,
                condition_value=branch.condition_value
            ))
            print(f"Migrated condition for branch: {branch.branch_name}")
    db.session.flush()