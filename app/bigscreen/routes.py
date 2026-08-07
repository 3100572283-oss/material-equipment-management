# -*- coding: utf-8 -*-
"""独立数据可视化大屏路由。

- GET /bigscreen/        全屏大屏首页（项目可切换）
- GET /bigscreen/api/data 跨模块聚合 JSON（供前端轮询）
"""
from flask import (render_template, request, jsonify, session)
from flask_login import login_required, current_user

from app.bigscreen import bigscreen_bp
from app.bigscreen.services import aggregate_bigscreen
from app.models import Project
from app.utils import apply_data_scope
from app.decorators import permission_required


def _resolve_pid(req_pid):
    """解析目标项目：?project_id= 需经 apply_data_scope 校验（防越权）；
    否则回退到 session 当前项目。"""
    current_pid = session.get('current_project_id')
    if req_pid:
        ok = apply_data_scope(Project.query.filter_by(id=req_pid), Project).first()
        if ok:
            return req_pid
    return current_pid


@bigscreen_bp.route('/')
@login_required
@permission_required('bigscreen:view:view')
def index():
    """独立大屏首页（全屏终端）。"""
    req_pid = request.args.get('project_id', type=int)
    pid = _resolve_pid(req_pid)
    allowed = [(p.id, p.name) for p in apply_data_scope(Project.query, Project).all()]
    return render_template('bigscreen/index.html',
                           projects=allowed,
                           current_project_id=pid,
                           current_project_name=dict(allowed).get(pid))


@bigscreen_bp.route('/api/data')
@login_required
@permission_required('bigscreen:view:view')
def api_data():
    """大屏聚合数据接口。"""
    req_pid = request.args.get('project_id', type=int)
    pid = _resolve_pid(req_pid)
    # 未解析到项目且非超管 → 提示先选项目（避免越权看全局）
    if pid is None and not current_user.is_admin():
        return jsonify({'error': 'no_project', 'msg': '请先选择项目'}), 400
    period = request.args.get('period')
    data = aggregate_bigscreen(pid, period)
    return jsonify(data)
