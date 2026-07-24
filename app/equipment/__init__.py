from flask import Blueprint, redirect, url_for, flash, session, jsonify
from flask_login import current_user

bp = Blueprint('equipment', __name__)


@bp.before_request
def check_module_enabled():
    """设备模块开关拦截 - 全局生效"""
    if not current_user.is_authenticated:
        return
    
    from app.models import SysModule, Project
    
    # 检查系统级模块开关
    sys_module = SysModule.query.filter_by(module_key='module_equipment').first()
    if sys_module and not sys_module.status:
        if request.path.startswith('/api/') or request.is_json:
            return jsonify({'code': 403, 'message': '设备管理模块已被系统关闭'}), 403
        flash('设备管理模块已被系统关闭', 'warning')
        return redirect(url_for('main.index'))
    
    # 检查项目级模块开关
    project_id = session.get('current_project_id')
    if project_id:
        project = Project.query.get(project_id)
        if project and not project.is_module_enabled('module_equipment'):
            if request.path.startswith('/api/') or request.is_json:
                return jsonify({'code': 403, 'message': '设备管理模块未在当前项目中启用'}), 403
            flash('设备管理模块未在当前项目中启用', 'warning')
            return redirect(url_for('main.index'))


from app.equipment import routes
