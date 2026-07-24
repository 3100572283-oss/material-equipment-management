"""审批流程引擎 - 路由层"""
from flask import (render_template, request, redirect, url_for, flash,
                   jsonify, session)
from flask_login import login_required, current_user

from app.approval import bp
from app.approval import service
from app.approval.service import (
    submit_approval, get_my_pending_approvals, get_pending_count,
    get_instance_by_biz, BIZ_NAMES, STATUS_MAP, BIZ_MODELS,
    can_view_approval, can_operate_approval
)
from app.decorators import admin_required, log_audit
from app.models import (ApprovalFlow, ApprovalNode, ApprovalBranch,
                        ApprovalInstance, ApprovalRecord, User, Project, SysRole)
from app import db
import json


# ============== 管理员配置部分 ==============

@bp.route('/admin/flows')
@login_required
@admin_required
def flows():
    """审批流程列表页"""
    scope_filter = request.args.get('scope', 'all')
    project_filter = request.args.get('project_id', None, type=int)
    
    query = ApprovalFlow.query
    
    if scope_filter == 'company':
        query = query.filter_by(scope='company')
    elif scope_filter == 'project':
        query = query.filter_by(scope='project')
    
    if project_filter:
        query = query.filter(ApprovalFlow.project_ids.like(f'%{project_filter}%'))
    
    flows_list = query.order_by(
        ApprovalFlow.scope.desc(),
        ApprovalFlow.is_default.desc(),
        ApprovalFlow.biz_type,
        ApprovalFlow.id
    ).all()
    
    projects = Project.query.all()
    
    return render_template('approval/flows.html',
                           flows=flows_list, biz_names=BIZ_NAMES,
                           projects=projects, scope_filter=scope_filter,
                           project_filter=project_filter)


@bp.route('/admin/flows/create', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='新增流程')
def create_flow():
    """新增审批流程"""
    if request.method == 'POST':
        flow_name = request.form.get('flow_name', '').strip()
        flow_code = request.form.get('flow_code', '').strip()
        biz_type = request.form.get('biz_type', '').strip()
        description = request.form.get('description', '').strip()
        scope = request.form.get('scope', 'company')
        project_ids = request.form.getlist('project_ids')
        
        if not flow_name or not biz_type:
            flash('流程名称和业务类型不能为空', 'error')
            return redirect(url_for('approval.create_flow'))

        if biz_type not in BIZ_MODELS:
            flash('不支持的业务类型', 'error')
            return redirect(url_for('approval.create_flow'))

        if not flow_code:
            flow_code = f"{biz_type}_approval"

        if ApprovalFlow.query.filter_by(flow_code=flow_code).first():
            flash('流程编码已存在', 'error')
            return redirect(url_for('approval.create_flow'))

        if scope == 'project' and not project_ids:
            flash('项目级流程必须选择至少一个项目', 'error')
            return redirect(url_for('approval.create_flow'))

        project_ids_json = json.dumps(project_ids) if project_ids else None

        existing_enabled = ApprovalFlow.query.filter_by(
            biz_type=biz_type, enabled=True).first()

        flow = ApprovalFlow(
            flow_code=flow_code,
            flow_name=flow_name,
            biz_type=biz_type,
            enabled=not existing_enabled,
            description=description or None,
            scope=scope,
            project_ids=project_ids_json,
            is_default=scope == 'company' and not existing_enabled
        )
        db.session.add(flow)
        db.session.commit()
        
        if flow.is_default:
            ApprovalFlow.query.filter(
                ApprovalFlow.biz_type == biz_type,
                ApprovalFlow.scope == 'company',
                ApprovalFlow.id != flow.id
            ).update({'is_default': False})
            db.session.commit()
        
        flash('流程创建成功，请配置审批节点', 'success')
        return redirect(url_for('approval.nodes', id=flow.id))

    projects = Project.query.all()
    return render_template('approval/flow_form.html',
                           flow=None, biz_names=BIZ_NAMES,
                           projects=projects)


@bp.route('/admin/flows/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='启用/禁用流程')
def toggle_flow(id):
    """启用/禁用流程"""
    flow = ApprovalFlow.query.get_or_404(id)
    if not flow.enabled:
        existing = ApprovalFlow.query.filter_by(
            biz_type=flow.biz_type, enabled=True).first()
        if existing and existing.id != flow.id:
            flash(f'该业务类型已有启用流程：{existing.flow_name}，请先禁用', 'error')
            return redirect(url_for('approval.flows'))
        if flow.nodes.count() == 0:
            flash('流程未配置节点，无法启用', 'error')
            return redirect(url_for('approval.flows'))

    flow.enabled = not flow.enabled
    db.session.commit()
    flash(f'流程已{"启用" if flow.enabled else "禁用"}', 'success')
    return redirect(url_for('approval.flows'))


@bp.route('/admin/flows/<int:id>/set_default', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='设为默认流程')
def set_default_flow(id):
    """设为默认流程"""
    flow = ApprovalFlow.query.get_or_404(id)
    if flow.scope != 'company':
        flash('只有公司级流程可以设为默认', 'error')
        return redirect(url_for('approval.flows'))
    
    ApprovalFlow.query.filter(
        ApprovalFlow.biz_type == flow.biz_type,
        ApprovalFlow.scope == 'company'
    ).update({'is_default': False})
    db.session.commit()
    
    flow.is_default = True
    db.session.commit()
    
    flash('已设为默认流程，将作为所有未单独配置流程的项目的默认流程', 'success')
    return redirect(url_for('approval.flows'))


@bp.route('/admin/flows/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='删除流程')
def delete_flow(id):
    """删除流程"""
    flow = ApprovalFlow.query.get_or_404(id)
    if flow.instances.count() > 0:
        flash('该流程已有审批记录，无法删除（请禁用代替）', 'error')
        return redirect(url_for('approval.flows'))
    db.session.delete(flow)
    db.session.commit()
    flash('流程已删除', 'success')
    return redirect(url_for('approval.flows'))


@bp.route('/admin/flows/<int:id>/nodes')
@login_required
@admin_required
def nodes(id):
    """节点配置页"""
    flow = ApprovalFlow.query.get_or_404(id)
    nodes_list = flow.nodes.order_by(ApprovalNode.node_order).all()
    users = User.query.filter_by(status='active').order_by(User.username).all()
    roles = SysRole.query.filter_by(status=True).order_by(SysRole.sort).all()
    return render_template('approval/nodes.html',
                           flow=flow, nodes=nodes_list, users=users,
                           roles=roles, biz_names=BIZ_NAMES)


@bp.route('/admin/flows/<int:id>/nodes/create', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='新增节点')
def create_node(id):
    """新增节点"""
    flow = ApprovalFlow.query.get_or_404(id)
    node_name = request.form.get('node_name', '').strip()
    approve_type = request.form.get('approve_type', 'role')
    pass_rule = request.form.get('pass_rule', 'ANY')

    if not node_name:
        flash('节点名称不能为空', 'error')
        return redirect(url_for('approval.nodes', id=flow.id))

    max_order = db.session.query(db.func.max(ApprovalNode.node_order)).filter_by(
        flow_id=flow.id, branch_id=None).scalar() or 0

    approve_role = None
    approve_user_id = None
    if approve_type == 'role':
        roles = request.form.getlist('approve_role')
        approve_role = ','.join([r.strip() for r in roles if r.strip()])
        if not approve_role:
            flash('请至少选择一个审批角色', 'error')
            return redirect(url_for('approval.nodes', id=flow.id))
    else:
        approve_user_id = request.form.get('approve_user_id', type=int)
        if not approve_user_id:
            flash('请选择指定审批人', 'error')
            return redirect(url_for('approval.nodes', id=flow.id))

    node = ApprovalNode(
        flow_id=flow.id,
        node_order=max_order + 1,
        node_name=node_name,
        approve_type=approve_type,
        approve_role=approve_role,
        approve_user_id=approve_user_id,
        pass_rule=pass_rule
    )
    db.session.add(node)
    db.session.commit()
    flash('节点已添加', 'success')
    return redirect(url_for('approval.nodes', id=flow.id))


@bp.route('/admin/nodes/<int:id>/edit', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='编辑节点')
def edit_node(id):
    """编辑节点"""
    node = ApprovalNode.query.get_or_404(id)
    node_name = request.form.get('node_name', '').strip()
    approve_type = request.form.get('approve_type', 'role')
    pass_rule = request.form.get('pass_rule', 'ANY')

    if not node_name:
        flash('节点名称不能为空', 'error')
        return redirect(url_for('approval.nodes', id=node.flow_id))

    node.node_name = node_name
    node.approve_type = approve_type
    node.pass_rule = pass_rule

    if approve_type == 'role':
        roles = request.form.getlist('approve_role')
        node.approve_role = ','.join([r.strip() for r in roles if r.strip()])
        node.approve_user_id = None
        if not node.approve_role:
            flash('请至少选择一个审批角色', 'error')
            return redirect(url_for('approval.nodes', id=node.flow_id))
    else:
        node.approve_user_id = request.form.get('approve_user_id', type=int)
        node.approve_role = None
        if not node.approve_user_id:
            flash('请选择指定审批人', 'error')
            return redirect(url_for('approval.nodes', id=node.flow_id))

    db.session.commit()
    flash('节点已更新', 'success')
    return redirect(url_for('approval.nodes', id=node.flow_id))


@bp.route('/admin/nodes/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='删除节点')
def delete_node(id):
    """删除节点"""
    node = ApprovalNode.query.get_or_404(id)
    flow_id = node.flow_id
    db.session.delete(node)
    db.session.commit()

    # 重新排序剩余节点
    remaining = ApprovalNode.query.filter_by(flow_id=flow_id).order_by(
        ApprovalNode.node_order).all()
    for i, n in enumerate(remaining, 1):
        n.node_order = i
    db.session.commit()
    flash('节点已删除', 'success')
    return redirect(url_for('approval.nodes', id=flow_id))


@bp.route('/admin/nodes/<int:id>/move', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='移动节点')
def move_node(id):
    """上移/下移节点"""
    node = ApprovalNode.query.get_or_404(id)
    direction = request.form.get('direction', 'up')
    flow_id = node.flow_id
    branch_id = node.branch_id

    if branch_id:
        nodes_list = ApprovalNode.query.filter_by(branch_id=branch_id).order_by(
            ApprovalNode.node_order).all()
    else:
        nodes_list = ApprovalNode.query.filter_by(flow_id=flow_id).order_by(
            ApprovalNode.node_order).all()

    idx = None
    for i, n in enumerate(nodes_list):
        if n.id == id:
            idx = i
            break

    if idx is None:
        flash('节点不存在', 'error')
        return redirect(url_for('approval.nodes', id=flow_id))

    if direction == 'up' and idx > 0:
        prev = nodes_list[idx - 1]
        node.node_order, prev.node_order = prev.node_order, node.node_order
    elif direction == 'down' and idx < len(nodes_list) - 1:
        nxt = nodes_list[idx + 1]
        node.node_order, nxt.node_order = nxt.node_order, node.node_order
    else:
        flash('无法移动节点', 'error')
        return redirect(url_for('approval.nodes', id=flow_id))

    db.session.commit()
    flash('节点已移动', 'success')
    return redirect(url_for('approval.nodes', id=flow_id))


# ============== 分支管理 ==============

@bp.route('/admin/flows/<int:id>/branches')
@login_required
@admin_required
def branches(id):
    """分支管理页"""
    flow = ApprovalFlow.query.get_or_404(id)
    users = User.query.filter_by(status='active').order_by(User.username).all()
    roles = SysRole.query.filter_by(status=True).order_by(SysRole.sort).all()
    branch_list = flow.branches.order_by(ApprovalBranch.priority).all()
    return render_template('approval/branches.html',
                           flow=flow, users=users, roles=roles,
                           branch_list=branch_list,
                           biz_names=BIZ_NAMES)


@bp.route('/admin/flows/<int:id>/branches/toggle', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='切换分支模式')
def toggle_branch_mode(id):
    """切换是否启用分支模式"""
    flow = ApprovalFlow.query.get_or_404(id)
    flow.has_branch = not flow.has_branch
    db.session.commit()
    flash(f'已{"启用" if flow.has_branch else "关闭"}分支模式', 'success')
    return redirect(url_for('approval.branches', id=id))


@bp.route('/admin/branches/create', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='新增分支')
def create_branch():
    """新增分支"""
    from app.models import ApprovalBranchCondition
    flow_id = request.form.get('flow_id', type=int)
    branch_name = request.form.get('branch_name', '').strip()
    condition_fields = request.form.getlist('condition_fields[]')
    condition_operators = request.form.getlist('condition_operators[]')
    condition_values = request.form.getlist('condition_values[]')
    condition_logic = request.form.get('condition_logic', 'AND')
    is_default = request.form.get('is_default') == 'true'
    priority = request.form.get('priority', type=int)

    if not flow_id or not branch_name:
        flash('流程ID和分支名称不能为空', 'error')
        return redirect(request.referrer or url_for('approval.flows'))

    flow = ApprovalFlow.query.get_or_404(flow_id)
    flow.has_branch = True

    if is_default:
        for b in flow.branches:
            b.is_default = False

    branch = ApprovalBranch(
        flow_id=flow_id,
        branch_name=branch_name,
        condition_logic=condition_logic,
        is_default=is_default,
        priority=priority or flow.branches.count() + 1
    )
    db.session.add(branch)
    db.session.commit()

    for idx, (field, operator, value) in enumerate(zip(condition_fields, condition_operators, condition_values)):
        if field and operator and value:
            cond = ApprovalBranchCondition(
                branch_id=branch.id,
                sort=idx + 1,
                condition_field=field,
                condition_operator=operator,
                condition_value=value
            )
            db.session.add(cond)
    db.session.commit()

    flash('分支已创建', 'success')
    return redirect(url_for('approval.branches', id=flow_id))


@bp.route('/admin/branches/<int:id>/edit', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='编辑分支')
def edit_branch(id):
    """编辑分支"""
    from app.models import ApprovalBranchCondition
    branch = ApprovalBranch.query.get_or_404(id)
    branch_name = request.form.get('branch_name', '').strip()
    condition_fields = request.form.getlist('condition_fields[]')
    condition_operators = request.form.getlist('condition_operators[]')
    condition_values = request.form.getlist('condition_values[]')
    condition_logic = request.form.get('condition_logic', 'AND')
    is_default = request.form.get('is_default') == 'true'
    priority = request.form.get('priority', type=int)

    if not branch_name:
        flash('分支名称不能为空', 'error')
        return redirect(url_for('approval.branches', id=branch.flow_id))

    branch.branch_name = branch_name
    branch.condition_logic = condition_logic
    branch.priority = priority or branch.priority

    if is_default:
        for b in branch.flow.branches:
            b.is_default = False
        branch.is_default = True

    for cond in branch.conditions.all():
        db.session.delete(cond)

    for idx, (field, operator, value) in enumerate(zip(condition_fields, condition_operators, condition_values)):
        if field and operator and value:
            cond = ApprovalBranchCondition(
                branch_id=branch.id,
                sort=idx + 1,
                condition_field=field,
                condition_operator=operator,
                condition_value=value
            )
            db.session.add(cond)

    db.session.commit()
    flash('分支已更新', 'success')
    return redirect(url_for('approval.branches', id=branch.flow_id))


@bp.route('/admin/branches/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='删除分支')
def delete_branch(id):
    """删除分支"""
    branch = ApprovalBranch.query.get_or_404(id)
    if branch.is_default:
        flash('默认分支不可删除', 'error')
        return redirect(url_for('approval.branches', id=branch.flow_id))
    flow_id = branch.flow_id
    db.session.delete(branch)
    db.session.commit()
    # 重新排序
    remaining = ApprovalBranch.query.filter_by(flow_id=flow_id).order_by(ApprovalBranch.priority).all()
    for i, b in enumerate(remaining, 1):
        b.priority = i
    db.session.commit()
    flash('分支已删除', 'success')
    return redirect(url_for('approval.branches', id=flow_id))


@bp.route('/admin/branches/<int:id>/move', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='移动分支')
def move_branch(id):
    """上移/下移分支（调整优先级）"""
    branch = ApprovalBranch.query.get_or_404(id)
    direction = request.form.get('direction', 'up')
    flow_id = branch.flow_id

    branches_list = ApprovalBranch.query.filter_by(flow_id=flow_id).order_by(
        ApprovalBranch.priority).all()

    idx = None
    for i, b in enumerate(branches_list):
        if b.id == id:
            idx = i
            break

    if idx is None:
        flash('分支不存在', 'error')
        return redirect(url_for('approval.branches', id=flow_id))

    if direction == 'up' and idx > 0:
        prev = branches_list[idx - 1]
        if prev.is_default:
            flash('不能移动到默认分支前面', 'error')
            return redirect(url_for('approval.branches', id=flow_id))
        branch.priority, prev.priority = prev.priority, branch.priority
    elif direction == 'down' and idx < len(branches_list) - 1:
        nxt = branches_list[idx + 1]
        if branch.is_default:
            flash('默认分支必须在最后', 'error')
            return redirect(url_for('approval.branches', id=flow_id))
        branch.priority, nxt.priority = nxt.priority, branch.priority
    else:
        flash('无法移动分支', 'error')
        return redirect(url_for('approval.branches', id=flow_id))

    db.session.commit()
    flash('分支优先级已调整', 'success')
    return redirect(url_for('approval.branches', id=flow_id))


@bp.route('/admin/branches/<int:branch_id>/nodes/create', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='新增分支节点')
def create_branch_node(branch_id):
    """新增分支下的节点"""
    branch = ApprovalBranch.query.get_or_404(branch_id)
    node_name = request.form.get('node_name', '').strip()
    approve_type = request.form.get('approve_type', 'role')
    pass_rule = request.form.get('pass_rule', 'ANY')

    if not node_name:
        flash('节点名称不能为空', 'error')
        return redirect(url_for('approval.branches', id=branch.flow_id))

    max_order = db.session.query(db.func.max(ApprovalNode.node_order)).filter_by(
        branch_id=branch_id).scalar() or 0

    approve_role = None
    approve_user_id = None
    if approve_type == 'role':
        roles = request.form.getlist('approve_role')
        approve_role = ','.join([r.strip() for r in roles if r.strip()])
        if not approve_role:
            flash('请至少选择一个审批角色', 'error')
            return redirect(url_for('approval.branches', id=branch.flow_id))
    else:
        approve_user_id = request.form.get('approve_user_id', type=int)
        if not approve_user_id:
            flash('请选择指定审批人', 'error')
            return redirect(url_for('approval.branches', id=branch.flow_id))

    node = ApprovalNode(
        flow_id=branch.flow_id,
        branch_id=branch_id,
        node_order=max_order + 1,
        node_name=node_name,
        approve_type=approve_type,
        approve_role=approve_role,
        approve_user_id=approve_user_id,
        pass_rule=pass_rule
    )
    db.session.add(node)
    db.session.commit()
    flash('节点已添加', 'success')
    return redirect(url_for('approval.branches', id=branch.flow_id))


@bp.route('/admin/branch_nodes/<int:id>/edit', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='编辑分支节点')
def edit_branch_node(id):
    """编辑分支节点"""
    node = ApprovalNode.query.get_or_404(id)
    node_name = request.form.get('node_name', '').strip()
    approve_type = request.form.get('approve_type', 'role')
    pass_rule = request.form.get('pass_rule', 'ANY')

    if not node_name:
        flash('节点名称不能为空', 'error')
        return redirect(url_for('approval.branches', id=node.flow_id))

    node.node_name = node_name
    node.approve_type = approve_type
    node.pass_rule = pass_rule

    if approve_type == 'role':
        roles = request.form.getlist('approve_role')
        node.approve_role = ','.join([r.strip() for r in roles if r.strip()])
        node.approve_user_id = None
        if not node.approve_role:
            flash('请至少选择一个审批角色', 'error')
            return redirect(url_for('approval.branches', id=node.flow_id))
    else:
        node.approve_user_id = request.form.get('approve_user_id', type=int)
        node.approve_role = None
        if not node.approve_user_id:
            flash('请选择指定审批人', 'error')
            return redirect(url_for('approval.branches', id=node.flow_id))

    db.session.commit()
    flash('节点已更新', 'success')
    return redirect(url_for('approval.branches', id=node.flow_id))


@bp.route('/admin/branch_nodes/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='approval', operation='删除分支节点')
def delete_branch_node(id):
    """删除分支节点"""
    node = ApprovalNode.query.get_or_404(id)
    flow_id = node.flow_id
    branch_id = node.branch_id
    db.session.delete(node)
    db.session.commit()

    remaining = ApprovalNode.query.filter_by(branch_id=branch_id).order_by(
        ApprovalNode.node_order).all()
    for i, n in enumerate(remaining, 1):
        n.node_order = i
    db.session.commit()

    flash('节点已删除', 'success')
    return redirect(url_for('approval.branches', id=flow_id))


# ============== 业务端审批部分 ==============

@bp.route('/my')
@login_required
def my_approvals():
    """我的审批页面（Tab：待我审批/我已审批）"""
    project_filter = request.args.get('project_id', None, type=int)

    pending_instances = get_my_pending_approvals(current_user.id)
    if project_filter:
        pending_instances = [inst for inst in pending_instances if inst.project_id == project_filter]

    approved_ids = db.session.query(ApprovalRecord.instance_id).filter(
        ApprovalRecord.approver_id == current_user.id,
        ApprovalRecord.action.in_(['approve', 'reject'])
    ).distinct().all()
    ids = [r[0] for r in approved_ids]
    if ids:
        approved_query = ApprovalInstance.query.filter(
            ApprovalInstance.id.in_(ids)
        )
        if project_filter:
            approved_query = approved_query.filter(ApprovalInstance.project_id == project_filter)
        approved_instances = approved_query.order_by(ApprovalInstance.submit_time.desc()).all()
    else:
        approved_instances = []

    projects = Project.query.all()

    return render_template('approval/my_approvals.html',
                           pending_instances=pending_instances,
                           approved_instances=approved_instances,
                           biz_names=BIZ_NAMES,
                           status_map=STATUS_MAP,
                           projects=projects,
                           project_filter=project_filter)


@bp.route('/detail/<int:instance_id>')
@login_required
def detail(instance_id):
    """审批详情页 - 审批人可查看，不受数据权限限制"""
    instance = ApprovalInstance.query.get_or_404(instance_id)

    # 独立权限判断：审批人/申请人/历史审批人直接放行，其他用户走管理员或数据权限
    if not can_view_approval(instance_id, current_user.id):
        if not current_user.is_admin():
            from flask import abort
            abort(403)
    
    branch_name = None
    if instance.branch_id:
        branch = ApprovalBranch.query.get(instance.branch_id)
        branch_name = branch.branch_name if branch else None
    
    all_nodes = instance.flow.nodes.order_by(ApprovalNode.node_order).all()
    if instance.branch_id:
        all_nodes = [n for n in all_nodes if n.branch_id == instance.branch_id]
    records = instance.records.order_by(ApprovalRecord.approve_time).all()

    timeline = []
    for node in all_nodes:
        node_records = [r for r in records if r.node_id == node.id]
        if node_records:
            latest = node_records[-1]
            if latest.action == 'approve':
                node_status = 'approved'
            elif latest.action == 'reject':
                node_status = 'rejected'
            else:
                node_status = 'processed'
            
            sign_details = []
            if node.pass_rule == 'ALL':
                all_approvers = node.get_all_approvers()
                for approver in all_approvers:
                    r = next((rec for rec in node_records if rec.approver_id == approver.id), None)
                    if r:
                        sign_details.append({
                            'user': approver,
                            'status': r.action,
                            'time': r.approve_time,
                            'opinion': r.opinion
                        })
                    else:
                        sign_details.append({
                            'user': approver,
                            'status': 'pending',
                            'time': None,
                            'opinion': None
                        })
            elif node.pass_rule == 'ANY' and node_status == 'approved':
                first_approver = next((r for r in node_records if r.action == 'approve'), None)
                all_approvers = node.get_all_approvers()
                for approver in all_approvers:
                    if first_approver and approver.id == first_approver.approver_id:
                        sign_details.append({
                            'user': approver,
                            'status': 'approve',
                            'time': first_approver.approve_time,
                            'opinion': first_approver.opinion
                        })
                    else:
                        sign_details.append({
                            'user': approver,
                            'status': 'skipped',
                            'time': None,
                            'opinion': None
                        })
            
            timeline.append({
                'node': node,
                'status': node_status,
                'record': latest,
                'records': node_records,
                'sign_details': sign_details if node.pass_rule in ('ALL', 'ANY') else None
            })
        elif node.id == instance.current_node_id:
            sign_details = []
            if node.pass_rule == 'ALL':
                all_approvers = node.get_all_approvers()
                for approver in all_approvers:
                    sign_details.append({
                        'user': approver,
                        'status': 'pending',
                        'time': None,
                        'opinion': None
                    })
            elif node.pass_rule == 'ANY':
                all_approvers = node.get_all_approvers()
                for approver in all_approvers:
                    sign_details.append({
                        'user': approver,
                        'status': 'pending',
                        'time': None,
                        'opinion': None
                    })
            
            timeline.append({
                'node': node,
                'status': 'current',
                'record': None,
                'records': [],
                'sign_details': sign_details if node.pass_rule in ('ALL', 'ANY') else None
            })
        else:
            timeline.append({
                'node': node,
                'status': 'pending',
                'record': None,
                'records': [],
                'sign_details': None
            })

    submit_record = next((r for r in records if r.action == 'submit'), None)

    can_approve = (instance.status in ('pending', 'approving')
                   and instance.current_node is not None
                   and instance.current_node.can_approve(current_user)
                   and instance.applicant_id != current_user.id)
    can_withdraw = (instance.status in ('pending', 'approving')
                    and instance.applicant_id == current_user.id)

    # 当前用户审批过的节点ID集合（用于详情页高亮标记自己审批过的节点）
    my_approved_node_ids = set()
    for r in records:
        if r.approver_id == current_user.id and r.action in ('approve', 'reject'):
            if r.node_id:
                my_approved_node_ids.add(r.node_id)

    status_label, status_color = STATUS_MAP.get(
        instance.status, (instance.status, 'secondary'))

    biz_obj = service.get_biz_obj(instance.biz_type, instance.biz_id)

    roles = SysRole.query.filter_by(status=True).order_by(SysRole.sort).all()
    return render_template('approval/detail.html',
                           instance=instance,
                           branch_name=branch_name,
                           timeline=timeline,
                           submit_record=submit_record,
                           records=records,
                           can_approve=can_approve,
                           can_withdraw=can_withdraw,
                           status_label=status_label,
                           status_color=status_color,
                           biz_name=BIZ_NAMES.get(instance.biz_type, instance.biz_type),
                           biz_obj=biz_obj,
                           biz_names=BIZ_NAMES,
                           status_map=STATUS_MAP,
                           roles=roles,
                           my_approved_node_ids=my_approved_node_ids)


@bp.route('/submit/<biz_type>/<int:biz_id>', methods=['POST'])
@login_required
@log_audit(module='approval', operation='提交')
def submit(biz_type, biz_id):
    """提交审批"""
    if biz_type not in BIZ_MODELS:
        flash('不支持的业务类型', 'error')
        return redirect(url_for('approval.my_approvals'))

    opinion = request.form.get('opinion', '').strip()
    project_id = request.form.get('project_id', None, type=int)
    success, message, instance = submit_approval(biz_type, biz_id, opinion=opinion, project_id=project_id)

    if success and instance:
        flash(message, 'success')
        return redirect(url_for('approval.detail', instance_id=instance.id))

    flash(message, 'error')
    return redirect(request.referrer or url_for('approval.my_approvals'))


@bp.route('/approve/<int:instance_id>', methods=['POST'])
@login_required
@log_audit(module='approval', operation='审批通过')
def approve(instance_id):
    """审批通过"""
    opinion = request.form.get('opinion', '').strip()
    success, message = service.approve(instance_id, opinion=opinion)

    flash(message, 'success' if success else 'error')
    return redirect(url_for('approval.detail', instance_id=instance_id))


@bp.route('/reject/<int:instance_id>', methods=['POST'])
@login_required
@log_audit(module='approval', operation='驳回')
def reject(instance_id):
    """驳回审批"""
    reason = request.form.get('reason', '').strip()
    success, message = service.reject(instance_id, reason=reason)

    flash(message, 'success' if success else 'error')
    return redirect(url_for('approval.detail', instance_id=instance_id))


@bp.route('/withdraw/<int:instance_id>', methods=['POST'])
@login_required
@log_audit(module='approval', operation='撤回')
def withdraw(instance_id):
    """撤回审批"""
    reason = request.form.get('reason', '').strip()
    success, message = service.withdraw(instance_id, reason=reason)

    flash(message, 'success' if success else 'error')
    return redirect(url_for('approval.detail', instance_id=instance_id))


@bp.route('/api/pending_count')
@login_required
def api_pending_count():
    """获取待审批数量（JSON）"""
    count = get_pending_count(current_user.id)
    return jsonify({'count': count})
