# app/integration/routes.py
"""外部对接中心路由：数据源配置 + 接口目录 + 调用流水 + 导出模板管理。"""
from datetime import datetime

from flask import (render_template, request, redirect, url_for, flash,
                   jsonify, session, send_file)
from flask_login import login_required, current_user

from app import db
from app.integration import integration_bp
from app.integration.models import (ExtCredential, ExtEndpoint, ExtQueryLog,
                                    ExtQueryCache, ExportTemplate, ExportTemplateField,
                                    PROVIDERS, FIELD_SOURCES,
                                    ensure_builtin_integration_data)
from app.integration import qcc_client, services
from app.decorators import permission_required
from app.utils import encrypt_secret


def _operator():
    return getattr(current_user, 'username', None) or getattr(current_user, 'name', None)


def _is_xhr():
    """AJAX 请求判定：前端 integration-ui.js 在 fetch 时带 X-Requested-With 头。"""
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


# ============================================================ 数据源配置
@integration_bp.route('/')
@integration_bp.route('/datasource/')
@login_required
@permission_required('integration:datasource:view')
def datasource_list():
    ensure_builtin_integration_data()
    creds = ExtCredential.query.order_by(ExtCredential.provider).all()
    endpoints = ExtEndpoint.query.order_by(ExtEndpoint.provider, ExtEndpoint.code).all()
    today_calls = qcc_client._today_call_count()
    recent_logs = (ExtQueryLog.query.order_by(ExtQueryLog.created_at.desc())
                   .limit(20).all())
    cache_count = ExtQueryCache.query.count()
    return render_template('integration/datasource_list.html',
                           creds=creds, endpoints=endpoints, PROVIDERS=PROVIDERS,
                           today_calls=today_calls, recent_logs=recent_logs,
                           cache_count=cache_count)


@integration_bp.route('/datasource/<int:id>/save', methods=['POST'])
@login_required
@permission_required('integration:datasource:edit')
def datasource_save(id):
    cred = ExtCredential.query.get_or_404(id)
    cred.display_name = request.form.get('display_name', '').strip() or cred.display_name
    cred.base_url = request.form.get('base_url', '').strip() or None
    cred.auth_style = request.form.get('auth_style', 'header_sign').strip()
    cred.enabled = request.form.get('enabled') == 'on'
    cred.remark = request.form.get('remark', '').strip() or None

    # 空值 = 不改动既有密钥（页面只做掩码回显，不回传明文）
    new_key = request.form.get('api_key', '').strip()
    if new_key and not new_key.startswith('*'):
        cred.api_key = encrypt_secret(new_key)
    new_secret = request.form.get('api_secret', '').strip()
    if new_secret and not new_secret.startswith('*'):
        cred.api_secret = encrypt_secret(new_secret)
    if request.form.get('clear_key') == 'on':
        cred.api_key = None
        cred.api_secret = None
        cred.enabled = False

    for field, caster in (('cache_days', int), ('timeout_sec', int), ('daily_quota', int)):
        raw = request.form.get(field, '').strip()
        if raw == '':
            setattr(cred, field, None if field == 'daily_quota' else getattr(cred, field))
        else:
            try:
                setattr(cred, field, caster(raw))
            except ValueError:
                pass

    cred.updated_by = _operator()
    db.session.commit()
    flash('数据源配置已保存。', 'success')
    return redirect(url_for('integration.datasource_list'))


@integration_bp.route('/datasource/<int:id>/test', methods=['POST'])
@login_required
@permission_required('integration:datasource:edit')
def datasource_test(id):
    cred = ExtCredential.query.get_or_404(id)
    if cred.provider != 'qcc':
        msg = '该数据源为模板化导出，无需连通性测试。'
        if _is_xhr():
            return jsonify({'toast': msg, 'toast_type': 'info'})
        flash(msg, 'info')
        return redirect(url_for('integration.datasource_list'))
    ok, msg = qcc_client.test_connection(operator=_operator())
    toast_msg = '连通性测试%s：%s' % ('通过' if ok else '失败', msg)
    if _is_xhr():
        return jsonify({'toast': toast_msg,
                        'toast_type': 'success' if ok else 'error',
                        'reload': True})
    flash(toast_msg, 'success' if ok else 'danger')
    return redirect(url_for('integration.datasource_list'))


@integration_bp.route('/endpoint/<int:id>/save', methods=['POST'])
@login_required
@permission_required('integration:datasource:edit')
def endpoint_save(id):
    ep = ExtEndpoint.query.get_or_404(id)
    ep.path = request.form.get('path', '').strip() or None
    ep.method = request.form.get('method', 'GET').strip().upper()
    ep.enabled = request.form.get('enabled') == 'on'
    db.session.commit()
    msg = '接口 %s 已更新。' % (ep.name or ep.code)
    if _is_xhr():
        return jsonify({'toast': msg, 'toast_type': 'success', 'reload': True})
    flash(msg, 'success')
    return redirect(url_for('integration.datasource_list'))


@integration_bp.route('/cache/clear', methods=['POST'])
@login_required
@permission_required('integration:datasource:edit')
def cache_clear():
    n = ExtQueryCache.query.delete()
    db.session.commit()
    msg = '已清空 %s 条查询缓存（下次核验将重新调用外部接口）。' % n
    if _is_xhr():
        return jsonify({'toast': msg, 'toast_type': 'success', 'reload': True})
    flash(msg, 'success')
    return redirect(url_for('integration.datasource_list'))


# ============================================================ 导出模板
@integration_bp.route('/template/')
@login_required
@permission_required('integration:template:view')
def template_list():
    ensure_builtin_integration_data()
    templates = ExportTemplate.query.order_by(ExportTemplate.scene,
                                              ExportTemplate.id).all()
    return render_template('integration/template_list.html', templates=templates)


@integration_bp.route('/template/<int:id>/')
@login_required
@permission_required('integration:template:view')
def template_detail(id):
    tpl = ExportTemplate.query.get_or_404(id)
    return render_template('integration/template_detail.html',
                           tpl=tpl, FIELD_SOURCES=FIELD_SOURCES)


@integration_bp.route('/template/create', methods=['POST'])
@login_required
@permission_required('integration:template:edit')
def template_create():
    code = request.form.get('code', '').strip()
    name = request.form.get('name', '').strip()
    if not code or not name:
        flash('模板编码与名称必填。', 'warning')
        return redirect(url_for('integration.template_list'))
    if ExportTemplate.query.filter_by(code=code).first():
        flash('模板编码已存在。', 'warning')
        return redirect(url_for('integration.template_list'))
    copy_from = request.form.get('copy_from', type=int)
    tpl = ExportTemplate(
        code=code, name=name,
        scene=request.form.get('scene', 'subcontractor').strip(),
        target_platform=request.form.get('target_platform', '').strip() or '铁建云链',
        version=request.form.get('version', 'v1').strip(),
        file_format='xlsx', is_active=True, is_builtin=False,
        remark=request.form.get('remark', '').strip() or None,
        created_by=_operator())
    db.session.add(tpl)
    db.session.flush()
    if copy_from:
        src = ExportTemplate.query.get(copy_from)
        if src:
            for f in src.fields:
                db.session.add(ExportTemplateField(
                    template_id=tpl.id, seq=f.seq, target_field=f.target_field,
                    source_expr=f.source_expr, default_value=f.default_value,
                    required=f.required, remark=f.remark))
    db.session.commit()
    flash('模板已创建。', 'success')
    return redirect(url_for('integration.template_detail', id=tpl.id))


@integration_bp.route('/template/<int:id>/save', methods=['POST'])
@login_required
@permission_required('integration:template:edit')
def template_save(id):
    tpl = ExportTemplate.query.get_or_404(id)
    tpl.name = request.form.get('name', '').strip() or tpl.name
    tpl.target_platform = request.form.get('target_platform', '').strip() or tpl.target_platform
    tpl.version = request.form.get('version', '').strip() or tpl.version
    tpl.is_active = request.form.get('is_active') == 'on'
    tpl.remark = request.form.get('remark', '').strip() or None
    db.session.commit()
    flash('模板信息已保存。', 'success')
    return redirect(url_for('integration.template_detail', id=tpl.id))


@integration_bp.route('/template/<int:id>/delete', methods=['POST'])
@login_required
@permission_required('integration:template:edit')
def template_delete(id):
    tpl = ExportTemplate.query.get_or_404(id)
    if tpl.is_builtin:
        msg = '内置模板不可删除，可改为停用或调整字段。'
        if _is_xhr():
            return jsonify({'toast': msg, 'toast_type': 'warning'})
        flash(msg, 'warning')
        return redirect(url_for('integration.template_detail', id=tpl.id))
    db.session.delete(tpl)
    db.session.commit()
    if _is_xhr():
        return jsonify({'toast': '模板已删除。', 'toast_type': 'success',
                        'redirect': url_for('integration.template_list')})
    flash('模板已删除。', 'success')
    return redirect(url_for('integration.template_list'))


@integration_bp.route('/template/<int:id>/field/add', methods=['POST'])
@login_required
@permission_required('integration:template:edit')
def field_add(id):
    tpl = ExportTemplate.query.get_or_404(id)
    target = request.form.get('target_field', '').strip()
    if not target:
        flash('目标平台列名必填。', 'warning')
        return redirect(url_for('integration.template_detail', id=tpl.id))
    expr = request.form.get('source_expr', '').strip()
    if expr and expr not in FIELD_SOURCES:
        flash('非法的取值源。', 'warning')
        return redirect(url_for('integration.template_detail', id=tpl.id))
    max_seq = max([f.seq or 0 for f in tpl.fields] or [0])
    db.session.add(ExportTemplateField(
        template_id=tpl.id, seq=max_seq + 1, target_field=target,
        source_expr=expr or None,
        default_value=request.form.get('default_value', '').strip() or None,
        required=request.form.get('required') == 'on',
        remark=request.form.get('remark', '').strip() or None))
    db.session.commit()
    flash('字段已添加。', 'success')
    return redirect(url_for('integration.template_detail', id=tpl.id))


@integration_bp.route('/template/<int:id>/field/batch_save', methods=['POST'])
@login_required
@permission_required('integration:template:edit')
def field_batch_save(id):
    """整表保存：列名 / 取值源 / 顺序 / 必填 一次提交。"""
    tpl = ExportTemplate.query.get_or_404(id)
    saved = 0
    for f in tpl.fields:
        prefix = 'f%d_' % f.id
        if (prefix + 'target_field') not in request.form:
            continue
        target = request.form.get(prefix + 'target_field', '').strip()
        if not target:
            continue
        expr = request.form.get(prefix + 'source_expr', '').strip()
        if expr and expr not in FIELD_SOURCES:
            expr = f.source_expr
        f.target_field = target
        f.source_expr = expr or None
        f.default_value = request.form.get(prefix + 'default_value', '').strip() or None
        f.required = request.form.get(prefix + 'required') == 'on'
        try:
            f.seq = int(request.form.get(prefix + 'seq', f.seq or 0))
        except ValueError:
            pass
        saved += 1
    db.session.commit()
    flash('已保存 %s 个字段映射。' % saved, 'success')
    return redirect(url_for('integration.template_detail', id=tpl.id))


@integration_bp.route('/template/field/<int:fid>/delete', methods=['POST'])
@login_required
@permission_required('integration:template:edit')
def field_delete(fid):
    f = ExportTemplateField.query.get_or_404(fid)
    tid = f.template_id
    db.session.delete(f)
    db.session.commit()
    if _is_xhr():
        return jsonify({'toast': '字段已删除。', 'toast_type': 'success', 'reload': True})
    flash('字段已删除。', 'success')
    return redirect(url_for('integration.template_detail', id=tid))


@integration_bp.route('/template/<int:id>/preview')
@login_required
@permission_required('integration:template:view')
def template_preview(id):
    """用当前项目下最近 5 条分包商档案预览渲染结果（不落导出记录）。"""
    from app.subcontractor.models import SubcontractorProfile
    from app.utils import apply_data_scope
    tpl = ExportTemplate.query.get_or_404(id)
    pid = session.get('current_project_id')
    q = SubcontractorProfile.query
    if pid:
        q = q.filter_by(project_id=pid)
    q = apply_data_scope(q, SubcontractorProfile)
    profiles = q.order_by(SubcontractorProfile.created_at.desc()).limit(5).all()
    fields = sorted(tpl.fields, key=lambda x: (x.seq or 0, x.id))
    rows = [[services.resolve_field(p, f.source_expr, f.default_value) for f in fields]
            for p in profiles]
    return jsonify({
        'headers': [f.target_field for f in fields],
        'rows': rows,
        'count': len(rows),
    })
