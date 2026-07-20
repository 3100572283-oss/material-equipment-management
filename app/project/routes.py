from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from datetime import datetime
from app.project import bp
from app import db
from app.models import Project
from app.decorators import admin_required, editor_required, log_audit
from app.utils import gen_project_code


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        return None


@bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    query = Project.query
    if keyword:
        query = query.filter(Project.name.contains(keyword) | Project.code.contains(keyword))
    pagination = query.order_by(Project.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False
    )
    return render_template('project/index.html', pagination=pagination, keyword=keyword)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='project', operation='新增')
def create():
    if request.method == 'POST':
        project = Project(
            name=request.form.get('name', '').strip(),
            code=gen_project_code(),
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
@editor_required
@log_audit(module='project', operation='编辑')
def edit(id):
    project = Project.query.get_or_404(id)
    if request.method == 'POST':
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
        db.session.commit()
        flash('项目更新成功。', 'success')
        return redirect(url_for('project.index'))
    return render_template('project/form.html', project=project)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='project', operation='删除')
def delete(id):
    project = Project.query.get_or_404(id)
    db.session.delete(project)
    db.session.commit()
    flash('项目删除成功。', 'success')
    return redirect(url_for('project.index'))


@bp.route('/<int:id>/toggle_archive', methods=['POST'])
@login_required
@admin_required
@log_audit(module='project', operation='归档/取消归档')
def toggle_archive(id):
    """项目归档/取消归档"""
    project = Project.query.get_or_404(id)
    project.is_archived = not project.is_archived
    db.session.commit()
    flash(f'项目已{"归档" if project.is_archived else "取消归档"}。', 'success')
    return redirect(url_for('project.index'))


@bp.route('/<int:id>/modules', methods=['GET', 'POST'])
@login_required
@log_audit(module='project', operation='配置功能模块')
def modules(id):
    """项目功能模块配置"""
    from flask_login import current_user
    project = Project.query.get_or_404(id)

    # 权限校验：公司管理员 或 项目管理员（且项目是自己项目）
    can_config = False
    if current_user.is_admin():
        can_config = True
    else:
        # 检查是否为该项目的项目管理员（角色为project_admin且能访问该项目）
        if current_user.can_access_project(project.id):
            role_code = current_user.get_role_code()
            if role_code == 'project_admin':
                can_config = True
            elif current_user.has_permission('project:config'):
                can_config = True

    if not can_config:
        flash('您没有权限配置此项目的功能模块', 'error')
        return redirect(url_for('project.index'))

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
