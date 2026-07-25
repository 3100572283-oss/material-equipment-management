from flask import render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from datetime import datetime
from app.project import bp
from app import db
from app.models import Project, SysRoleDataScope
from app.decorators import log_audit, permission_required
from app.utils import gen_project_code, get_sub_dept_ids


def _get_project_data_scope_dept_ids():
    """获取当前用户数据权限范围内的部门ID列表
    
    返回 None 表示拥有全部数据权限（所有部门）
    返回列表表示仅可访问这些部门下的项目
    
    规则：
    - all / admin: 返回 None（全部）
    - dept_and_sub: 返回本部门及所有子部门ID
    - dept: 返回本部门ID
    - custom: 返回自定义部门ID列表
    - self: 返回空列表（仅靠 sys_user_project 关联）
    """
    if current_user.is_admin():
        return None

    role = current_user.role_obj
    data_scope = 'all'
    custom_depts = []
    if role:
        cfg = SysRoleDataScope.query.filter_by(role_id=role.id).first()
        if cfg:
            data_scope = cfg.data_scope or 'all'
            if cfg.custom_depts:
                import json
                try:
                    raw = json.loads(cfg.custom_depts)
                    custom_depts = [int(x) for x in raw if str(x).isdigit()]
                except Exception:
                    custom_depts = [int(x.strip()) for x in cfg.custom_depts.split(',') if x.strip().isdigit()]
    else:
        data_scope = current_user.data_scope or 'all'

    if data_scope == 'all':
        return None

    dept_ids = []
    if data_scope == 'dept' and current_user.dept_id:
        dept_ids = [current_user.dept_id]
    elif data_scope == 'dept_and_sub' and current_user.dept_id:
        dept_ids = get_sub_dept_ids(current_user.dept_id)
        dept_ids.append(current_user.dept_id)
    elif data_scope == 'custom' and custom_depts:
        dept_ids = custom_depts

    return dept_ids


def _apply_project_data_scope(query):
    """对项目查询应用数据权限过滤
    
    过滤逻辑：
    1. 按部门范围过滤（dept_id 匹配）
    2. 加上用户被直接分配的项目（sys_user_project）
    """
    dept_ids = _get_project_data_scope_dept_ids()

    if dept_ids is None:
        return query

    from sqlalchemy import or_

    conditions = []
    if dept_ids:
        conditions.append(Project.dept_id.in_(dept_ids))

    user_project_ids = [up.project_id for up in current_user.user_projects]
    if user_project_ids:
        conditions.append(Project.id.in_(user_project_ids))

    if not conditions:
        return query.filter(db.false())

    return query.filter(or_(*conditions))


def _check_project_permission(project):
    """检查当前用户是否有权限操作指定项目
    
    检查逻辑：
    - admin: 有权限
    - 项目.dept_id 在用户数据权限部门范围内: 有权限
    - 用户在 sys_user_project 中被分配到该项目: 有权限
    """
    if current_user.is_admin():
        return True

    dept_ids = _get_project_data_scope_dept_ids()
    if dept_ids is None:
        return True

    if project.dept_id and dept_ids and project.dept_id in dept_ids:
        return True

    for up in current_user.user_projects:
        if up.project_id == project.id:
            return True

    return False


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        return None


@bp.route('/')
@login_required
@permission_required('system:project:list')
def index():
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    archived = request.args.get('archived', '', type=str)
    dept_id = request.args.get('dept_id', type=int, default=0)
    status = request.args.get('status', '', type=str)

    query = Project.query
    if keyword:
        query = query.filter(Project.name.contains(keyword) | Project.code.contains(keyword))
    if archived == '1':
        query = query.filter_by(is_archived=True)
    elif archived == '0':
        query = query.filter_by(is_archived=False)
    if dept_id:
        query = query.filter_by(dept_id=dept_id)
    if status:
        query = query.filter_by(status=status)

    # 数据权限过滤：按用户数据权限范围过滤项目
    query = _apply_project_data_scope(query)

    pagination = query.order_by(Project.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False
    )

    # 获取有权限的行政部门列表（供筛选使用）
    from app.models import SysDept
    dept_query = SysDept.query.filter(
        SysDept.dept_type.in_(['company', 'branch', 'dept', 'team'])
    ).order_by(SysDept.sort.asc(), SysDept.created_at.asc())
    
    # 按数据权限过滤部门
    dept_ids = _get_project_data_scope_dept_ids()
    if dept_ids is not None:
        if dept_ids:
            dept_query = dept_query.filter(SysDept.id.in_(dept_ids))
        else:
            dept_query = dept_query.filter(False)
    
    filter_depts = dept_query.all()

    return render_template('project/index.html', 
                           pagination=pagination, 
                           keyword=keyword, 
                           archived=archived,
                           dept_id=dept_id,
                           status=status,
                           filter_depts=filter_depts)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@permission_required('system:project:add')
@log_audit(module='project', operation='新增')
def create():
    if request.method == 'POST':
        # 数据权限校验：非 admin 只能在自己权限范围内的部门建项目
        dept_id = request.form.get('dept_id', type=int)
        if not current_user.is_admin():
            allowed_dept_ids = _get_project_data_scope_dept_ids()
            if allowed_dept_ids is not None:
                # 默认归属到用户所在部门
                if not dept_id:
                    dept_id = current_user.dept_id
                # 校验 dept_id 是否在权限范围内
                if dept_id and (not allowed_dept_ids or dept_id not in allowed_dept_ids):
                    abort(403)
        project = Project(
            name=request.form.get('name', '').strip(),
            code=gen_project_code(),
            dept_id=dept_id,
            address=request.form.get('address', '').strip(),
            start_date=_parse_date(request.form.get('start_date')),
            planned_end_date=_parse_date(request.form.get('planned_end_date')),
            manager=request.form.get('manager', '').strip(),
            contact_phone=request.form.get('contact_phone', '').strip(),
            status=request.form.get('status', 'active'),
            project_type=request.form.get('project_type', '').strip() or None,
            building_area=float(request.form.get('building_area', 0) or 0),
            contract_amount=float(request.form.get('contract_amount', 0) or 0),
        )
        db.session.add(project)
        db.session.commit()
        flash('项目创建成功。', 'success')
        return redirect(url_for('project.index'))
    return render_template('project/form.html')


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@permission_required('system:project:edit')
@log_audit(module='project', operation='编辑')
def edit(id):
    project = Project.query.get_or_404(id)
    # 数据权限校验：只能编辑权限范围内的项目
    if not _check_project_permission(project):
        abort(403)
    if request.method == 'POST':
        # 数据权限校验：非 admin 不能将项目移到权限范围外的部门
        new_dept_id = request.form.get('dept_id', type=int)
        if not current_user.is_admin() and new_dept_id is not None:
            allowed_dept_ids = _get_project_data_scope_dept_ids()
            if allowed_dept_ids is not None and new_dept_id not in allowed_dept_ids:
                abort(403)
        project.name = request.form.get('name', '').strip()
        project.address = request.form.get('address', '').strip()
        project.start_date = _parse_date(request.form.get('start_date'))
        project.planned_end_date = _parse_date(request.form.get('planned_end_date'))
        project.manager = request.form.get('manager', '').strip()
        project.contact_phone = request.form.get('contact_phone', '').strip()
        project.status = request.form.get('status', 'active')
        project.project_type = request.form.get('project_type', '').strip() or None
        project.building_area = float(request.form.get('building_area', 0) or 0)
        project.contract_amount = float(request.form.get('contract_amount', 0) or 0)
        if new_dept_id is not None:
            project.dept_id = new_dept_id
        db.session.commit()
        flash('项目更新成功。', 'success')
        return redirect(url_for('project.index'))
    return render_template('project/form.html', project=project)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@permission_required('system:project:delete')
@log_audit(module='project', operation='删除')
def delete(id):
    project = Project.query.get_or_404(id)
    # 数据权限校验：只能删除权限范围内的项目
    if not _check_project_permission(project):
        abort(403)
    db.session.delete(project)
    db.session.commit()
    flash('项目删除成功。', 'success')
    return redirect(url_for('project.index'))


@bp.route('/<int:id>/toggle_archive', methods=['POST'])
@login_required
@permission_required('system:project:edit')
@log_audit(module='project', operation='归档/取消归档')
def toggle_archive(id):
    """项目归档/取消归档"""
    project = Project.query.get_or_404(id)
    # 数据权限校验：只能操作权限范围内的项目
    if not _check_project_permission(project):
        abort(403)
    project.is_archived = not project.is_archived
    db.session.commit()
    flash(f'项目已{"归档" if project.is_archived else "取消归档"}。', 'success')
    return redirect(url_for('project.index'))


@bp.route('/<int:id>/modules', methods=['GET', 'POST'])
@login_required
@permission_required('system:project:config')
@log_audit(module='project', operation='配置功能模块')
def modules(id):
    """项目功能模块配置"""
    project = Project.query.get_or_404(id)

    # 数据权限校验：只能配置权限范围内的项目
    if not _check_project_permission(project):
        abort(403)

    if request.method == 'POST':
        import json
        config = {}
        module_keys = [
            'module_turnover', 'module_equipment', 'module_quality_check',
            'module_batch', 'module_approval', 'module_ai',
            'module_industry_tools', 'module_scrap', 'module_period_close',
            'module_subcontract'
        ]
        for key in module_keys:
            config[key] = request.form.get(key) == 'on'
        project.set_module_config(config)
        db.session.commit()
        flash('功能模块配置已保存', 'success')
        return redirect(url_for('project.modules', id=id))

    module_config = project.get_module_config()
    module_labels = Project.get_module_labels()
    return render_template('project/modules.html', project=project,
                           module_config=module_config, module_labels=module_labels)


@bp.route('/api/list')
@login_required
@permission_required('system:project:list')
def api_list():
    """返回有权限的项目列表（供前端选择器使用）"""
    keyword = request.args.get('keyword', '', type=str)
    archived = request.args.get('archived', '0', type=str)

    query = Project.query
    if keyword:
        query = query.filter(Project.name.contains(keyword) | Project.code.contains(keyword))
    if archived == '0':
        query = query.filter_by(is_archived=False)
    elif archived == '1':
        query = query.filter_by(is_archived=True)

    # 数据权限过滤：按用户数据权限范围过滤项目
    query = _apply_project_data_scope(query)

    projects = query.order_by(Project.created_at.desc()).all()
    data = [{
        'id': p.id,
        'name': p.name,
        'code': p.code,
        'status': p.status,
        'is_archived': p.is_archived,
        'dept_id': p.dept_id,
    } for p in projects]
    return jsonify({'code': 0, 'data': data, 'count': len(data)})
