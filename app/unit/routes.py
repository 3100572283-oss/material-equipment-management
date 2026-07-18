import os
from flask import render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required
from werkzeug.utils import secure_filename
from app.unit import bp
from app import db
from app.models import UsageUnit, UnitTeam
from app.decorators import editor_required, log_audit

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@bp.route('/')
@login_required
def index():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    query = UsageUnit.query.filter_by(project_id=project_id)
    if keyword:
        query = query.filter(UsageUnit.name.contains(keyword) | UsageUnit.code.contains(keyword))
    pagination = query.order_by(UsageUnit.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False
    )
    return render_template('unit/index.html', pagination=pagination, keyword=keyword)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='unit', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        file = request.files.get('auth_file')
        auth_path = None
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'authorizations')
            os.makedirs(upload_dir, exist_ok=True)
            auth_path = os.path.join('uploads', 'authorizations', filename)
            file.save(os.path.join(upload_dir, filename))

        unit = UsageUnit(
            project_id=project_id,
            name=request.form.get('name', '').strip(),
            code=request.form.get('code', '').strip(),
            manager=request.form.get('manager', '').strip(),
            contact_phone=request.form.get('contact_phone', '').strip(),
            auth_file=auth_path
        )
        db.session.add(unit)
        db.session.commit()
        flash('用料单位创建成功。', 'success')
        return redirect(url_for('unit.index'))
    return render_template('unit/form.html')


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='unit', operation='编辑')
def edit(id):
    unit = UsageUnit.query.get_or_404(id)
    if request.method == 'POST':
        file = request.files.get('auth_file')
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'authorizations')
            os.makedirs(upload_dir, exist_ok=True)
            unit.auth_file = os.path.join('uploads', 'authorizations', filename)
            file.save(os.path.join(upload_dir, filename))

        unit.name = request.form.get('name', '').strip()
        unit.code = request.form.get('code', '').strip()
        unit.manager = request.form.get('manager', '').strip()
        unit.contact_phone = request.form.get('contact_phone', '').strip()
        db.session.commit()
        flash('用料单位更新成功。', 'success')
        return redirect(url_for('unit.index'))
    return render_template('unit/form.html', unit=unit)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='unit', operation='删除')
def delete(id):
    unit = UsageUnit.query.get_or_404(id)
    db.session.delete(unit)
    db.session.commit()
    flash('用料单位删除成功。', 'success')
    return redirect(url_for('unit.index'))


@bp.route('/<int:id>/detail')
@login_required
def detail(id):
    """用料单位详情页（基本信息 + 班组列表）"""
    unit = UsageUnit.query.get_or_404(id)
    teams = unit.teams.order_by(UnitTeam.created_at.desc()).all()
    return render_template('unit/detail.html', unit=unit, teams=teams)


@bp.route('/<int:unit_id>/teams/create', methods=['POST'])
@login_required
@editor_required
@log_audit(module='unit', operation='班组新增')
def create_team(unit_id):
    unit = UsageUnit.query.get_or_404(unit_id)
    team = UnitTeam(
        unit_id=unit.id,
        team_name=request.form.get('team_name', '').strip(),
        picker_name=request.form.get('picker_name', '').strip(),
        phone=request.form.get('phone', '').strip()
    )
    db.session.add(team)
    db.session.commit()
    flash('班组创建成功。', 'success')
    return redirect(url_for('unit.detail', id=unit.id))


@bp.route('/<int:unit_id>/teams/<int:team_id>/edit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='unit', operation='班组编辑')
def edit_team(unit_id, team_id):
    team = UnitTeam.query.get_or_404(team_id)
    if team.unit_id != unit_id:
        flash('班组不属于该单位。', 'danger')
        return redirect(url_for('unit.detail', id=unit_id))
    team.team_name = request.form.get('team_name', '').strip()
    team.picker_name = request.form.get('picker_name', '').strip()
    team.phone = request.form.get('phone', '').strip()
    db.session.commit()
    flash('班组更新成功。', 'success')
    return redirect(url_for('unit.detail', id=unit_id))


@bp.route('/<int:unit_id>/teams/<int:team_id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='unit', operation='班组删除')
def delete_team(unit_id, team_id):
    team = UnitTeam.query.get_or_404(team_id)
    if team.unit_id != unit_id:
        flash('班组不属于该单位。', 'danger')
        return redirect(url_for('unit.detail', id=unit_id))
    db.session.delete(team)
    db.session.commit()
    flash('班组删除成功。', 'success')
    return redirect(url_for('unit.detail', id=unit_id))


@bp.route('/<int:unit_id>/teams/list')
@login_required
def team_list(unit_id):
    """返回指定用料单位的班组JSON列表"""
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
