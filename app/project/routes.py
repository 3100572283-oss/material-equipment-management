from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from datetime import datetime
from app.project import bp
from app import db
from app.models import Project
from app.decorators import admin_required, editor_required, log_audit


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
            code=request.form.get('code', '').strip(),
            address=request.form.get('address', '').strip(),
            start_date=_parse_date(request.form.get('start_date')),
            planned_end_date=_parse_date(request.form.get('planned_end_date')),
            manager=request.form.get('manager', '').strip(),
            contact_phone=request.form.get('contact_phone', '').strip()
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
        project.code = request.form.get('code', '').strip()
        project.address = request.form.get('address', '').strip()
        project.start_date = _parse_date(request.form.get('start_date'))
        project.planned_end_date = _parse_date(request.form.get('planned_end_date'))
        project.manager = request.form.get('manager', '').strip()
        project.contact_phone = request.form.get('contact_phone', '').strip()
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
