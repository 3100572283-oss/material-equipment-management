# -*- coding: utf-8 -*-
"""移动端门户路由

包含两类路由：
1. 离线 PWA 录入（旧版，保留向后兼容）: /stock_in /stock_out /offline_data /api/*
2. 移动端门户（第一期）: /login / /stock-in/new /stock-out/new /inventory /records /profile
"""
from flask import (render_template, request, redirect, url_for, flash,
                   jsonify, session, make_response)
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, date, timedelta
from sqlalchemy import or_
import uuid
import os
import json

from app.mobile import bp

# P2: 文件上传扩展名校验
def _validate_upload_file(file):
    """校验上传文件扩展名，不通过则abort 400"""
    if file and file.filename:
        from app.utils import validate_file_extension
        from flask import abort
        ok, err = validate_file_extension(file.filename)
        if not ok:
            abort(400, err)
    return file

def _validate_upload_files(files):
    """校验上传文件列表扩展名，不通过则abort 400"""
    from app.utils import validate_file_extension
    from flask import abort
    for f in files:
        if f and f.filename:
            ok, err = validate_file_extension(f.filename)
            if not ok:
                abort(400, err)

from app.mobile.jwt_utils import (generate_access_token, generate_refresh_token, verify_token, mobile_auth_required, get_current_user_id, get_current_username)
from app import db
from app.models import (Material, Category, UsageUnit, StockIn, StockInItem,
                        StockOut, StockOutItem, Project, Inventory, Supplier,
                        Contract, WorkNumber, UnitTeam, User, LoginLog,
                        Attachment,
                        Equipment, EquipmentMaintenance, EquipmentStatusLog,
                        EquipmentRentSettle,
                        TurnoverMaterial, TurnoverInventory, TurnoverRecord,
                        EquipmentInspection, EquipmentInspectionTask,
                        EquipmentInspectionRecord,
                        PurchaseRequisition, PurchaseRequisitionItem,
                        MaterialScrap, MaterialScrapItem,
                        MaterialTransfer, MaterialTransferItem,
                        ConcreteTicket, Invoice)
from app.utils import (to_decimal, _gen_code_with_seq, get_dict_items,
                       apply_data_scope, get_project_materials, get_project_suppliers,
                       log_operation, upload_attachment, get_config)
from app.services.inventory_cost import InventoryCostService
from app.decorators import log_audit


# ============================================================
# 第一部分：离线 PWA 录入（旧版，保留向后兼容）
# ============================================================

def _legacy_gen_stock_in_code(project_id):
    """生成入库单号: RK-{项目ID}-{年月日}-{3位序号}（旧离线格式）"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"RK-{project_id}-{today}-"
    existing = StockIn.query.filter(StockIn.code.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


def _legacy_gen_stock_out_code(project_id):
    """生成出库单号: CK-{项目ID}-{年月日}-{3位序号}（旧离线格式）"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"CK-{project_id}-{today}-"
    existing = StockOut.query.filter(StockOut.code.like(f"{prefix}%")).count()
    return f"{prefix}{existing + 1:03d}"


def _require_project():
    """检查是否已选择项目，返回 project_id；未选择则重定向到首页。"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return None, redirect(url_for('mobile.portal_home'))
    return project_id, None


def _check_project_access():
    """P1-6: 验证当前用户是否有权访问当前项目（移动端API专用）
    
    返回 (project_id, error_response):
    - 成功: (project_id, None)
    - 失败: (None, jsonify_response)
    """
    project_id = session.get('current_project_id')
    if not project_id:
        return None, (jsonify({'success': False, 'message': '请先选择项目'}), 400)
    # 验证用户是否有权访问该项目
    if not current_user.can_access_project(project_id):
        return None, (jsonify({'success': False, 'message': '无权限访问该项目'}), 403)
    return project_id, None


def _check_project_access_or_redirect():
    """P1-6: 验证项目访问权限（页面路由专用，失败时重定向）
    
    返回 (project_id, redirect_response):
    - 成功: (project_id, None)
    - 失败: (None, redirect_response)
    """
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return None, redirect(url_for('mobile.portal_home'))
    if not current_user.can_access_project(project_id):
        flash('您无权限访问该项目。', 'danger')
        session.pop('current_project_id', None)
        return None, redirect(url_for('mobile.portal_home'))
    return project_id, None



# ============== JWT认证端点 ==============

@bp.route('/api/auth/token', methods=['POST'])
def api_auth_token():
    """移动端JWT登录 — 用户名密码换取token"""
    data = request.get_json(silent=True) or request.form
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not username or not password:
        return jsonify({'success': False, 'message': '请输入用户名和密码'}), 400
    
    from app.models import User
    user = User.query.filter_by(username=username).first()
    
    if user is None or not check_password_hash(user.password_hash, password):
        return jsonify({'success': False, 'message': '用户名或密码错误'}), 401
    
    if user.status and user.status != 'active':
        return jsonify({'success': False, 'message': '账号已被禁用'}), 403
    
    access_token = generate_access_token(user.id, user.username, user.role_id)
    refresh_token = generate_refresh_token(user.id, user.username)
    
    return jsonify({
        'success': True,
        'data': {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'token_type': 'Bearer',
            'expires_in': 86400,
            'user': {
                'id': user.id,
                'username': user.username,
                'name': user.name or user.username,
                'role_id': user.role_id,
            }
        }
    })


@bp.route('/api/auth/refresh', methods=['POST'])
def api_auth_refresh():
    """刷新access token"""
    data = request.get_json(silent=True) or request.form
    refresh_token = data.get('refresh_token', '').strip()
    
    if not refresh_token:
        return jsonify({'success': False, 'message': '缺少refresh_token'}), 400
    
    payload = verify_token(refresh_token)
    if not payload or payload.get('type') != 'refresh':
        return jsonify({'success': False, 'message': 'refresh_token无效或已过期'}), 401
    
    from app.models import User
    user = User.query.get(payload['user_id'])
    if user is None:
        return jsonify({'success': False, 'message': '用户不存在'}), 401
    
    access_token = generate_access_token(user.id, user.username, user.role_id)
    
    return jsonify({
        'success': True,
        'data': {
            'access_token': access_token,
            'token_type': 'Bearer',
            'expires_in': 86400,
        }
    })


@bp.route('/api/auth/profile', methods=['GET'])
@mobile_auth_required
def api_auth_profile():
    """获取当前用户信息（需认证）"""
    user_id = get_current_user_id()
    from app.models import User
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'}), 404
    
    return jsonify({
        'success': True,
        'data': {
            'id': user.id,
            'username': user.username,
            'name': user.name or user.username,
            'role_id': user.role_id,
            'email': user.email or '',
            'phone': user.phone or '',
        }
    })


@bp.route('/offline')
@login_required
def offline_index():
    """离线录入首页（旧版，保留向后兼容）"""
    project_id, redirect_resp = _require_project()
    if redirect_resp:
        return redirect_resp

    project = Project.query.get(project_id)
    return render_template('mobile/index.html', project=project)


@bp.route('/stock_in')
@login_required
def stock_in():
    """离线入库录入页面（旧版）"""
    project_id, redirect_resp = _require_project()
    if redirect_resp:
        return redirect_resp

    return render_template('mobile/stock_in.html')


@bp.route('/stock_out')
@login_required
def stock_out():
    """离线出库录入页面（旧版）"""
    project_id, redirect_resp = _require_project()
    if redirect_resp:
        return redirect_resp

    return render_template('mobile/stock_out.html')


@bp.route('/offline_data')
@login_required
def offline_data():
    """查看本地离线数据页面（旧版）"""
    project_id, redirect_resp = _require_project()
    if redirect_resp:
        return redirect_resp

    return render_template('mobile/offline_data.html')


@bp.route('/api/material_list')
@mobile_auth_required
def api_material_list():
    """获取当前项目的物资列表JSON（供离线缓存）"""
    project_id, err = _check_project_access()
    if err:
        return err

    materials = Material.query.filter_by(project_id=project_id) \
        .order_by(Material.code.asc(), Material.name.asc()).all()
    return jsonify([{
        'id': m.id,
        'name': m.name,
        'code': m.code or '',
        'spec': m.specification or '',
        'unit': m.unit or '',
        'category_id': m.category_id
    } for m in materials])


@bp.route('/api/category_list')
@mobile_auth_required
def api_category_list():
    """获取当前项目的分类列表JSON"""
    project_id, err = _check_project_access()
    if err:
        return err

    categories = Category.query.filter_by(project_id=project_id) \
        .order_by(Category.sort_order.asc(), Category.name.asc()).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'parent_id': c.parent_id or 0,
        'category_code': c.category_code or ''
    } for c in categories])


@bp.route('/api/usage_units')
@mobile_auth_required
def api_usage_units():
    """获取当前项目的用料单位列表JSON"""
    project_id, err = _check_project_access()
    if err:
        return err

    units = UsageUnit.query.filter_by(project_id=project_id) \
        .order_by(UsageUnit.name.asc()).all()
    return jsonify([{
        'id': u.id,
        'name': u.name,
        'code': u.code or '',
        'manager': u.manager or ''
    } for u in units])


@bp.route('/api/sync', methods=['POST'])
@mobile_auth_required
def api_sync():
    """同步离线数据到服务器（旧版，保留向后兼容）"""
    project_id, err = _check_project_access()
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    records = payload.get('records') or []

    if not records:
        return jsonify({'success': True, 'synced': 0, 'failed': 0, 'errors': [],
                        'message': '没有需要同步的记录。'})

    synced = 0
    failed = 0
    errors = []

    for idx, rec in enumerate(records):
        rec_type = rec.get('type')
        data = rec.get('data') or {}
        try:
            if rec_type == 'stock_in':
                with db.session.begin_nested():
                    _sync_stock_in(project_id, data)
                synced += 1
            elif rec_type == 'stock_out':
                with db.session.begin_nested():
                    _sync_stock_out(project_id, data)
                synced += 1
            else:
                failed += 1
                errors.append(f'第{idx + 1}条：未知记录类型 {rec_type}')
        except Exception as e:
            failed += 1
            errors.append(f'第{idx + 1}条：{e}')

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'synced': 0, 'failed': len(records),
                        'errors': [f'提交数据库失败：{e}']}), 500

    return jsonify({
        'success': True,
        'synced': synced,
        'failed': failed,
        'errors': errors,
        'message': f'同步完成：成功 {synced} 条，失败 {failed} 条。'
    })


def _sync_stock_in(project_id, data):
    """同步一条入库记录（旧版离线）"""
    material_id = int(data.get('material_id') or 0)
    if material_id <= 0:
        raise ValueError('物资ID无效')

    material = Material.query.filter_by(id=material_id, project_id=project_id).first()
    if not material:
        raise ValueError(f'物资不存在(id={material_id})')

    quantity = to_decimal(data.get('quantity') or 0)
    if quantity <= 0:
        raise ValueError('数量必须大于0')

    unit_price = to_decimal(data.get('unit_price') or 0)
    amount = float(quantity) * float(unit_price)
    batch_no = (data.get('batch_no') or '').strip() or None
    remark = (data.get('remark') or '').strip()
    saved_at = data.get('saved_at') or ''

    stock_in_date = date.today()
    if saved_at:
        try:
            stock_in_date = datetime.fromisoformat(saved_at).date()
        except Exception:
            pass

    price_status = 'confirmed' if float(unit_price) > 0 else 'unpriced'

    stock_in = StockIn(
        project_id=project_id,
        code=_legacy_gen_stock_in_code(project_id),
        stock_in_date=stock_in_date,
        stock_in_type='采购入库',
        operator=current_user.name or current_user.username,
        remark=f'[移动端离线] {remark}' if remark else '[移动端离线]',
        total_quantity=quantity,
        total_amount=amount,
        estimated_amount=0,
        actual_amount=amount if price_status == 'confirmed' else 0,
        approval_status='passed',
        quality_status='passed',
    )
    db.session.add(stock_in)
    db.session.flush()

    item = StockInItem(
        stock_in_id=stock_in.id,
        material_id=material_id,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
        price_status=price_status,
        batch_no=batch_no,
    )
    db.session.add(item)

    inv = Inventory.query.filter_by(
        project_id=project_id, material_id=material_id).first()
    if not inv:
        inv = Inventory(project_id=project_id, material_id=material_id,
                        quantity=0, estimated_amount=0, actual_amount=0)
        db.session.add(inv)
        db.session.flush()
    inv.quantity = to_decimal(inv.quantity) + quantity
    if price_status == 'confirmed':
        inv.actual_amount = to_decimal(inv.actual_amount or 0) + to_decimal(amount)


def _sync_stock_out(project_id, data):
    """同步一条出库记录（旧版离线）"""
    material_id = int(data.get('material_id') or 0)
    if material_id <= 0:
        raise ValueError('物资ID无效')

    material = Material.query.filter_by(id=material_id, project_id=project_id).first()
    if not material:
        raise ValueError(f'物资不存在(id={material_id})')

    quantity = to_decimal(data.get('quantity') or 0)
    if quantity <= 0:
        raise ValueError('数量必须大于0')

    usage_unit_id = data.get('usage_unit_id')
    usage_unit_id = int(usage_unit_id) if usage_unit_id else None
    remark = (data.get('remark') or '').strip()
    saved_at = data.get('saved_at') or ''

    stock_out_date = date.today()
    if saved_at:
        try:
            stock_out_date = datetime.fromisoformat(saved_at).date()
        except Exception:
            pass

    inv = Inventory.query.filter_by(
        project_id=project_id, material_id=material_id).first()
    current_stock = float(inv.quantity) if inv else 0
    if float(quantity) > current_stock:
        raise ValueError(
            f'{material.name}库存不足（当前 {current_stock}，出库 {float(quantity)}）')

    stock_out = StockOut(
        project_id=project_id,
        code=_legacy_gen_stock_out_code(project_id),
        stock_out_date=stock_out_date,
        stock_out_type='工程领用',
        usage_unit_id=usage_unit_id,
        operator=current_user.name or current_user.username,
        remark=f'[移动端离线] {remark}' if remark else '[移动端离线]',
        total_quantity=quantity,
        total_amount=0,
        approval_status='passed',
    )
    db.session.add(stock_out)
    db.session.flush()

    unit_price = to_decimal(0)
    if inv and float(inv.quantity) > 0 and float(inv.actual_amount or 0) > 0:
        unit_price = to_decimal(
            float(inv.actual_amount) / float(inv.quantity))
    amount = float(quantity) * float(unit_price)

    item = StockOutItem(
        stock_out_id=stock_out.id,
        material_id=material_id,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
    )
    db.session.add(item)

    stock_out.total_amount = amount

    if inv:
        inv.quantity = to_decimal(inv.quantity) - quantity
        if float(unit_price) > 0:
            inv.actual_amount = to_decimal(inv.actual_amount or 0) - to_decimal(amount)


# ============================================================
# 第二部分：移动端门户（第一期）
# ============================================================


@bp.route('/manifest.json')
def manifest_json():
    """PWA 应用清单"""
    from flask import send_from_directory, current_app
    static_dir = os.path.join(current_app.root_path, 'static')
    resp = make_response(send_from_directory(static_dir, 'manifest.json'))
    resp.headers['Content-Type'] = 'application/manifest+json; charset=utf-8'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@bp.route('/sw.js')
def service_worker():
    """Service Worker 文件"""
    from flask import send_from_directory, current_app
    static_dir = os.path.join(current_app.root_path, 'static')
    resp = make_response(send_from_directory(static_dir, 'sw.js'))
    resp.headers['Content-Type'] = 'application/javascript; charset=utf-8'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

def _parse_user_agent(user_agent_str):
    """简单解析User-Agent获取浏览器和操作系统"""
    browser = 'Unknown'
    os_name = 'Unknown'
    if not user_agent_str:
        return browser, os_name
    ua = user_agent_str.lower()
    if 'chrome' in ua and 'edg' not in ua:
        browser = 'Chrome'
    elif 'firefox' in ua:
        browser = 'Firefox'
    elif 'safari' in ua and 'chrome' not in ua:
        browser = 'Safari'
    elif 'edg' in ua:
        browser = 'Edge'
    if 'mac' in ua:
        os_name = 'macOS'
    elif 'windows' in ua:
        os_name = 'Windows'
    elif 'linux' in ua:
        os_name = 'Linux'
    elif 'android' in ua:
        os_name = 'Android'
    elif 'iphone' in ua or 'ipad' in ua:
        os_name = 'iOS'
    return browser, os_name


def _record_login_log(user, status, fail_reason=None):
    """记录登录日志"""
    user_agent = request.headers.get('User-Agent', '')
    browser, os_name = _parse_user_agent(user_agent)
    session_id = str(uuid.uuid4())
    log = LoginLog(
        user_id=user.id if user and status == 'success' else None,
        username=user.username if user else request.form.get('username', ''),
        login_time=datetime.now(),
        ip_address=request.remote_addr,
        user_agent=user_agent[:512] if user_agent else None,
        browser=browser,
        os=os_name,
        status=status,
        fail_reason=fail_reason,
        session_id=session_id
    )
    db.session.add(log)
    db.session.commit()
    return log.id if status == 'success' else None


def _get_current_project():
    """获取当前项目；不存在则尝试默认主项目"""
    project_id = session.get('current_project_id')
    if project_id:
        project = Project.query.get(project_id)
        if project and not project.is_archived:
            return project
    # 自动设置主项目
    main_project = current_user.get_main_project()
    if main_project:
        session['current_project_id'] = main_project.id
        session['current_project_name'] = main_project.name
        return main_project
    return None


# ============== 登录/退出 ==============

@bp.route('/login', methods=['GET', 'POST'])
def login():
    """移动端独立登录页"""
    if current_user.is_authenticated:
        return redirect(url_for('mobile.portal_home'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(username=username).first()

        if user and user.locked_until and user.locked_until > datetime.now():
            _record_login_log(user, 'failed', fail_reason='账号已锁定')
            remaining = int((user.locked_until - datetime.now()).total_seconds() / 60)
            flash(f'账号已被锁定，请 {remaining} 分钟后再试。', 'danger')
            return render_template('mobile/login.html')

        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=remember)
            user.last_login_at = datetime.now()
            user.last_login_ip = request.remote_addr
            user.failed_login_count = 0
            user.locked_until = None
            log_id = _record_login_log(user, 'success')
            session['login_log_id'] = log_id
            db.session.commit()
            log_operation('登录', module='系统', description=f'用户 {username} 移动端登录')
            # 设置默认项目
            main_project = user.get_main_project()
            if main_project:
                session['current_project_id'] = main_project.id
                session['current_project_name'] = main_project.name
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/mobile/'):
                return redirect(next_page)
            return redirect(url_for('mobile.portal_home'))
        else:
            if user:
                user.failed_login_count = (user.failed_login_count or 0) + 1
                if user.failed_login_count >= 5:
                    user.locked_until = datetime.now() + timedelta(minutes=30)
                    _record_login_log(user, 'failed', fail_reason='连续失败5次，账号锁定30分钟')
                    db.session.commit()
                    flash('连续登录失败5次，账号已锁定30分钟。', 'danger')
                else:
                    _record_login_log(user, 'failed', fail_reason='密码错误')
                    db.session.commit()
                    remaining = 5 - user.failed_login_count
                    flash(f'用户名或密码错误，还剩 {remaining} 次尝试机会。', 'danger')
            else:
                _record_login_log(None, 'failed', fail_reason='用户不存在')
                flash('用户名或密码错误。', 'danger')

    return render_template('mobile/login.html')


@bp.route('/logout')
@login_required
def logout():
    """移动端退出登录"""
    log_id = session.get('login_log_id')
    if log_id:
        log = LoginLog.query.get(log_id)
        if log:
            log.logout_time = datetime.now()
            db.session.commit()
    log_operation('退出', module='系统', description=f'用户 {current_user.username} 移动端退出')
    logout_user()
    session.pop('current_project_id', None)
    session.pop('current_project_name', None)
    session.pop('login_log_id', None)
    flash('您已退出登录。', 'info')
    return redirect(url_for('mobile.login'))


# ============== 首页 ==============

@bp.route('/')
@login_required
def portal_home():
    """移动端首页"""
    project = _get_current_project()
    if not project:
        flash('您未关联任何项目，请联系管理员', 'warning')
        return redirect(url_for('mobile.profile'))

    today = date.today()
    today_in = StockIn.query.filter(
        StockIn.project_id == project.id,
        StockIn.stock_in_date == today,
        StockIn.status == 'approved'
    ).all()
    today_out = StockOut.query.filter(
        StockOut.project_id == project.id,
        StockOut.stock_out_date == today,
        StockOut.status == 'approved'
    ).all()

    pending_qc = StockIn.query.filter_by(
        project_id=project.id, quality_status='pending', status='approved'
    ).count()

    # 问候语
    hour = datetime.now().hour
    if hour < 11:
        greeting = '早上好'
    elif hour < 13:
        greeting = '中午好'
    elif hour < 18:
        greeting = '下午好'
    else:
        greeting = '晚上好'

    # 日期显示
    weekday_cn = ['一', '二', '三', '四', '五', '六', '日'][today.weekday()]
    today_str = f"{today.strftime('%Y年%m月%d日')} 星期{weekday_cn}"

    visible_projects = current_user.get_visible_projects()

    # 待审批数量
    pending_approval_count = 0
    try:
        from app.approval.service import get_pending_count
        pending_approval_count = get_pending_count(current_user.id)
    except Exception:
        pass

    # 待巡检数量
    pending_inspection_count = 0
    try:
        pending_inspection_count = EquipmentInspectionTask.query.filter_by(
            project_id=project.id, status='pending'
        ).count()
    except Exception:
        pass

    # 未读消息数
    unread_message_count = 0
    try:
        from app.models import Message
        unread_message_count = Message.query.filter_by(
            user_id=current_user.id, is_read=False
        ).count()
    except Exception:
        pass

    return render_template('mobile/home.html', project=project,
                           greeting=greeting,
                           today_str=today_str,
                           visible_projects=visible_projects,
                           today_in_count=len(today_in),
                           today_in_qty=sum(float(s.total_quantity or 0) for s in today_in),
                           today_out_count=len(today_out),
                           today_out_qty=sum(float(s.total_quantity or 0) for s in today_out),
                           pending_qc=pending_qc,
                           pending_approval_count=pending_approval_count,
                           pending_inspection_count=pending_inspection_count,
                           unread_message_count=unread_message_count)


# ============== 录入中心 ==============

@bp.route('/entry-center')
@login_required
def entry_center():
    """录入中心：选择录入类型"""
    project = _get_current_project()
    if not project:
        flash('请先选择项目', 'warning')
        return redirect(url_for('mobile.profile'))

    # 定义所有录入类型
    # 结构: 分组 -> [{ key, name, desc, icon, color, url, permission_check }]
    # permission_check 是一个元组 (perm_func, *args) 或 None 表示所有人可见
    entry_groups = []

    # 库存类
    stock_group = {
        'name': '库存类',
        'icon': 'bi-box-seam',
        'items': [
            {
                'key': 'stock_in',
                'name': '入库登记',
                'desc': '新增入库单',
                'icon': 'bi-box-arrow-in-down',
                'color': 'blue',
                'url': 'mobile.stock_in_new',
                'has_perm': current_user.can_edit()
            },
            {
                'key': 'stock_out',
                'name': '出库登记',
                'desc': '新增出库单',
                'icon': 'bi-box-arrow-up',
                'color': 'green',
                'url': 'mobile.stock_out_new',
                'has_perm': current_user.can_edit()
            },
            {
                'key': 'stock_check',
                'name': '库存盘点',
                'desc': '盘点登记',
                'icon': 'bi-clipboard-data',
                'color': 'orange',
                'url': 'mobile.stock_check_list',
                'has_perm': current_user.can_edit()
            },
            {
                'key': 'scrap',
                'name': '物资报废',
                'desc': '报废申请',
                'icon': 'bi-trash',
                'color': 'danger',
                'url': 'mobile.scrap_create',
                'has_perm': current_user.can_edit()
            },
        ]
    }
    entry_groups.append(stock_group)

    # 商砼类
    concrete_group = {
        'name': '商砼类',
        'icon': 'bi-receipt',
        'items': [
            {
                'key': 'concrete',
                'name': '商砼小票',
                'desc': '现场登记',
                'icon': 'bi-truck',
                'color': 'info',
                'url': 'mobile.concrete_create',
                'has_perm': current_user.can_edit()
            },
        ]
    }
    entry_groups.append(concrete_group)

    # 设备类（检查设备模块是否开启）
    equipment_enabled = get_config('equipment_module_enabled', 'true') == 'true'
    if equipment_enabled:
        equipment_group = {
            'name': '设备类',
            'icon': 'bi-truck',
            'items': [
                {
                    'key': 'equipment_in',
                    'name': '设备进场',
                    'desc': '登记进场',
                    'icon': 'bi-truck',
                    'color': 'cyan',
                    'url': 'mobile.equipment_create',
                    'has_perm': current_user.can_edit()
                },
                {
                    'key': 'equipment_repair',
                    'name': '维修登记',
                    'desc': '维修记录',
                    'icon': 'bi-tools',
                    'color': 'amber',
                    'url': 'mobile.equipment_list',
                    'has_perm': current_user.can_edit()
                },
                {
                    'key': 'equipment_out',
                    'name': '设备退场',
                    'desc': '登记退场',
                    'icon': 'bi-arrow-right-square',
                    'color': 'teal',
                    'url': 'mobile.equipment_list',
                    'has_perm': current_user.can_edit()
                },
            ]
        }
        entry_groups.append(equipment_group)

    # 周转材类
    turnover_enabled = get_config('turnover_module_enabled', 'true') == 'true'
    if turnover_enabled:
        turnover_group = {
            'name': '周转材类',
            'icon': 'bi-box',
            'items': [
                {
                    'key': 'turnover_borrow',
                    'name': '领用登记',
                    'desc': '周转材领用',
                    'icon': 'bi-box-seam',
                    'color': 'amber',
                    'url': 'mobile.turnover_borrow',
                    'has_perm': current_user.can_edit()
                },
                {
                    'key': 'turnover_return',
                    'name': '归还登记',
                    'desc': '周转材归还',
                    'icon': 'bi-arrow-counterclockwise',
                    'color': 'teal',
                    'url': 'mobile.turnover_list',
                    'has_perm': current_user.can_edit()
                },
            ]
        }
        entry_groups.append(turnover_group)

    # 申请类
    requisition_group = {
        'name': '申请类',
        'icon': 'bi-file-earmark-text',
        'items': [
            {
                'key': 'pr',
                'name': '领料申请',
                'desc': '提交领料申请',
                'icon': 'bi-file-earmark-text',
                'color': 'warning',
                'url': 'mobile.pr_create',
                'has_perm': current_user.can_edit()
            },
        ]
    }
    entry_groups.append(requisition_group)

    # 基础数据类（仅管理员可见，含AI营业执照识别入口）
    if current_user.is_admin():
        base_group = {
            'name': '基础数据',
            'icon': 'bi-database',
            'items': [
                {
                    'key': 'supplier',
                    'name': '供应商管理',
                    'desc': '维护供应商主库（支持拍照识别营业执照）',
                    'icon': 'bi-truck',
                    'color': 'purple',
                    'url': 'mobile.supplier_list',
                    'has_perm': True
                },
                {
                    'key': 'invoice',
                    'name': '发票登记',
                    'desc': '新增发票（支持拍照识别发票）',
                    'icon': 'bi-receipt',
                    'color': 'cyan',
                    'url': 'mobile.invoice_list',
                    'has_perm': True
                },
            ]
        }
        entry_groups.append(base_group)

    # 过滤权限：只保留有权限的卡片，且过滤掉空分组
    visible_groups = []
    all_visible_items = []
    for group in entry_groups:
        visible_items = [item for item in group['items'] if item['has_perm']]
        if visible_items:
            g = {
                'name': group['name'],
                'icon': group['icon'],
                'items': visible_items
            }
            visible_groups.append(g)
            all_visible_items.extend(visible_items)

    # 如果只有1种录入权限，直接跳转到对应表单
    if len(all_visible_items) == 1:
        return redirect(url_for(all_visible_items[0]['url']))

    visible_projects = current_user.get_visible_projects()

    return render_template('mobile/entry_center.html',
                           project=project,
                           active_tab='record',
                           entry_groups=visible_groups,
                           visible_projects=visible_projects)


# ============== 入库登记 ==============

@bp.route('/stock-in/new', methods=['GET', 'POST'])
@login_required
def stock_in_new():
    """新增入库（移动端简化版）"""
    project = _get_current_project()
    if not project:
        flash('请先选择项目', 'warning')
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        return _handle_stock_in_submit(project)

    suppliers = get_project_suppliers(project.id, common_only=True).all()
    contracts = Contract.query.filter_by(project_id=project.id) \
        .order_by(Contract.code).all()
    stock_in_types = get_dict_items('stockin_type')

    return render_template('mobile/stock_in_new.html', project=project,
                           suppliers=suppliers, contracts=contracts,
                           stock_in_types=stock_in_types)


def _handle_stock_in_submit(project):
    """处理入库单提交"""
    try:
        supplier_id = request.form.get('supplier_id', type=int) or None
        contract_id = request.form.get('contract_id', type=int) or None
        if contract_id and not supplier_id:
            contract = Contract.query.get(contract_id)
            if contract:
                supplier_id = contract.supplier_id

        stock_in_type = request.form.get('stock_in_type', '采购入库')
        remark = (request.form.get('remark', '') or '').strip()

        stock_in = StockIn(
            project_id=project.id,
            code=_gen_code_with_seq('RK', project.id, StockIn),
            stock_in_date=date.today(),
            stock_in_type=stock_in_type,
            contract_id=contract_id,
            supplier_id=supplier_id,
            operator=current_user.name or current_user.username,
            remark=(remark + ' [移动端]') if remark else '[移动端]'
        )
        db.session.add(stock_in)
        db.session.flush()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')

        total_qty = 0
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
            except Exception:
                continue
            if qty <= 0:
                continue
            item = StockInItem(
                stock_in_id=stock_in.id,
                material_id=int(mid),
                quantity=qty,
                unit_price=0,
                amount=0,
                price_status='unpriced'
            )
            db.session.add(item)
            total_qty = to_decimal(total_qty) + qty

        stock_in.total_quantity = total_qty
        stock_in.total_amount = 0
        stock_in.estimated_amount = 0
        stock_in.actual_amount = 0

        # 审批/质检状态
        try:
            from app.approval.service import is_approval_enabled
            from app.utils import ConfigCache
            enable_qc = ConfigCache.get('enable_quality_check') == 'true'
        except Exception:
            enable_qc = False
            is_approval_enabled = lambda *a, **kw: False

        if enable_qc:
            stock_in.quality_status = 'pending'
            stock_in.approval_status = 'draft'
        elif is_approval_enabled('stockin'):
            stock_in.approval_status = 'draft'
            stock_in.quality_status = 'passed'
        else:
            stock_in.approval_status = 'passed'
            stock_in.quality_status = 'passed'
            # 立即更新库存
            for item in stock_in.items:
                InventoryCostService.apply_inbound(
                    project_id=project.id,
                    material_id=item.material_id,
                    quantity=item.quantity,
                    amount=0,
                    price_status='estimated'
                )

        db.session.commit()

        # 处理照片上传
        photos = request.files.getlist('photos[]')
        _validate_upload_files(photos)
        uploaded = 0
        for photo in photos:
            if photo and photo.filename:
                att, err = upload_attachment(photo, 'stock_in',
                                              biz_id=stock_in.id, project_id=project.id)
                if err:
                    flash(f'图片 {photo.filename} 上传失败：{err}', 'warning')
                else:
                    uploaded += 1

        # 处理手写签名
        signatures = request.files.getlist('signatures[]')
        _validate_upload_files(signatures)
        sig_uploaded = 0
        for sig in signatures:
            if sig and sig.filename:
                att, err = upload_attachment(sig, 'stock_in_signature',
                                              biz_id=stock_in.id, project_id=project.id)
                if err:
                    flash(f'签名上传失败：{err}', 'warning')
                else:
                    sig_uploaded += 1

        flash(f'入库单 {stock_in.code} 提交成功' +
              (f'，上传 {uploaded} 张图片' if uploaded else '') +
              (f'，签名 {sig_uploaded} 张' if sig_uploaded else ''), 'success')
        return redirect(url_for('mobile.records', type='in'))

    except Exception as e:
        db.session.rollback()
        flash(f'提交失败：{e}', 'danger')
        return redirect(url_for('mobile.stock_in_new'))


# ============== 出库登记 ==============

@bp.route('/stock-out/new', methods=['GET', 'POST'])
@login_required
def stock_out_new():
    """新增出库（移动端简化版）"""
    project = _get_current_project()
    if not project:
        flash('请先选择项目', 'warning')
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        return _handle_stock_out_submit(project)

    usage_units = UsageUnit.query.filter_by(project_id=project.id) \
        .order_by(UsageUnit.name).all()
    work_numbers = WorkNumber.query.filter_by(project_id=project.id).all()
    stock_out_types = get_dict_items('stockout_type')

    return render_template('mobile/stock_out_new.html', project=project,
                           usage_units=usage_units, work_numbers=work_numbers,
                           stock_out_types=stock_out_types)


def _handle_stock_out_submit(project):
    """处理出库单提交"""
    try:
        usage_unit_id = request.form.get('usage_unit_id', type=int) or None
        team_id = request.form.get('team_id', type=int) or None
        work_number_id = request.form.get('work_number_id', type=int) or None
        stock_out_type = request.form.get('stock_out_type', '工程领用')
        issue_location = (request.form.get('issue_location', '') or '').strip()
        purpose = (request.form.get('purpose', '') or '').strip()
        remark = (request.form.get('remark', '') or '').strip()

        if not usage_unit_id:
            flash('请选择领料单位', 'danger')
            return redirect(url_for('mobile.stock_out_new'))

        stock_out = StockOut(
            project_id=project.id,
            code=_gen_code_with_seq('CK', project.id, StockOut),
            stock_out_date=date.today(),
            stock_out_type=stock_out_type,
            usage_unit_id=usage_unit_id,
            team_id=team_id,
            work_number_id=work_number_id,
            issue_location=issue_location,
            purpose=purpose,
            operator=current_user.name or current_user.username,
            remark=(remark + ' [移动端]') if remark else '[移动端]'
        )
        db.session.add(stock_out)
        db.session.flush()

        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')

        # 库存校验
        insufficient = []
        items_data = []
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            try:
                qty = to_decimal(quantities[idx] if idx < len(quantities) else 0)
            except Exception:
                continue
            if qty <= 0:
                continue

            mat_id = int(mid)
            inv = Inventory.query.filter_by(
                project_id=project.id, material_id=mat_id).first()
            current_stock = float(inv.quantity) if inv else 0
            if float(qty) > current_stock:
                mat = Material.query.get(mat_id)
                name = mat.name if mat else f'ID={mat_id}'
                insufficient.append(f'{name} 当前库存 {current_stock}')
                continue

            items_data.append((mat_id, qty))

        if insufficient:
            db.session.rollback()
            flash('库存不足：' + '；'.join(insufficient), 'danger')
            return redirect(url_for('mobile.stock_out_new'))

        total_qty = 0
        total_amount = 0
        for mat_id, qty in items_data:
            avg_price = InventoryCostService.get_weighted_average_price(
                project_id=project.id, material_id=mat_id)
            amount = float(qty) * float(avg_price)
            item = StockOutItem(
                stock_out_id=stock_out.id,
                material_id=mat_id,
                quantity=qty,
                unit_price=avg_price,
                amount=amount
            )
            db.session.add(item)
            total_qty = to_decimal(total_qty) + qty
            total_amount = to_decimal(total_amount) + to_decimal(amount)

        stock_out.total_quantity = total_qty
        stock_out.total_amount = total_amount

        # 审批状态
        try:
            from app.approval.service import is_approval_enabled
            approval_enabled = is_approval_enabled('stockout')
        except Exception:
            approval_enabled = False

        if approval_enabled:
            stock_out.approval_status = 'draft'
        else:
            stock_out.approval_status = 'passed'
            # 立即扣减库存
            for item in stock_out.items:
                InventoryCostService.apply_outbound(
                    project_id=project.id,
                    material_id=item.material_id,
                    quantity=item.quantity
                )

        db.session.commit()

        # 处理照片上传
        photos = request.files.getlist('photos[]')
        _validate_upload_files(photos)
        uploaded = 0
        for photo in photos:
            if photo and photo.filename:
                att, err = upload_attachment(photo, 'stock_out',
                                              biz_id=stock_out.id, project_id=project.id)
                if err:
                    flash(f'图片 {photo.filename} 上传失败：{err}', 'warning')
                else:
                    uploaded += 1

        # 处理手写签名
        signatures = request.files.getlist('signatures[]')
        _validate_upload_files(signatures)
        sig_uploaded = 0
        for sig in signatures:
            if sig and sig.filename:
                att, err = upload_attachment(sig, 'stock_out_signature',
                                              biz_id=stock_out.id, project_id=project.id)
                if err:
                    flash(f'签名上传失败：{err}', 'warning')
                else:
                    sig_uploaded += 1

        flash(f'出库单 {stock_out.code} 提交成功' +
              (f'，上传 {uploaded} 张图片' if uploaded else '') +
              (f'，签名 {sig_uploaded} 张' if sig_uploaded else ''), 'success')
        return redirect(url_for('mobile.records', type='out'))

    except Exception as e:
        db.session.rollback()
        flash(f'提交失败：{e}', 'danger')
        return redirect(url_for('mobile.stock_out_new'))


# ============== 库存查询 ==============

@bp.route('/inventory')
@login_required
def inventory():
    """库存查询"""
    project = _get_current_project()
    if not project:
        flash('请先选择项目', 'warning')
        return redirect(url_for('mobile.profile'))

    keyword = (request.args.get('keyword', '') or '').strip()
    query = db.session.query(
        Material,
        Category,
        Inventory.quantity.label('stock_qty'),
        Inventory.estimated_amount.label('est_amt'),
        Inventory.actual_amount.label('act_amt')
    ).outerjoin(Category, Category.id == Material.category_id).outerjoin(
        Inventory, (Inventory.material_id == Material.id) &
                   (Inventory.project_id == project.id)
    ).filter(Material.project_id == project.id)

    # 排除已删除的物资（如有 is_deleted 字段）
    if hasattr(Material, 'is_deleted'):
        query = query.filter(Material.is_deleted == False)

    if keyword:
        query = query.filter(or_(
            Material.name.contains(keyword),
            Material.code.contains(keyword)
        ))

    results = query.order_by(Material.name).limit(100).all()

    return render_template('mobile/inventory.html', project=project,
                           keyword=keyword, results=results)


# ============== 我的记录 ==============

@bp.route('/records')
@login_required
def records():
    """我的记录列表"""
    project = _get_current_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    record_type = request.args.get('type', 'in')
    date_filter = request.args.get('date', 'today')
    status_filter = request.args.get('status', '')

    today = date.today()
    date_from = None
    if date_filter == 'today':
        date_from = today
    elif date_filter == 'week':
        date_from = today - timedelta(days=today.weekday())
    elif date_filter == 'month':
        date_from = today.replace(day=1)

    if record_type == 'out':
        query = StockOut.query.filter_by(project_id=project.id)
        if date_from:
            query = query.filter(StockOut.stock_out_date >= date_from)
        if status_filter:
            query = query.filter_by(approval_status=status_filter)
        records_list = query.order_by(StockOut.created_at.desc()).limit(50).all()
    else:
        query = StockIn.query.filter_by(project_id=project.id)
        if date_from:
            query = query.filter(StockIn.stock_in_date >= date_from)
        if status_filter:
            query = query.filter_by(approval_status=status_filter)
        records_list = query.order_by(StockIn.created_at.desc()).limit(50).all()

    return render_template('mobile/records.html', project=project,
                           records=records_list, record_type=record_type,
                           date_filter=date_filter, status_filter=status_filter)


# ============== 我的 ==============

@bp.route('/profile')
@login_required
def profile():
    """我的页面"""
    project = _get_current_project()
    visible_projects = current_user.get_visible_projects()

    pending_qc = 0
    if project:
        pending_qc = StockIn.query.filter_by(
            project_id=project.id, quality_status='pending', status='approved'
        ).count()

    # 待审批数量
    pending_approval_count = 0
    try:
        from app.approval.service import get_pending_count
        pending_approval_count = get_pending_count(current_user.id)
    except Exception:
        pass

    # 未读消息数
    unread_msg_count = 0
    try:
        from app.models import Message
        unread_msg_count = Message.query.filter_by(
            user_id=current_user.id, is_read=False).count()
    except Exception:
        pass

    # 离线待同步数量（仅前端可读，这里给0占位）
    offline_pending = 0

    return render_template('mobile/profile.html', project=project,
                           visible_projects=visible_projects,
                           pending_qc=pending_qc,
                           pending_approval_count=pending_approval_count,
                           unread_msg_count=unread_msg_count,
                           offline_pending=offline_pending)


@bp.route('/switch_project/<int:pid>')
@login_required
def switch_project(pid):
    """切换当前项目"""
    if not current_user.can_access_project(pid):
        flash('无权限访问该项目', 'danger')
        return redirect(url_for('mobile.profile'))
    project = Project.query.get(pid)
    if not project or project.is_archived:
        flash('项目不存在或已归档', 'danger')
        return redirect(url_for('mobile.profile'))
    session['current_project_id'] = pid
    session['current_project_name'] = project.name
    flash(f'已切换到项目：{project.name}', 'success')
    return redirect(url_for('mobile.portal_home'))


@bp.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    """修改密码"""
    if request.method == 'POST':
        old_password = request.form.get('old_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not check_password_hash(current_user.password_hash, old_password):
            flash('当前密码错误', 'danger')
        elif new_password != confirm_password:
            flash('两次输入的新密码不一致', 'danger')
        elif len(new_password) < 6:
            flash('新密码长度不能少于6位', 'danger')
        else:
            current_user.password_hash = generate_password_hash(
                new_password, method='pbkdf2:sha256')
            db.session.commit()
            log_operation('修改', module='系统',
                          description=f'用户 {current_user.username} 移动端修改密码')
            flash('密码修改成功，请重新登录', 'success')
            return redirect(url_for('mobile.logout'))

        return redirect(url_for('mobile.change_password'))

    return render_template('mobile/change_password.html')


@bp.route('/offline_data')
@login_required
def offline_data_new():
    """离线数据管理页面"""
    project = _get_current_project()
    return render_template('mobile/offline_data_new.html',
                           project=project, online=True)


# ============== 盘点功能 ==============

@bp.route('/stock_check')
@login_required
def stock_check_list():
    """盘点单列表"""
    project = _get_current_project()
    if not project:
        flash('请先选择项目', 'warning')
        return redirect(url_for('mobile.profile'))

    from app.models import StockCheck
    items = StockCheck.query.filter_by(project_id=project.id) \
        .order_by(StockCheck.created_at.desc()).limit(50).all()
    return render_template('mobile/stock_check_list.html',
                           project=project, items=items)


@bp.route('/stock_check/<int:check_id>')
@login_required
def stock_check_detail(check_id):
    """盘点详情页"""
    from app.models import StockCheck, Inventory
    check = StockCheck.query.get_or_404(check_id)
    if not current_user.can_access_project(check.project_id):
        flash('无权限访问', 'danger')
        return redirect(url_for('mobile.stock_check_list'))

    # 确保所有物资都有盘点明细
    project_materials = Material.query.filter_by(project_id=check.project_id).all()
    if hasattr(Material, 'is_deleted'):
        project_materials = [m for m in project_materials if not m.is_deleted]
    existing_map = {item.material_id: item for item in check.items.all()}
    for m in project_materials:
        if m.id not in existing_map:
            inv = Inventory.query.filter_by(
                project_id=check.project_id, material_id=m.id).first()
            from app import db as _db
            from app.models import StockCheckItem
            new_item = StockCheckItem(
                check_id=check.id,
                material_id=m.id,
                material_name=m.name,
                specification=m.specification or '',
                unit=m.unit or '',
                book_qty=float(inv.quantity) if inv else 0,
                actual_qty=None,
                diff_reason=''
            )
            _db.session.add(new_item)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    # 重新查询 items
    items = check.items.all()
    return render_template('mobile/stock_check_detail.html',
                           project=check.project, check=check, items=items)


@bp.route('/stock_check/<int:check_id>/save', methods=['POST'])
@login_required
def stock_check_save(check_id):
    """保存盘点明细"""
    from app.models import StockCheck, StockCheckItem
    check = StockCheck.query.get_or_404(check_id)
    if not current_user.can_access_project(check.project_id):
        return jsonify({'success': False, 'msg': '无权限'}), 403

    item_ids = request.form.getlist('item_id[]')
    actual_qtys = request.form.getlist('actual_qty[]')
    diff_reasons = request.form.getlist('diff_reason[]')

    saved = 0
    for idx, item_id in enumerate(item_ids):
        if not item_id:
            continue
        item = StockCheckItem.query.get(int(item_id))
        if not item or item.check_id != check.id:
            continue
        try:
            actual = actual_qtys[idx] if idx < len(actual_qtys) else ''
            reason = diff_reasons[idx] if idx < len(diff_reasons) else ''
            if actual == '' or actual is None:
                item.actual_qty = None
            else:
                item.actual_qty = float(actual)
            item.diff_reason = (reason or '').strip()
            saved += 1
        except Exception:
            continue

    # 更新盘点状态：如果所有明细都有 actual_qty 则自动完成
    all_filled = all(it.actual_qty is not None for it in check.items.all())
    if all_filled and check.status == 'ongoing':
        check.status = 'confirmed'

    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'saved': saved,
            'status': check.status,
            'all_filled': all_filled
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)}), 500


@bp.route('/stock_check/<int:check_id>/confirm', methods=['POST'])
@login_required
def stock_check_confirm(check_id):
    """确认盘点，调整库存"""
    from app.models import StockCheck
    from app.stock_check.routes import _apply_inventory_add, _apply_inventory_sub
    check = StockCheck.query.get_or_404(check_id)
    if not current_user.can_access_project(check.project_id):
        return jsonify({'success': False, 'msg': '无权限'}), 403
    if check.status == 'confirmed':
        return jsonify({'success': False, 'msg': '盘点已确认，不能重复操作'}), 400

    try:
        for item in check.items.all():
            if item.actual_qty is None:
                continue
            diff = float(item.actual_qty) - float(item.book_qty)
            if diff > 0:
                _apply_inventory_add(check.project_id, item.material_id, diff)
            elif diff < 0:
                _apply_inventory_sub(check.project_id, item.material_id, -diff)
        check.status = 'confirmed'
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)}), 500


# ============== 审批功能 ==============

@bp.route('/approvals')
@login_required
def approvals():
    """待我审批列表"""
    from app.approval.service import get_my_pending_approvals, get_pending_count
    from flask import request as req
    biz_type = req.args.get('type', '')
    instances = get_my_pending_approvals(current_user.id)
    if biz_type:
        instances = [i for i in instances if i.biz_type == biz_type]
    pending_count = get_pending_count(current_user.id)
    return render_template('mobile/approvals.html',
                           instances=instances,
                           pending_count=pending_count,
                           current_type=biz_type)


@bp.route('/approval/<int:instance_id>')
@login_required
def approval_detail(instance_id):
    """审批详情"""
    from app.models import ApprovalInstance, ApprovalRecord
    instance = ApprovalInstance.query.get_or_404(instance_id)
    records = ApprovalRecord.query.filter_by(instance_id=instance_id) \
        .order_by(ApprovalRecord.approve_time.asc()).all()
    # 取业务单据对象
    biz_obj = None
    biz_items = []
    try:
        from app.approval.service import get_biz_obj, get_biz_title
        biz_obj = get_biz_obj(instance.biz_type, instance.biz_id)
        instance.biz_title = get_biz_title(instance.biz_type, instance.biz_id)
        # 预先将 items 转换为列表（兼容 dynamic/lazy 关系）
        if biz_obj is not None and hasattr(biz_obj, 'items'):
            try:
                biz_items = list(biz_obj.items.all())
            except Exception:
                try:
                    biz_items = list(biz_obj.items)
                except Exception:
                    biz_items = []
    except Exception:
        pass
    return render_template('mobile/approval_detail.html',
                           instance=instance, biz_obj=biz_obj,
                           biz_items=biz_items, records=records)


@bp.route('/approval/<int:instance_id>/approve', methods=['POST'])
@login_required
def approval_approve(instance_id):
    """同意审批"""
    from app.approval.service import approve
    opinion = (request.form.get('opinion', '') or '').strip()
    success, msg = approve(instance_id, approver_id=current_user.id, opinion=opinion)
    if success:
        flash('已同意', 'success')
    else:
        flash('操作失败：' + msg, 'danger')
    return redirect(url_for('mobile.approvals'))


@bp.route('/approval/<int:instance_id>/reject', methods=['POST'])
@login_required
def approval_reject(instance_id):
    """驳回审批"""
    from app.approval.service import reject
    reason = (request.form.get('reason', '') or '').strip()
    if not reason:
        flash('请填写驳回原因', 'danger')
        return redirect(url_for('mobile.approval_detail', instance_id=instance_id))
    success, msg = reject(instance_id, approver_id=current_user.id, reason=reason)
    if success:
        flash('已驳回', 'success')
    else:
        flash('操作失败：' + msg, 'danger')
    return redirect(url_for('mobile.approvals'))


# ============== 钢材换算工具 ==============

@bp.route('/steel_calc')
@login_required
def steel_calc():
    """钢材理论重量计算"""
    from app.models import SteelSpecWeight
    specs = SteelSpecWeight.query.order_by(SteelSpecWeight.spec_type,
                                           SteelSpecWeight.spec_name).all()
    return render_template('mobile/steel_calc.html', specs=specs)


# ============== 站内消息 ==============

@bp.route('/messages')
@login_required
def messages():
    """站内消息中心"""
    from app.models import Message
    msgs = Message.query.filter_by(user_id=current_user.id) \
        .order_by(Message.created_at.desc()).limit(50).all()
    unread_count = sum(1 for m in msgs if not m.is_read)
    return render_template('mobile/messages.html',
                           messages=msgs, unread_count=unread_count)


@bp.route('/messages/<int:msg_id>/read', methods=['POST'])
@login_required
def message_read(msg_id):
    """标记消息已读"""
    from app.models import Message
    msg = Message.query.get_or_404(msg_id)
    if msg.user_id != current_user.id:
        return jsonify({'success': False, 'msg': '无权限'}), 403
    msg.is_read = True
    db.session.commit()
    return jsonify({'success': True})


@bp.route('/messages/read_all', methods=['POST'])
@login_required
def messages_read_all():
    """全部已读"""
    from app.models import Message
    Message.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({'success': True})




# ============== 采购申请 ==============

@bp.route('/purchase-request', methods=['GET', 'POST'])
@login_required
def purchase_request():
    """移动端采购申请（复用领料申请模型和审批流程）"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        apply_dept = request.form.get('apply_dept', '').strip()
        demand_date_str = request.form.get('demand_date', '')
        try:
            demand_date = datetime.strptime(demand_date_str, '%Y-%m-%d').date()
        except Exception:
            demand_date = None
        remark = request.form.get('remark', '').strip()
        supplier_id = request.form.get('supplier_id', type=int) or None

        # 补充供应商信息到备注
        if supplier_id:
            supplier = Supplier.query.get(supplier_id)
            if supplier:
                remark = f'供应商: {supplier.name}\n' + remark if remark else f'供应商: {supplier.name}'

        pr = PurchaseRequisition(
            pr_no=_m_gen_pr_no(project.id),
            project_id=project.id,
            apply_dept=apply_dept,
            apply_user=current_user.name or current_user.username,
            apply_date=date.today(),
            demand_date=demand_date,
            status='draft',
            remark=remark,
        )
        db.session.add(pr)
        db.session.flush()

        # 明细
        material_ids = request.form.getlist('material_id[]')
        apply_qtys = request.form.getlist('apply_qty[]')
        purposes = request.form.getlist('purpose[]')
        est_prices = request.form.getlist('est_price[]')
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = _m_to_float(apply_qtys[idx] if idx < len(apply_qtys) else 0)
            if qty == 0:
                continue
            mat = Material.query.get(int(mid))
            purpose = (purposes[idx] if idx < len(purposes) else '').strip()
            # 附加预计单价到用途
            est_price = _m_to_float(est_prices[idx] if idx < len(est_prices) else 0)
            if est_price > 0:
                purpose = f'预计单价: ¥{est_price:.2f} | ' + purpose if purpose else f'预计单价: ¥{est_price:.2f}'
            item = PurchaseRequisitionItem(
                pr_id=pr.id, material_id=int(mid),
                material_name=mat.name if mat else '',
                specification=mat.specification if mat else '',
                unit=mat.unit if mat else '',
                apply_qty=qty, purpose=purpose,
                converted=False,
            )
            db.session.add(item)

        db.session.commit()

        # 自动提交审批
        if request.form.get('submit_type') == 'submit':
            from app.approval.service import submit_approval
            success, msg, instance = submit_approval('purchase_requisition', pr.id,
                                                     applicant_id=current_user.id)
            if success:
                pr.status = 'pending'
                db.session.commit()
                flash('采购申请已提交审批', 'success')
            else:
                flash(f'提交审批失败：{msg}', 'danger')
            return redirect(url_for('mobile.pr_detail', id=pr.id))

        flash('采购申请已保存为草稿', 'success')
        return redirect(url_for('mobile.pr_detail', id=pr.id))

    suppliers = Supplier.query.filter_by(project_id=project.id).order_by(Supplier.name).all()
    return render_template('mobile/purchase_request.html', project=project,
                           suppliers=suppliers,
                           today=date.today().isoformat(),
                           default_pr_no=_m_gen_pr_no(project.id))


# ============== API 接口 ==============

@bp.route('/api/materials/search')
@mobile_auth_required
def api_materials_search():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """搜索物资（含库存信息）"""
    project = _get_current_project()
    if not project:
        return jsonify([])
    kw = (request.args.get('q', '') or '').strip()
    q = Material.query.filter_by(project_id=project.id)
    if kw:
        q = q.filter(or_(Material.name.contains(kw), Material.code.contains(kw)))
    materials = q.order_by(Material.name).limit(30).all()
    result = []
    for m in materials:
        inv = Inventory.query.filter_by(
            project_id=project.id, material_id=m.id).first()
        result.append({
            'id': m.id,
            'name': m.name,
            'code': m.code or '',
            'spec': m.specification or '',
            'unit': m.unit or '',
            'category': m.category.name if m.category else '',
            'stock': float(inv.quantity) if inv else 0
        })
    return jsonify(result)


@bp.route('/api/suppliers/search')
@mobile_auth_required
def api_suppliers_search():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """搜索供应商"""
    project = _get_current_project()
    if not project:
        return jsonify([])
    kw = (request.args.get('q', '') or '').strip()
    q = Supplier.query.filter_by(project_id=project.id)
    if kw:
        q = q.filter(Supplier.name.contains(kw))
    suppliers = q.order_by(Supplier.name).limit(30).all()
    return jsonify([{
        'id': s.id, 'name': s.name,
        'contact': s.contact_person or '',
        'phone': s.phone or ''
    } for s in suppliers])


@bp.route('/api/teams/<int:unit_id>')
@mobile_auth_required
def api_teams(unit_id):
    """获取班组列表"""
    teams = UnitTeam.query.filter_by(unit_id=unit_id).all()
    return jsonify([{
        'id': t.id, 'team_name': t.team_name,
        'picker_name': t.picker_name or '',
        'phone': t.phone or ''
    } for t in teams])


@bp.route('/api/stock_check')
@mobile_auth_required
def api_stock_check():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """查询当前物资库存"""
    project = _get_current_project()
    if not project:
        return jsonify({'stock': 0})
    material_id = request.args.get('material_id', type=int)
    if not material_id:
        return jsonify({'stock': 0})
    inv = Inventory.query.filter_by(
        project_id=project.id, material_id=material_id).first()
    return jsonify({'stock': float(inv.quantity) if inv else 0})


# ============================================================
# 第三部分扩展：设备管理 + 周转材管理 + 设备巡检（二期/三期补充）
# ============================================================

def _m_require_project():
    """移动端设备/周转材/巡检路由专用项目检查"""
    project = _get_current_project()
    if not project:
        return None
    return project


def _m_ai_vision_enabled():
    """判断AI视觉识别是否启用（与PC端共用同一套配置）

    条件：系统启用AI + 启用视觉识别
    返回 bool。前端按钮根据该返回值决定是否显示。
    """
    try:
        return (get_config('ai_enabled', 'false') == 'true'
                and get_config('ai_vision_enabled', 'false') == 'true')
    except Exception:
        return False


def _m_parse_date(value, fmt='%Y-%m-%d'):
    if not value:
        return None
    try:
        return datetime.strptime(value, fmt).date()
    except (ValueError, TypeError):
        return None


def _m_to_float(value, default=0):
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _m_to_int(value, default=0):
    try:
        if value is None or value == '':
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _m_gen_equipment_code(project_id):
    """生成设备编号：SB-{project_id}-{count+1:04d}"""
    count = Equipment.query.filter_by(project_id=project_id).count()
    return f'SB-{project_id}-{count + 1:04d}'


def _m_gen_turnover_code(project_id):
    """生成周转材编号：ZZ-{project_id}-{count+1:04d}"""
    count = TurnoverMaterial.query.filter_by(project_id=project_id).count()
    return f'ZZ-{project_id}-{count + 1:04d}'


def _m_gen_inspection_task_no(project_id):
    """生成巡检任务编号：XJ-{project_id}-{YYYYMMDD}-{3位序号}"""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f'XJ-{project_id}-{today}-'
    existing = EquipmentInspectionTask.query.filter(
        EquipmentInspectionTask.task_no.like(f'{prefix}%')
    ).count()
    return f'{prefix}{existing + 1:03d}'


# ============== 设备台账 ==============

@bp.route('/equipment')
@login_required
def equipment_list():
    """设备台账列表"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    keyword = (request.args.get('keyword', '') or '').strip()
    status = (request.args.get('status', '') or '').strip()
    source_type = (request.args.get('source_type', '') or '').strip()
    category = (request.args.get('category', '') or '').strip()

    q = Equipment.query.filter_by(project_id=project.id, is_deleted=False)
    if keyword:
        q = q.filter(Equipment.name.contains(keyword) | Equipment.code.contains(keyword))
    if status:
        q = q.filter_by(status=status)
    if source_type:
        q = q.filter_by(source_type=source_type)
    if category:
        q = q.filter_by(category=category)
    items = q.order_by(Equipment.created_at.desc()).limit(200).all()

    stats = {
        'total': len(items),
        'in_use': sum(1 for e in items if e.status == 'in_use'),
        'idle': sum(1 for e in items if e.status == 'idle'),
        'repairing': sum(1 for e in items if e.status == 'repairing'),
        'exited': sum(1 for e in items if e.status == 'exited'),
    }
    status_items = get_dict_items('equipment_status')
    source_items = get_dict_items('equipment_source')
    category_items = get_dict_items('equipment_category')
    return render_template('mobile/equipment_list.html',
                           project=project, items=items, stats=stats,
                           keyword=keyword, status=status,
                           source_type=source_type, category=category,
                           status_items=status_items, source_items=source_items,
                           category_items=category_items, active_tab='query')


@bp.route('/equipment/<int:eid>')
@login_required
def equipment_detail(eid):
    """设备详情"""
    eq = Equipment.query.get_or_404(eid)
    maintenances = EquipmentMaintenance.query.filter_by(equipment_id=eid).order_by(
        EquipmentMaintenance.maintain_date.desc()
    ).limit(20).all()
    status_logs = EquipmentStatusLog.query.filter_by(equipment_id=eid).order_by(
        EquipmentStatusLog.created_at.desc()
    ).limit(10).all()
    inspections = EquipmentInspectionRecord.query.filter_by(equipment_id=eid).order_by(
        EquipmentInspectionRecord.inspect_date.desc()
    ).limit(10).all()
    status_items = get_dict_items('equipment_status')
    return render_template('mobile/equipment_detail.html',
                           eq=eq, maintenances=maintenances,
                           status_logs=status_logs, inspections=inspections,
                           status_items=status_items)


@bp.route('/equipment/create', methods=['GET', 'POST'])
@login_required
def equipment_create():
    """设备进场登记"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    if request.method == 'POST':
        try:
            source_type = request.form.get('source_type', 'self')
            eq = Equipment(
                project_id=project.id,
                code=request.form.get('code', '').strip() or _m_gen_equipment_code(project.id),
                name=request.form.get('name', '').strip(),
                source_type=source_type,
                category=request.form.get('category', '') or None,
                specification=request.form.get('specification', '') or None,
                manufacturer=request.form.get('manufacturer', '') or None,
                department=request.form.get('department', '') or None,
                responsible=request.form.get('responsible', '') or None,
                location=request.form.get('location', '') or None,
                status='in_use',
                remark=(request.form.get('remark', '') or '').strip() or None,
            )
            # 自有设备字段
            if source_type == 'self':
                eq.purchase_date = _m_parse_date(request.form.get('purchase_date')) or date.today()
                eq.original_value = _m_to_float(request.form.get('original_value'), 0)
                eq.use_years = _m_to_int(request.form.get('use_years'), 0)
                eq.residual_rate = _m_to_float(request.form.get('residual_rate'), 5)
            # 租赁设备字段
            elif source_type == 'rent':
                eq.supplier_id = _m_to_int(request.form.get('supplier_id'), 0) or None
                eq.rent_type = request.form.get('rent_type', '') or None
                eq.rent_unit_price = _m_to_float(request.form.get('rent_unit_price'), 0)
                eq.rent_period = _m_to_int(request.form.get('rent_period'), 0) or None
                eq.entry_date = _m_parse_date(request.form.get('entry_date')) or date.today()
                eq.expected_exit_date = _m_parse_date(request.form.get('expected_exit_date'))
                eq.entry_exit_fee = _m_to_float(request.form.get('entry_exit_fee'), 0)
                eq.deposit = _m_to_float(request.form.get('deposit'), 0)
            # 劳务队自带字段
            elif source_type == 'labor':
                eq.labor_team = request.form.get('labor_team', '') or None
                eq.entry_date = _m_parse_date(request.form.get('entry_date')) or date.today()

            db.session.add(eq)
            db.session.flush()

            # 写状态变更日志
            log = EquipmentStatusLog(
                equipment_id=eq.id,
                old_status=None,
                new_status='in_use',
                exit_date=None,
                operator_id=current_user.id if current_user.is_authenticated else None,
                operator_name=current_user.name or current_user.username if current_user.is_authenticated else None,
                remark='设备进场登记 [移动端]'
            )
            db.session.add(log)
            db.session.commit()

            # 上传进场照片
            photos = request.files.getlist('photos[]')
            _validate_upload_files(photos)
            uploaded = 0
            for photo in photos:
                if photo and photo.filename:
                    att, err = upload_attachment(photo, 'equipment',
                                                  biz_id=eq.id, project_id=project.id)
                    if not err:
                        uploaded += 1
            log_operation('新增', module='设备台账',
                          description=f'移动端新增设备 {eq.name}({eq.code})')
            flash(f'设备 {eq.code} 进场登记成功' +
                  (f'，上传 {uploaded} 张照片' if uploaded else ''), 'success')
            return redirect(url_for('mobile.equipment_detail', eid=eq.id))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{e}', 'danger')
            return redirect(url_for('mobile.equipment_create'))

    default_code = _m_gen_equipment_code(project.id)
    suppliers = Supplier.query.filter_by(project_id=project.id).order_by(Supplier.name).all()
    return render_template('mobile/equipment_create.html',
                           project=project, default_code=default_code,
                           suppliers=suppliers,
                           source_items=get_dict_items('equipment_source'),
                           category_items=get_dict_items('equipment_category'),
                           rent_type_items=get_dict_items('rent_type'),
                           today=date.today())


@bp.route('/equipment/<int:eid>/exit', methods=['POST'])
@login_required
def equipment_exit(eid):
    """设备退场登记"""
    eq = Equipment.query.get_or_404(eid)
    exit_date = _m_parse_date(request.form.get('exit_date')) or date.today()
    reason = (request.form.get('reason', '') or '').strip()
    meter_reading = (request.form.get('meter_reading', '') or '').strip()

    old_status = eq.status
    eq.status = 'exited'
    eq.exit_date = exit_date
    if meter_reading:
        eq.remark = (eq.remark or '') + f'\n[退场仪表读数:{meter_reading}]'

    log = EquipmentStatusLog(
        equipment_id=eq.id,
        old_status=old_status,
        new_status='exited',
        exit_date=exit_date,
        operator_id=current_user.id if current_user.is_authenticated else None,
        operator_name=current_user.name or current_user.username if current_user.is_authenticated else None,
        remark=reason or '设备退场 [移动端]'
    )
    db.session.add(log)
    db.session.commit()

    # 上传退场照片
    photos = request.files.getlist('photos[]')
    _validate_upload_files(photos)
    for photo in photos:
        if photo and photo.filename:
            upload_attachment(photo, 'equipment_exit',
                              biz_id=eq.id, project_id=eq.project_id)

    log_operation('退场', module='设备台账',
                  description=f'设备 {eq.name}({eq.code}) 退场')
    flash(f'设备 {eq.code} 已退场', 'success')
    return redirect(url_for('mobile.equipment_detail', eid=eq.id))


@bp.route('/equipment/<int:eid>/maintenance', methods=['GET', 'POST'])
@login_required
def equipment_maintenance(eid):
    """设备维修保养登记"""
    eq = Equipment.query.get_or_404(eid)
    if request.method == 'POST':
        try:
            m_type = request.form.get('maintain_type', '维修')
            m = EquipmentMaintenance(
                equipment_id=eid,
                maintain_date=_m_parse_date(request.form.get('maintain_date')) or date.today(),
                maintain_type=m_type,
                content=(request.form.get('content', '') or '').strip(),
                cost=_m_to_float(request.form.get('cost'), 0),
                vendor=(request.form.get('vendor', '') or '').strip(),
                next_maintain_date=_m_parse_date(request.form.get('next_maintain_date')),
                operator=(request.form.get('operator', '') or '').strip() or
                          (current_user.name or current_user.username),
                remark=(request.form.get('remark', '') or '').strip()
            )
            db.session.add(m)
            # 维修类型自动变更状态
            if m_type == '维修':
                old_status = eq.status
                eq.status = 'repairing'
                slog = EquipmentStatusLog(
                    equipment_id=eid,
                    old_status=old_status,
                    new_status='repairing',
                    operator_id=current_user.id if current_user.is_authenticated else None,
                    operator_name=current_user.name or current_user.username if current_user.is_authenticated else None,
                    remark=f'维修登记 #{m.id} [移动端]'
                )
                db.session.add(slog)
            db.session.commit()

            # 上传维修前后照片
            photos = request.files.getlist('photos[]')
            _validate_upload_files(photos)
            for photo in photos:
                if photo and photo.filename:
                    upload_attachment(photo, 'equipment_maintenance',
                                      biz_id=m.id, project_id=eq.project_id)

            log_operation('新增', module='设备维保',
                          description=f'设备 {eq.name} 维保登记({m_type})')
            flash(f'维保记录已添加', 'success')
            return redirect(url_for('mobile.equipment_detail', eid=eid))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{e}', 'danger')
            return redirect(url_for('mobile.equipment_maintenance', eid=eid))

    maintain_types = [('日常保养', '日常保养'), ('故障维修', '故障维修'), ('大修', '大修')]
    return render_template('mobile/equipment_maintenance.html',
                           eq=eq, maintain_types=maintain_types,
                           today=date.today())


@bp.route('/equipment/<int:eid>/change_status', methods=['POST'])
@login_required
def equipment_change_status(eid):
    """设备状态快速变更"""
    eq = Equipment.query.get_or_404(eid)
    new_status = (request.form.get('new_status', '') or '').strip()
    if not new_status:
        flash('请选择新状态', 'danger')
        return redirect(url_for('mobile.equipment_detail', eid=eid))
    old_status = eq.status
    eq.status = new_status
    log = EquipmentStatusLog(
        equipment_id=eid,
        old_status=old_status,
        new_status=new_status,
        operator_id=current_user.id if current_user.is_authenticated else None,
        operator_name=current_user.name or current_user.username if current_user.is_authenticated else None,
        remark='状态快速变更 [移动端]'
    )
    db.session.add(log)
    db.session.commit()
    log_operation('状态变更', module='设备台账',
                  description=f'设备 {eq.name} 状态由 {old_status} 变更为 {new_status}')
    flash('设备状态已变更', 'success')
    return redirect(url_for('mobile.equipment_detail', eid=eid))


@bp.route('/equipment/qrcode/<int:eid>')
@login_required
def equipment_qrcode(eid):
    """设备二维码（移动端查看/扫码）"""
    eq = Equipment.query.get_or_404(eid)
    return render_template('mobile/equipment_qrcode.html', eq=eq, hide_tab=True)


@bp.route('/api/equipments/search')
@mobile_auth_required
def api_equipments_search():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """搜索设备"""
    project = _m_require_project()
    if not project:
        return jsonify([])
    kw = (request.args.get('q', '') or '').strip()
    q = Equipment.query.filter_by(project_id=project.id, is_deleted=False)
    if kw:
        q = q.filter(Equipment.name.contains(kw) | Equipment.code.contains(kw))
    items = q.order_by(Equipment.name).limit(30).all()
    return jsonify([{
        'id': e.id, 'name': e.name, 'code': e.code or '',
        'spec': e.specification or '', 'status': e.status or '',
        'source_type': e.source_type or ''
    } for e in items])


@bp.route('/api/equipment/by_code/<code>')
@mobile_auth_required
def api_equipment_by_code(code):
    """扫码查设备：根据编码返回设备ID"""
    project = _m_require_project()
    if not project:
        return jsonify({'success': False, 'message': '请先选择项目'}), 400
    code = (code or '').strip()
    eq = Equipment.query.filter_by(
        project_id=project.id, code=code, is_deleted=False
    ).first()
    if not eq:
        return jsonify({'success': False, 'message': f'未找到编码为 {code} 的设备'}), 404
    return jsonify({
        'success': True, 'id': eq.id, 'name': eq.name,
        'code': eq.code, 'status': eq.status
    })


# ============== 周转材管理 ==============

@bp.route('/turnover')
@login_required
def turnover_list():
    """周转材台账列表"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    keyword = (request.args.get('keyword', '') or '').strip()
    material_type = (request.args.get('material_type', '') or '').strip()
    q = TurnoverMaterial.query.filter_by(project_id=project.id)
    if keyword:
        q = q.filter(TurnoverMaterial.name.contains(keyword) | TurnoverMaterial.code.contains(keyword))
    if material_type:
        q = q.filter_by(material_type=material_type)
    items = q.order_by(TurnoverMaterial.created_at.desc()).limit(200).all()
    # 关联库存
    inv_map = {}
    invs = TurnoverInventory.query.filter_by(project_id=project.id).all()
    for inv in invs:
        inv_map[inv.material_id] = inv
    return render_template('mobile/turnover_list.html',
                           project=project, items=items, inv_map=inv_map,
                           keyword=keyword, material_type=material_type,
                           active_tab='query')


@bp.route('/turnover/<int:tid>')
@login_required
def turnover_detail(tid):
    """周转材详情"""
    tm = TurnoverMaterial.query.get_or_404(tid)
    inv = TurnoverInventory.query.filter_by(
        project_id=tm.project_id, material_id=tm.id).first()
    records = TurnoverRecord.query.filter_by(material_id=tm.id).order_by(
        TurnoverRecord.out_date.desc()
    ).limit(20).all()
    return render_template('mobile/turnover_detail.html',
                           tm=tm, inv=inv, records=records)


@bp.route('/turnover/borrow', methods=['GET', 'POST'])
@login_required
def turnover_borrow():
    """周转材领用登记"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    if request.method == 'POST':
        try:
            material_ids = request.form.getlist('material_id[]')
            qtys = request.form.getlist('qty[]')
            teams = request.form.getlist('team[]')
            borrower = (request.form.get('borrower', '') or '').strip() or (current_user.name or current_user.username)
            out_date = _m_parse_date(request.form.get('out_date')) or date.today()
            remark = (request.form.get('remark', '') or '').strip()
            created = 0
            for idx, mid in enumerate(material_ids):
                if not mid:
                    continue
                try:
                    qty = float(qtys[idx] if idx < len(qtys) else 0)
                except (ValueError, TypeError):
                    continue
                if qty <= 0:
                    continue
                team = teams[idx] if idx < len(teams) else ''
                inv = TurnoverInventory.query.filter_by(
                    project_id=project.id, material_id=int(mid)).first()
                if not inv or float(inv.quantity or 0) < qty:
                    flash(f'物资ID {mid} 库存不足', 'danger')
                    continue
                rec = TurnoverRecord(
                    project_id=project.id,
                    material_id=int(mid),
                    team=team or borrower,
                    qty=qty,
                    out_date=out_date,
                    expect_return_date=_m_parse_date(request.form.get('expect_return_date')),
                    remark=(remark + ' [移动端]') if remark else '[移动端]'
                )
                db.session.add(rec)
                inv.quantity = float(inv.quantity or 0) - qty
                if inv.material and inv.material.material_type == 'rental':
                    inv.rent_out_qty = float(inv.rent_out_qty or 0) + qty
                created += 1
            db.session.commit()

            # 上传现场照片
            photos = request.files.getlist('photos[]')
            _validate_upload_files(photos)
            for photo in photos:
                if photo and photo.filename:
                    upload_attachment(photo, 'turnover_borrow',
                                      biz_id=0, project_id=project.id)

            log_operation('领用', module='周转材',
                          description=f'移动端领用 {created} 条周转材')
            flash(f'领用登记成功，共 {created} 条', 'success')
            return redirect(url_for('mobile.turnover_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{e}', 'danger')
            return redirect(url_for('mobile.turnover_borrow'))

    materials = TurnoverMaterial.query.filter_by(project_id=project.id).order_by(
        TurnoverMaterial.name).all()
    return render_template('mobile/turnover_borrow.html',
                           project=project, materials=materials)


@bp.route('/turnover/record/<int:rid>/return', methods=['GET', 'POST'])
@login_required
def turnover_return(rid):
    """周转材归还登记"""
    rec = TurnoverRecord.query.get_or_404(rid)
    if request.method == 'POST':
        try:
            return_qty = float(request.form.get('return_qty', 0))
            lost_qty = float(request.form.get('lost_qty', 0))
            condition = (request.form.get('condition', '完好') or '完好')
            if return_qty + lost_qty > rec.using_qty:
                flash('归还+损耗数量不能超过在用数量', 'danger')
                return redirect(url_for('mobile.turnover_return', rid=rid))
            rec.returned_qty = float(rec.returned_qty or 0) + return_qty
            rec.lost_qty = float(rec.lost_qty or 0) + lost_qty
            if rec.returned_qty + rec.lost_qty >= rec.qty:
                rec.status = 'returned'
                rec.actual_return_date = _m_parse_date(request.form.get('return_date')) or date.today()
            else:
                rec.status = 'partial'
            inv = TurnoverInventory.query.filter_by(
                project_id=rec.project_id, material_id=rec.material_id).first()
            if inv:
                inv.quantity = float(inv.quantity or 0) + return_qty
                if inv.material and inv.material.material_type == 'rental':
                    inv.rent_out_qty = max(0, float(inv.rent_out_qty or 0) - return_qty)
            # 租赁费自动计算
            if rec.material and rec.material.material_type == 'rental':
                using_days = rec.using_days
                price = float(rec.material.rental_price or 0)
                unit_factor = 1 if rec.material.rental_unit == 'day' else 30
                total_returned = float(rec.returned_qty or 0)
                rec.rent_fee = round(using_days / unit_factor * price * total_returned, 2)
            db.session.commit()

            # 损坏拍照
            photos = request.files.getlist('photos[]')
            _validate_upload_files(photos)
            for photo in photos:
                if photo and photo.filename:
                    upload_attachment(photo, 'turnover_return',
                                      biz_id=rec.id, project_id=rec.project_id)

            log_operation('归还', module='周转材',
                          description=f'归还周转材 {rec.material.name if rec.material else rec.id}')
            flash('归还处理成功', 'success')
            return redirect(url_for('mobile.turnover_detail', tid=rec.material_id))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{e}', 'danger')
            return redirect(url_for('mobile.turnover_return', rid=rid))

    return render_template('mobile/turnover_return.html', rec=rec)


@bp.route('/turnover/rental')
@login_required
def turnover_rental():
    """周转材租赁结算查询"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    records = TurnoverRecord.query.filter(
        TurnoverRecord.project_id == project.id,
        TurnoverRecord.material_id.in_(
            db.session.query(TurnoverMaterial.id).filter_by(
                project_id=project.id, material_type='rental')
        )
    ).order_by(TurnoverRecord.out_date.desc()).limit(100).all()
    total_fee = sum(float(r.rent_fee or 0) for r in records)
    return render_template('mobile/turnover_rental.html',
                           project=project, records=records,
                           total_fee=round(total_fee, 2), active_tab='query')


@bp.route('/api/turnover/search')
@mobile_auth_required
def api_turnover_search():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """搜索周转材"""
    project = _m_require_project()
    if not project:
        return jsonify([])
    kw = (request.args.get('q', '') or '').strip()
    q = TurnoverMaterial.query.filter_by(project_id=project.id)
    if kw:
        q = q.filter(TurnoverMaterial.name.contains(kw) | TurnoverMaterial.code.contains(kw))
    items = q.order_by(TurnoverMaterial.name).limit(30).all()
    result = []
    for m in items:
        inv = TurnoverInventory.query.filter_by(
            project_id=project.id, material_id=m.id).first()
        result.append({
            'id': m.id, 'name': m.name, 'code': m.code or '',
            'spec': m.specification or '', 'unit': m.unit or '',
            'material_type': m.material_type or 'own',
            'stock': float(inv.quantity) if inv else 0
        })
    return jsonify(result)


@bp.route('/api/turnover/inventory/<int:mid>')
@mobile_auth_required
def api_turnover_inventory(mid):
    """周转材库存查询"""
    project = _m_require_project()
    if not project:
        return jsonify({'quantity': 0, 'rent_out_qty': 0})
    inv = TurnoverInventory.query.filter_by(
        project_id=project.id, material_id=mid).first()
    if inv:
        return jsonify({'quantity': float(inv.quantity or 0),
                        'rent_out_qty': float(inv.rent_out_qty or 0)})
    return jsonify({'quantity': 0, 'rent_out_qty': 0})


@bp.route('/turnover/qrcode/<int:tid>')
@login_required
def turnover_qrcode(tid):
    """周转材二维码展示页"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    tm = TurnoverMaterial.query.filter_by(
        id=tid, project_id=project.id).first_or_404()
    return render_template('mobile/turnover_qrcode.html',
                           tm=tm, project=project, hide_tab=True)


# ============== 设备巡检 ==============

@bp.route('/inspection')
@login_required
def inspection_list():
    """我的巡检任务列表"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    status = (request.args.get('status', '') or '').strip()
    q = EquipmentInspectionTask.query.filter_by(project_id=project.id)
    if status:
        q = q.filter_by(status=status)
    else:
        # 默认显示待巡检和逾期
        q = q.filter(EquipmentInspectionTask.status.in_(['pending', 'overdue']))
    tasks = q.order_by(EquipmentInspectionTask.plan_inspect_date.asc()).limit(50).all()

    stats = {
        'pending': EquipmentInspectionTask.query.filter_by(
            project_id=project.id, status='pending').count(),
        'done': EquipmentInspectionTask.query.filter_by(
            project_id=project.id, status='done').count(),
        'overdue': EquipmentInspectionTask.query.filter_by(
            project_id=project.id, status='overdue').count(),
    }
    return render_template('mobile/inspection_list.html',
                           project=project, tasks=tasks, stats=stats,
                           current_status=status)


@bp.route('/inspection/<int:tid>', methods=['GET', 'POST'])
@login_required
def inspection_form(tid):
    """巡检录入"""
    task = EquipmentInspectionTask.query.get_or_404(tid)
    eq = task.equipment
    plan = task.inspection
    if request.method == 'POST':
        try:
            overall = request.form.get('overall_status', 'normal')
            meter_reading = (request.form.get('meter_reading', '') or '').strip()
            issue_desc = (request.form.get('issue_desc', '') or '').strip()
            remark = (request.form.get('remark', '') or '').strip()
            # 收集巡检项结果
            items_result = {}
            for key in request.form:
                if key.startswith('item_'):
                    items_result[key] = request.form.get(key)
            rec = EquipmentInspectionRecord(
                project_id=task.project_id,
                task_id=task.id,
                equipment_id=task.equipment_id,
                inspect_date=_m_parse_date(request.form.get('inspect_date')) or date.today(),
                inspector_id=current_user.id if current_user.is_authenticated else None,
                inspector_name=current_user.name or current_user.username,
                overall_status=overall,
                items_result=json.dumps(items_result, ensure_ascii=False) if items_result else None,
                meter_reading=meter_reading or None,
                issue_desc=issue_desc or None,
                auto_maintenance=False,
                remark=(remark + ' [移动端]') if remark else '[移动端]'
            )
            db.session.add(rec)
            task.status = 'done'
            db.session.commit()

            # 异常自动生成维修工单
            if overall == 'abnormal' and issue_desc:
                m = EquipmentMaintenance(
                    equipment_id=task.equipment_id,
                    maintain_date=rec.inspect_date,
                    maintain_type='维修',
                    content=f'巡检异常自动生成：{issue_desc}',
                    operator=current_user.name or current_user.username,
                    remark=f'由巡检任务 {task.task_no} 自动生成'
                )
                db.session.add(m)
                # 设备状态变更为维修中
                old_status = eq.status
                eq.status = 'repairing'
                slog = EquipmentStatusLog(
                    equipment_id=eq.id,
                    old_status=old_status,
                    new_status='repairing',
                    operator_id=current_user.id if current_user.is_authenticated else None,
                    operator_name=current_user.name or current_user.username if current_user.is_authenticated else None,
                    remark=f'巡检异常自动生成维修工单 #{m.id}'
                )
                db.session.add(slog)
                rec.auto_maintenance = True
                db.session.commit()

            # 上传现场照片
            photos = request.files.getlist('photos[]')
            _validate_upload_files(photos)
            for photo in photos:
                if photo and photo.filename:
                    upload_attachment(photo, 'equipment_inspection',
                                      biz_id=rec.id, project_id=task.project_id)

            log_operation('巡检', module='设备巡检',
                          description=f'完成巡检任务 {task.task_no}')
            flash('巡检记录已提交' +
                  ('，已自动生成维修工单' if overall == 'abnormal' and issue_desc else ''),
                  'success')
            return redirect(url_for('mobile.inspection_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{e}', 'danger')
            return redirect(url_for('mobile.inspection_form', tid=tid))

    # 解析巡检项
    inspect_items = []
    if plan and plan.inspect_items:
        try:
            inspect_items = json.loads(plan.inspect_items)
        except Exception:
            inspect_items = [s.strip() for s in plan.inspect_items.split(',') if s.strip()]
    if not inspect_items:
        inspect_items = ['外观检查', '运行情况', '油位/液位', '仪表读数', '紧固件', '清洁度']
    return render_template('mobile/inspection_form.html',
                           task=task, eq=eq, plan=plan,
                           inspect_items=inspect_items,
                           today=date.today().strftime('%Y-%m-%d'))


@bp.route('/inspection/records')
@login_required
def inspection_records():
    """巡检记录查询"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    keyword = (request.args.get('keyword', '') or '').strip()
    q = EquipmentInspectionRecord.query.filter_by(project_id=project.id)
    if keyword:
        q = q.join(Equipment).filter(
            Equipment.name.contains(keyword) | Equipment.code.contains(keyword))
    records = q.order_by(EquipmentInspectionRecord.inspect_date.desc()).limit(50).all()
    return render_template('mobile/inspection_records.html',
                           project=project, records=records, keyword=keyword)


@bp.route('/inspection/scan')
@login_required
def inspection_scan():
    """扫码进入巡检录入"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    code = (request.args.get('code', '') or '').strip()
    if code:
        eq = Equipment.query.filter_by(
            project_id=project.id, code=code, is_deleted=False).first()
        if eq:
            # 查找该设备的待巡检任务
            task = EquipmentInspectionTask.query.filter_by(
                equipment_id=eq.id, status='pending'
            ).order_by(EquipmentInspectionTask.plan_inspect_date.asc()).first()
            if task:
                return redirect(url_for('mobile.inspection_form', tid=task.id))
            # 没有待巡检任务，跳转到设备详情
            flash('该设备暂无待巡检任务', 'info')
            return redirect(url_for('mobile.equipment_detail', eid=eq.id))
        flash(f'未找到编码为 {code} 的设备', 'warning')
    return render_template('mobile/inspection_scan.html', project=project)


# ============== 扫码直达 ==============

@bp.route('/scan_redirect')
@login_required
def scan_redirect():
    """扫码后识别类型并跳转：物资→库存查询，设备→设备详情，周转材→周转材详情"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    code = (request.args.get('code', '') or '').strip()
    if not code:
        return redirect(url_for('mobile.portal_home'))

    # 1. 优先匹配设备
    eq = Equipment.query.filter_by(
        project_id=project.id, code=code, is_deleted=False).first()
    if eq:
        return redirect(url_for('mobile.equipment_detail', eid=eq.id))

    # 2. 匹配周转材
    tm = TurnoverMaterial.query.filter_by(
        project_id=project.id, code=code).first()
    if tm:
        return redirect(url_for('mobile.turnover_detail', tid=tm.id))

    # 3. 匹配物资
    mat = Material.query.filter_by(
        project_id=project.id, code=code).first()
    if mat:
        return redirect(url_for('mobile.inventory') + '?keyword=' + code)

    flash(f'未找到编码 {code} 对应的设备/周转材/物资', 'warning')
    return redirect(url_for('mobile.inventory'))


# ============================================================
# 第四部分：商砼小票模块
# ============================================================

_CONCRETE_STRENGTH_GRADES = ['C15', 'C20', 'C25', 'C30', 'C35', 'C40', 'C45', 'C50']


def _m_gen_concrete_ticket_no(project_id):
    """生成商砼小票号：CT-{project_id}-{YYYYMMDD}-{seq:03d}"""
    today_str = datetime.now().strftime('%Y%m%d')
    prefix = f'CT-{project_id}-{today_str}-'
    existing = ConcreteTicket.query.filter(
        ConcreteTicket.ticket_no.like(f'{prefix}%')
    ).count()
    return f'{prefix}{existing + 1:03d}'


@bp.route('/concrete')
@login_required
def concrete_list():
    """商砼小票列表"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    start_date = (request.args.get('start_date', '') or '').strip()
    end_date = (request.args.get('end_date', '') or '').strip()
    strength_grade = (request.args.get('strength_grade', '') or '').strip()
    is_reconciled = (request.args.get('is_reconciled', '') or '').strip()

    q = ConcreteTicket.query.filter_by(project_id=project.id)
    if start_date:
        try:
            q = q.filter(ConcreteTicket.arrival_time >= datetime.strptime(start_date, '%Y-%m-%d'))
        except Exception:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            q = q.filter(ConcreteTicket.arrival_time <= end_dt)
        except Exception:
            pass
    if strength_grade:
        q = q.filter(ConcreteTicket.strength_grade == strength_grade)
    if is_reconciled in ('0', '1'):
        q = q.filter(ConcreteTicket.is_reconciled == (is_reconciled == '1'))
    tickets = q.order_by(ConcreteTicket.arrival_time.desc()).limit(100).all()

    # 今日统计
    today = date.today()
    today_count = ConcreteTicket.query.filter_by(project_id=project.id).filter(
        db.func.date(ConcreteTicket.arrival_time) == today
    ).count()
    today_volume = db.session.query(
        db.func.coalesce(db.func.sum(ConcreteTicket.volume), 0)
    ).filter_by(project_id=project.id).filter(
        db.func.date(ConcreteTicket.arrival_time) == today
    ).scalar()

    return render_template('mobile/concrete_list.html', project=project,
                           tickets=tickets, today_count=today_count,
                           today_volume=float(today_volume or 0),
                           strength_grades=_CONCRETE_STRENGTH_GRADES,
                           start_date=start_date, end_date=end_date,
                           strength_grade=strength_grade,
                           is_reconciled=is_reconciled,
                           active_tab='query')


def _save_concrete_ticket(project_id, data, files=None):
    """保存商砼小票（供 concrete_create 和离线同步共用）

    Args:
        project_id: 项目ID
        data: dict-like（支持 .get()），表单字段集合；
              离线同步时 data 可包含 photos(base64列表) 和 location(对象)
        files: 可选，在线上传的照片 FileStorage 列表

    Returns:
        (ticket, ticket_no, uploaded_count)
    """
    import base64
    from io import BytesIO
    from werkzeug.datastructures import FileStorage

    ticket_no = (data.get('ticket_no', '') or '').strip()
    if not ticket_no:
        ticket_no = _m_gen_concrete_ticket_no(project_id)

    supplier_id = data.get('supplier_id')
    try:
        supplier_id = int(supplier_id) if supplier_id not in (None, '') else None
    except (TypeError, ValueError):
        supplier_id = None
    supplier_name = (data.get('supplier_name', '') or '').strip() or None
    contract_id = data.get('contract_id')
    try:
        contract_id = int(contract_id) if contract_id not in (None, '') else None
    except (TypeError, ValueError):
        contract_id = None
    strength_grade = (data.get('strength_grade', '') or '').strip() or None
    material_id = data.get('material_id')
    try:
        material_id = int(material_id) if material_id not in (None, '') else None
    except (TypeError, ValueError):
        material_id = None
    pour_part = (data.get('pour_part', '') or '').strip() or None
    work_number_id = data.get('work_number_id')
    try:
        work_number_id = int(work_number_id) if work_number_id not in (None, '') else None
    except (TypeError, ValueError):
        work_number_id = None
    vehicle_count = data.get('vehicle_count')
    try:
        vehicle_count = int(vehicle_count) if vehicle_count not in (None, '') else 1
    except (TypeError, ValueError):
        vehicle_count = 1
    volume = _m_to_float(data.get('volume', 0))
    vehicle_no = (data.get('vehicle_no', '') or '').strip() or None
    driver_name = (data.get('driver_name', '') or '').strip() or None
    slump = (data.get('slump', '') or '').strip() or None
    remark = (data.get('remark', '') or '').strip() or None

    # 定位字段：兼容扁平结构（location_lat）和离线对象（location.lat）
    location_lat = data.get('location_lat')
    location_lng = data.get('location_lng')
    location_accuracy = data.get('location_accuracy')
    loc_obj = data.get('location')
    if isinstance(loc_obj, dict):
        if location_lat in (None, ''):
            location_lat = loc_obj.get('lat')
        if location_lng in (None, ''):
            location_lng = loc_obj.get('lng')
        if location_accuracy in (None, ''):
            location_accuracy = loc_obj.get('accuracy')
    try:
        location_lat = float(location_lat) if location_lat not in (None, '') else None
    except (TypeError, ValueError):
        location_lat = None
    try:
        location_lng = float(location_lng) if location_lng not in (None, '') else None
    except (TypeError, ValueError):
        location_lng = None
    try:
        location_accuracy = float(location_accuracy) if location_accuracy not in (None, '') else None
    except (TypeError, ValueError):
        location_accuracy = None

    arrival_time_str = (data.get('arrival_time', '') or '').strip()
    arrival_time = None
    if arrival_time_str:
        try:
            arrival_time = datetime.strptime(arrival_time_str, '%Y-%m-%dT%H:%M')
        except Exception:
            try:
                arrival_time = datetime.strptime(arrival_time_str, '%Y-%m-%d %H:%M')
            except Exception:
                arrival_time = datetime.now()
    else:
        arrival_time = datetime.now()

    ticket = ConcreteTicket(
        project_id=project_id, ticket_no=ticket_no,
        supplier_id=supplier_id, supplier_name=supplier_name,
        contract_id=contract_id,
        strength_grade=strength_grade, material_id=material_id,
        pour_part=pour_part,
        work_number_id=work_number_id, vehicle_count=vehicle_count,
        volume=volume, arrival_time=arrival_time,
        vehicle_no=vehicle_no, driver_name=driver_name,
        slump=slump, remark=remark,
        location_lat=location_lat, location_lng=location_lng,
        location_accuracy=location_accuracy,
        location_time=datetime.now() if location_lat else None,
    )
    db.session.add(ticket)
    db.session.flush()

    uploaded = 0
    # 在线照片文件
    if files:
        for photo in files:
            if photo and getattr(photo, 'filename', ''):
                att, err = upload_attachment(photo, 'concrete_ticket',
                                             biz_id=ticket.id, project_id=project_id)
                if not err:
                    uploaded += 1

    # 离线 base64 照片（data['photos'] 为 base64 字符串列表）
    photos_b64 = data.get('photos') if hasattr(data, 'get') else None
    if isinstance(photos_b64, list):
        for idx, b64 in enumerate(photos_b64):
            try:
                if not isinstance(b64, str) or ',' not in b64:
                    continue
                header, raw_b64 = b64.split(',', 1)
                mime = 'image/jpeg'
                if 'data:' in header and ';base64' in header:
                    mime_part = header.split(':', 1)[1].split(';', 1)[0]
                    if mime_part:
                        mime = mime_part
                raw = base64.b64decode(raw_b64)
                fname = f'offline_{ticket.id}_{idx}.jpg'
                fs = FileStorage(stream=BytesIO(raw), filename=fname,
                                 content_type=mime)
                att, err = upload_attachment(fs, 'concrete_ticket',
                                             biz_id=ticket.id, project_id=project_id)
                if not err:
                    uploaded += 1
            except Exception:
                continue

    return ticket, ticket_no, uploaded


@bp.route('/concrete/create', methods=['GET', 'POST'])
@login_required
def concrete_create():
    """商砼小票登记（支持连续登记模式）"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        try:
            photos_files = request.files.getlist('photos[]')
            _validate_upload_files(photos_files)
            ticket, ticket_no, uploaded = _save_concrete_ticket(
                project.id, request.form, files=photos_files)
            db.session.commit()
            log_operation('新增', module='商砼小票',
                          description=f'移动端登记商砼小票 {ticket.ticket_no}')

            # 连续登记模式：返回JSON，前端清空表单保留供应单位和标号
            if (request.form.get('continue_mode') == '1'
                    or request.headers.get('X-Continue-Mode') == '1'):
                return jsonify({'success': True, 'ticket_no': ticket_no,
                                'id': ticket.id, 'uploaded': uploaded})

            flash(f'商砼小票 {ticket_no} 登记成功' +
                  (f'，上传 {uploaded} 张照片' if uploaded else ''), 'success')
            return redirect(url_for('mobile.concrete_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{e}', 'danger')
            return redirect(url_for('mobile.concrete_create'))

    # GET
    suppliers = Supplier.query.filter_by(project_id=project.id).order_by(Supplier.name).all()
    work_numbers = WorkNumber.query.filter_by(project_id=project.id).order_by(WorkNumber.code).all()
    default_ticket_no = _m_gen_concrete_ticket_no(project.id)
    ai_vision_enabled = _m_ai_vision_enabled()
    return render_template('mobile/concrete_create.html', project=project,
                           suppliers=suppliers, work_numbers=work_numbers,
                           strength_grades=_CONCRETE_STRENGTH_GRADES,
                           default_ticket_no=default_ticket_no,
                           now=datetime.now().strftime('%Y-%m-%dT%H:%M'),
                           ai_vision_enabled=ai_vision_enabled)


@bp.route('/api/concrete/sync', methods=['POST'])
@mobile_auth_required
def api_concrete_sync():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """离线商砼小票同步接口

    请求体: { ticket: { client_id, ticket_no, supplier_id, supplier_name,
                       strength_grade, pour_part, work_number_id, vehicle_no,
                       vehicle_count, driver_name, volume, slump, arrival_time,
                       remark, photos: [base64...], location: {lat,lng,accuracy} } }
    返回: { success, ticket_id, ticket_no, uploaded, message }
    """
    project = _m_require_project()
    if not project:
        return jsonify({'success': False, 'message': '未选择项目'}), 400

    payload = request.get_json(silent=True) or {}
    ticket_data = payload.get('ticket') or payload
    if not ticket_data or not isinstance(ticket_data, dict):
        return jsonify({'success': False, 'message': '缺少小票数据'}), 400

    # 基本校验（与前端保持一致）
    if not (ticket_data.get('strength_grade') or '').strip():
        return jsonify({'success': False, 'message': '请选择砼标号'}), 400
    try:
        vol = float(ticket_data.get('volume', 0))
    except (TypeError, ValueError):
        vol = 0
    if vol <= 0:
        return jsonify({'success': False, 'message': '方量必须大于0'}), 400

    try:
        ticket, ticket_no, uploaded = _save_concrete_ticket(
            project.id, ticket_data, files=None)
        db.session.commit()
        log_operation('同步', module='商砼小票',
                      description=f'移动端离线同步商砼小票 {ticket.ticket_no}')
        return jsonify({
            'success': True,
            'ticket_id': ticket.id,
            'ticket_no': ticket_no,
            'uploaded': uploaded,
            'message': '同步成功'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'同步失败：{e}'}), 500


@bp.route('/concrete/<int:tid>')
@login_required
def concrete_detail(tid):
    """商砼小票详情"""
    ticket = ConcreteTicket.query.get_or_404(tid)
    attachments = Attachment.query.filter_by(
        module='concrete_ticket', biz_id=ticket.id, is_deleted=False
    ).order_by(Attachment.uploaded_at.asc()).all()
    # 当天录入可编辑
    can_edit = bool(ticket.arrival_time) and ticket.arrival_time.date() == date.today() \
        and not ticket.is_reconciled and not ticket.is_transferred
    return render_template('mobile/concrete_detail.html', ticket=ticket,
                           attachments=attachments, can_edit=can_edit)


@bp.route('/concrete/<int:tid>/edit', methods=['GET', 'POST'])
@login_required
def concrete_edit(tid):
    """商砼小票编辑（仅当天录入可编辑，隔天只读跳转详情）"""
    ticket = ConcreteTicket.query.get_or_404(tid)
    # 隔天或已对账/已转入库，禁止编辑
    can_edit = bool(ticket.arrival_time) and ticket.arrival_time.date() == date.today() \
        and not ticket.is_reconciled and not ticket.is_transferred
    if not can_edit:
        flash('该小票非当天录入或已对账/已转入库，不可编辑', 'warning')
        return redirect(url_for('mobile.concrete_detail', tid=ticket.id))

    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        try:
            ticket_no = (request.form.get('ticket_no', '') or '').strip()
            if not ticket_no:
                ticket_no = ticket.ticket_no
            ticket.ticket_no = ticket_no
            ticket.supplier_id = request.form.get('supplier_id', type=int) or None
            ticket.supplier_name = (request.form.get('supplier_name', '') or '').strip() or None
            ticket.strength_grade = (request.form.get('strength_grade', '') or '').strip() or None
            ticket.pour_part = (request.form.get('pour_part', '') or '').strip() or None
            ticket.work_number_id = request.form.get('work_number_id', type=int) or None
            ticket.vehicle_count = request.form.get('vehicle_count', type=int) or 1
            ticket.volume = _m_to_float(request.form.get('volume', 0))
            ticket.vehicle_no = (request.form.get('vehicle_no', '') or '').strip() or None
            ticket.driver_name = (request.form.get('driver_name', '') or '').strip() or None
            ticket.slump = (request.form.get('slump', '') or '').strip() or None
            ticket.remark = (request.form.get('remark', '') or '').strip() or None

            arrival_time_str = (request.form.get('arrival_time', '') or '').strip()
            if arrival_time_str:
                try:
                    ticket.arrival_time = datetime.strptime(arrival_time_str, '%Y-%m-%dT%H:%M')
                except Exception:
                    try:
                        ticket.arrival_time = datetime.strptime(arrival_time_str, '%Y-%m-%d %H:%M')
                    except Exception:
                        pass

            db.session.flush()

            # 追加照片
            photos = request.files.getlist('photos[]')
            _validate_upload_files(photos)
            uploaded = 0
            for photo in photos:
                if photo and photo.filename:
                    att, err = upload_attachment(photo, 'concrete_ticket',
                                                 biz_id=ticket.id, project_id=project.id)
                    if not err:
                        uploaded += 1

            db.session.commit()
            log_operation('编辑', module='商砼小票',
                          description=f'移动端编辑商砼小票 {ticket.ticket_no}')
            flash('商砼小票更新成功' +
                  (f'，新增 {uploaded} 张照片' if uploaded else ''), 'success')
            return redirect(url_for('mobile.concrete_detail', tid=ticket.id))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{e}', 'danger')
            return redirect(url_for('mobile.concrete_edit', tid=ticket.id))

    # GET 渲染编辑表单（复用 create 模板）
    suppliers = Supplier.query.filter_by(project_id=project.id).order_by(Supplier.name).all()
    work_numbers = WorkNumber.query.filter_by(project_id=project.id).order_by(WorkNumber.code).all()
    arrival_time_value = ticket.arrival_time.strftime('%Y-%m-%dT%H:%M') if ticket.arrival_time else ''
    existing_photos = Attachment.query.filter_by(
        module='concrete_ticket', biz_id=ticket.id, is_deleted=False
    ).order_by(Attachment.uploaded_at.asc()).all()
    return render_template('mobile/concrete_edit.html', project=project, ticket=ticket,
                           suppliers=suppliers, work_numbers=work_numbers,
                           strength_grades=_CONCRETE_STRENGTH_GRADES,
                           arrival_time_value=arrival_time_value,
                           existing_photos=existing_photos)


@bp.route('/api/concrete/suppliers')
@mobile_auth_required
def api_concrete_suppliers():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """当前项目供应商列表"""
    project = _m_require_project()
    if not project:
        return jsonify([])
    items = Supplier.query.filter_by(project_id=project.id).order_by(Supplier.name).all()
    return jsonify([{
        'id': s.id, 'name': s.name,
        'contact': s.contact_person or '',
        'phone': s.phone or ''
    } for s in items])


@bp.route('/api/concrete/history_pour_part')
@mobile_auth_required
def api_concrete_history_pour_part():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """历史浇筑部位联想（GET ?keyword=）"""
    project = _m_require_project()
    if not project:
        return jsonify([])
    kw = (request.args.get('keyword', '') or '').strip()
    q = ConcreteTicket.query.filter_by(project_id=project.id).filter(
        ConcreteTicket.pour_part.isnot(None)
    )
    if kw:
        q = q.filter(ConcreteTicket.pour_part.contains(kw))
    items = q.with_entities(ConcreteTicket.pour_part) \
        .distinct().order_by(ConcreteTicket.arrival_time.desc()).limit(20).all()
    seen = []
    for (pp,) in items:
        if pp and pp not in seen:
            seen.append(pp)
    return jsonify([{'value': pp, 'label': pp} for pp in seen[:15]])


# ============================================================
# 供应商管理（移动端） - 与PC端共用同一主库，支持AI营业执照识别
# ============================================================

@bp.route('/suppliers')
@login_required
def supplier_list():
    """供应商列表（公司级主库）"""
    keyword = (request.args.get('keyword', '') or '').strip()
    status_filter = (request.args.get('status_filter', '') or '').strip()

    query = Supplier.query.filter_by(source='company')
    if keyword:
        query = query.filter(
            or_(Supplier.name.contains(keyword), Supplier.code.contains(keyword))
        )
    if status_filter in ('qualified', 'unqualified', 'blacklist'):
        query = query.filter_by(status=status_filter)

    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(Supplier.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    suppliers = pagination.items
    return render_template('mobile/supplier_list.html',
                           suppliers=suppliers,
                           keyword=keyword,
                           status_filter=status_filter,
                           total=pagination.total)


def _m_gen_supplier_code():
    """生成公司级供应商编码：GYS + 6位流水号"""
    from sqlalchemy import func
    from app.utils import _code_gen_lock
    with _code_gen_lock:
        max_code = db.session.query(func.max(Supplier.code)).filter(
            Supplier.code.like('GYS%'),
            Supplier.source == 'company'
        ).scalar()
        if max_code:
            try:
                seq = int(max_code[3:]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
        candidate = f'GYS{seq:06d}'
        # 冲突重试（最多10次）
        for _ in range(10):
            exists = Supplier.query.filter_by(code=candidate, source='company').first()
            if not exists:
                return candidate
            seq += 1
            candidate = f'GYS{seq:06d}'
        return candidate


@bp.route('/suppliers/create', methods=['GET', 'POST'])
@login_required
@log_audit(module='mobile_supplier', operation='新增')
def supplier_create():
    """新增供应商（移动端）"""
    if not current_user.is_admin():
        flash('无权限，仅管理员可新增供应商', 'danger')
        return redirect(url_for('mobile.supplier_list'))

    if request.method == 'POST':
        name = (request.form.get('name', '') or '').strip()
        if not name:
            flash('供应商名称不能为空', 'danger')
            return redirect(url_for('mobile.supplier_create'))

        # 公司级主库使用第一个项目的 ID 满足 NOT NULL 约束
        first_project = Project.query.first()
        if not first_project:
            flash('系统未找到任何项目，无法创建供应商', 'danger')
            return redirect(url_for('mobile.supplier_create'))

        code = _m_gen_supplier_code()
        supplier = Supplier(
            project_id=first_project.id,
            name=name,
            code=code,
            credit_code=(request.form.get('credit_code', '') or '').strip(),
            contact_person=(request.form.get('contact_person', '') or '').strip(),
            phone=(request.form.get('phone', '') or '').strip(),
            legal_person=(request.form.get('legal_person', '') or '').strip(),
            address=(request.form.get('address', '') or '').strip(),
            bank_name=(request.form.get('bank_name', '') or '').strip(),
            bank_account=(request.form.get('bank_account', '') or '').strip(),
            source='company',
            status='qualified',
            create_dept=current_user.dept_id,
        )
        db.session.add(supplier)
        db.session.commit()
        flash(f'供应商创建成功，编码：{code}', 'success')
        return redirect(url_for('mobile.supplier_list'))

    ai_vision_enabled = _m_ai_vision_enabled()
    return render_template('mobile/supplier_form.html',
                           supplier=None,
                           ai_vision_enabled=ai_vision_enabled)


@bp.route('/suppliers/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@log_audit(module='mobile_supplier', operation='编辑')
def supplier_edit(id):
    """编辑供应商（移动端）"""
    if not current_user.is_admin():
        flash('无权限，仅管理员可编辑供应商', 'danger')
        return redirect(url_for('mobile.supplier_list'))

    supplier = Supplier.query.get_or_404(id)
    if supplier.source != 'company':
        flash('该供应商不在公司主库，无法通过此入口编辑', 'danger')
        return redirect(url_for('mobile.supplier_list'))

    if request.method == 'POST':
        name = (request.form.get('name', '') or '').strip()
        if not name:
            flash('供应商名称不能为空', 'danger')
            return redirect(url_for('mobile.supplier_edit', id=id))

        supplier.name = name
        supplier.credit_code = (request.form.get('credit_code', '') or '').strip()
        supplier.contact_person = (request.form.get('contact_person', '') or '').strip()
        supplier.phone = (request.form.get('phone', '') or '').strip()
        supplier.legal_person = (request.form.get('legal_person', '') or '').strip()
        supplier.address = (request.form.get('address', '') or '').strip()
        supplier.bank_name = (request.form.get('bank_name', '') or '').strip()
        supplier.bank_account = (request.form.get('bank_account', '') or '').strip()
        new_status = (request.form.get('status', '') or '').strip()
        if new_status in ('qualified', 'unqualified', 'blacklist'):
            supplier.status = new_status
        db.session.commit()
        flash('供应商更新成功', 'success')
        return redirect(url_for('mobile.supplier_list'))

    ai_vision_enabled = _m_ai_vision_enabled()
    return render_template('mobile/supplier_form.html',
                           supplier=supplier,
                           ai_vision_enabled=ai_vision_enabled)


# ============== 发票台账 ==============

def _m_parse_date(date_str):
    """移动端日期解析"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        return None


def _m_invoice_save_file(file_storage):
    """保存发票文件（图片或PDF）"""
    from werkzeug.utils import secure_filename
    from flask import current_app
    import os as _os

    if not file_storage or not file_storage.filename:
        return None
    allowed = {'pdf', 'png', 'jpg', 'jpeg', 'gif'}
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    if ext not in allowed:
        return None
    filename = secure_filename(file_storage.filename) or f"invoice_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
    upload_dir = _os.path.join(current_app.config['UPLOAD_FOLDER'], 'invoices')
    _os.makedirs(upload_dir, exist_ok=True)
    file_storage.save(_os.path.join(upload_dir, filename))
    return _os.path.join('uploads', 'invoices', filename)


@bp.route('/invoices')
@login_required
def invoice_list():
    """发票台账列表（移动端）"""
    project = _get_current_project()
    if not project:
        flash('请先选择项目', 'warning')
        return redirect(url_for('mobile.profile'))

    keyword = (request.args.get('keyword', '') or '').strip()
    query = Invoice.query.filter_by(project_id=project.id)
    if keyword:
        query = query.filter(
            or_(Invoice.invoice_number.contains(keyword),
                Invoice.invoice_code.contains(keyword),
                Invoice.remark.contains(keyword))
        )

    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(Invoice.invoice_date.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    invoices = pagination.items
    return render_template('mobile/invoice_list.html',
                           invoices=invoices,
                           keyword=keyword,
                           total=pagination.total)


@bp.route('/invoices/create', methods=['GET', 'POST'])
@login_required
@log_audit(module='mobile_invoice', operation='新增')
def invoice_create():
    """新增发票（移动端，支持AI识别）"""
    if not current_user.is_admin():
        flash('无权限，仅管理员可新增发票', 'danger')
        return redirect(url_for('mobile.invoice_list'))

    project = _get_current_project()
    if not project:
        flash('请先选择项目', 'warning')
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        contract_id = request.form.get('contract_id', type=int)
        if not contract_id:
            flash('请选择关联合同', 'danger')
            return redirect(url_for('mobile.invoice_create'))
        contract = Contract.query.get_or_404(contract_id)

        amount_with_tax = to_decimal(request.form.get('amount_with_tax'))
        tax_rate = to_decimal(request.form.get('tax_rate'), 13)
        from app.utils import calc_without_tax
        amount_without_tax = calc_without_tax(amount_with_tax, tax_rate)
        file_path = _m_invoice_save_file(request.files.get('file'))

        invoice = Invoice(
            project_id=project.id,
            contract_id=contract.id,
            supplier_id=contract.supplier_id,
            invoice_code=(request.form.get('invoice_code', '') or '').strip(),
            invoice_number=(request.form.get('invoice_number', '') or '').strip(),
            invoice_date=_m_parse_date(request.form.get('invoice_date')),
            amount_with_tax=amount_with_tax,
            tax_rate=tax_rate,
            amount_without_tax=amount_without_tax,
            file_path=file_path,
            remark=(request.form.get('remark', '') or '').strip()
        )
        db.session.add(invoice)
        db.session.commit()
        flash('发票创建成功', 'success')
        return redirect(url_for('mobile.invoice_list'))

    contracts = Contract.query.filter_by(project_id=project.id, is_deleted=False) \
        .order_by(Contract.code).all()
    pre_contract_id = request.args.get('contract_id', type=int)
    ai_vision_enabled = _m_ai_vision_enabled()
    return render_template('mobile/invoice_form.html',
                           invoice=None,
                           contracts=contracts,
                           pre_contract_id=pre_contract_id,
                           ai_vision_enabled=ai_vision_enabled)


@bp.route('/invoices/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@log_audit(module='mobile_invoice', operation='编辑')
def invoice_edit(id):
    """编辑发票（移动端）"""
    if not current_user.is_admin():
        flash('无权限，仅管理员可编辑发票', 'danger')
        return redirect(url_for('mobile.invoice_list'))

    invoice = Invoice.query.get_or_404(id)

    if request.method == 'POST':
        contract_id = request.form.get('contract_id', type=int)
        if not contract_id:
            flash('请选择关联合同', 'danger')
            return redirect(url_for('mobile.invoice_edit', id=id))
        contract = Contract.query.get_or_404(contract_id)

        file_path = _m_invoice_save_file(request.files.get('file'))
        if file_path:
            invoice.file_path = file_path

        invoice.contract_id = contract.id
        invoice.supplier_id = contract.supplier_id
        invoice.invoice_code = (request.form.get('invoice_code', '') or '').strip()
        invoice.invoice_number = (request.form.get('invoice_number', '') or '').strip()
        invoice.invoice_date = _m_parse_date(request.form.get('invoice_date'))
        invoice.amount_with_tax = to_decimal(request.form.get('amount_with_tax'))
        invoice.tax_rate = to_decimal(request.form.get('tax_rate'), 13)
        from app.utils import calc_without_tax
        invoice.amount_without_tax = calc_without_tax(invoice.amount_with_tax, invoice.tax_rate)
        invoice.remark = (request.form.get('remark', '') or '').strip()
        db.session.commit()
        flash('发票更新成功', 'success')
        return redirect(url_for('mobile.invoice_list'))

    contracts = Contract.query.filter_by(project_id=invoice.project_id, is_deleted=False) \
        .order_by(Contract.code).all()
    ai_vision_enabled = _m_ai_vision_enabled()
    return render_template('mobile/invoice_form.html',
                           invoice=invoice,
                           contracts=contracts,
                           pre_contract_id=None,
                           ai_vision_enabled=ai_vision_enabled)


# ============== 报废申请 ==============

@bp.route('/scrap')
@login_required
def scrap_list():
    """报废申请列表"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    status = request.args.get('status', '')
    q = MaterialScrap.query.filter_by(project_id=project.id)
    if status:
        q = q.filter(MaterialScrap.approval_status == status)
    scraps = q.order_by(MaterialScrap.created_at.desc()).limit(100).all()

    # 统计
    stats = {
        'draft': MaterialScrap.query.filter_by(
            project_id=project.id, approval_status='draft').count(),
        'pending': MaterialScrap.query.filter_by(
            project_id=project.id, approval_status='pending').count(),
        'passed': MaterialScrap.query.filter_by(
            project_id=project.id, approval_status='passed').count(),
        'rejected': MaterialScrap.query.filter_by(
            project_id=project.id, approval_status='rejected').count(),
    }
    REASON_LABELS = {'expired': '过期', 'damaged': '损坏',
                      'unqualified': '不合格', 'other': '其他'}
    return render_template('mobile/scrap_list.html', scraps=scraps, stats=stats,
                           reason_labels=REASON_LABELS, current_status=status)


@bp.route('/scrap/create', methods=['GET', 'POST'])
@login_required
def scrap_create():
    """新建报废申请"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        scrap_date_str = request.form.get('scrap_date')
        try:
            scrap_date = datetime.strptime(scrap_date_str, '%Y-%m-%d').date()
        except Exception:
            scrap_date = date.today()

        reason = request.form.get('reason', 'other')
        remark = request.form.get('remark', '').strip()
        usage_unit_id = request.form.get('usage_unit_id', type=int) or None
        location_lat = request.form.get('location_lat', type=float) or None
        location_lng = request.form.get('location_lng', type=float) or None
        location_accuracy = request.form.get('location_accuracy', type=float) or None

        # 生成单号 BF-{pid}-{YYYYMMDD}-{seq}
        today_str = datetime.now().strftime('%Y%m%d')
        prefix = f'BF-{project.id}-{today_str}-'
        existing = MaterialScrap.query.filter(
            MaterialScrap.code.like(f'{prefix}%')).count()
        code = f'{prefix}{existing + 1:03d}'

        scrap = MaterialScrap(
            project_id=project.id, code=code,
            scrap_date=scrap_date, reason=reason, remark=remark,
            usage_unit_id=usage_unit_id,
            approval_status='draft',
            applicant_id=current_user.id,
            applicant_name=current_user.name or current_user.username,
            location_lat=location_lat, location_lng=location_lng,
            location_accuracy=location_accuracy,
            location_time=datetime.now() if location_lat else None,
        )
        db.session.add(scrap)
        db.session.flush()

        # 明细
        material_ids = request.form.getlist('material_id[]')
        quantities = request.form.getlist('quantity[]')
        reason_details = request.form.getlist('reason_detail[]')
        total_qty = 0
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = _m_to_float(quantities[idx] if idx < len(quantities) else 0)
            if qty == 0:
                continue
            reason_detail = (reason_details[idx]
                             if idx < len(reason_details) else '').strip()
            item = MaterialScrapItem(
                scrap_id=scrap.id, material_id=int(mid),
                quantity=qty, unit_price=0, amount=0,
                reason_detail=reason_detail or None,
            )
            db.session.add(item)
            total_qty += qty
        scrap.total_quantity = total_qty
        scrap.total_amount = 0

        # 照片上传
        photos = request.files.getlist('photos[]')
        _validate_upload_files(photos)
        for photo in photos:
            if photo and photo.filename:
                att, err = upload_attachment(photo, 'scrap',
                                            biz_id=scrap.id, project_id=project.id)
                if err:
                    flash(f'照片 {photo.filename} 上传失败：{err}', 'warning')

        db.session.commit()

        # 自动提交审批（按钮"提交并审批"）
        if request.form.get('submit_type') == 'submit':
            return _scrap_submit_to_approval(scrap)

        flash('报废申请创建成功', 'success')
        return redirect(url_for('mobile.scrap_detail', id=scrap.id))

    # GET
    materials = Material.query.filter_by(project_id=project.id).order_by(
        Material.name).limit(200).all()
    usage_units = UsageUnit.query.filter_by(project_id=project.id).order_by(
        UsageUnit.name).all()
    REASON_LABELS = {'expired': '过期', 'damaged': '损坏',
                     'unqualified': '不合格', 'other': '其他'}
    today_str = date.today().isoformat()
    today_code_prefix = f'BF-{project.id}-{datetime.now().strftime("%Y%m%d")}-'
    existing_today = MaterialScrap.query.filter(
        MaterialScrap.code.like(f'{today_code_prefix}%')).count()
    default_code = f'{today_code_prefix}{existing_today + 1:03d}'
    return render_template('mobile/scrap_create.html', project=project,
                           materials=materials, usage_units=usage_units,
                           reason_labels=REASON_LABELS,
                           today=today_str, default_code=default_code)


def _scrap_submit_to_approval(scrap):
    """提交报废到审批流"""
    from app.approval.service import submit_approval, is_approval_enabled
    if not is_approval_enabled('scrap'):
        # 未启用审批流：直接通过并扣减库存
        from app.approval.service import _apply_scrap_inventory
        _apply_scrap_inventory(scrap)
        scrap.approval_status = 'passed'
        db.session.commit()
        flash('报废单已直接通过（未启用审批流），库存已扣减', 'success')
        return redirect(url_for('mobile.scrap_detail', id=scrap.id))
    success, msg, instance = submit_approval(
        'scrap', scrap.id, applicant_id=current_user.id, project_id=scrap.project_id)
    if success:
        scrap.approval_status = 'pending'
        scrap.approval_instance_id = instance.id if instance else None
        db.session.commit()
        flash('报废申请已提交审批', 'success')
    else:
        flash(f'提交审批失败：{msg}', 'danger')
    return redirect(url_for('mobile.scrap_detail', id=scrap.id))


@bp.route('/scrap/<int:id>/submit', methods=['POST'])
@login_required
def scrap_submit(id):
    """提交报废申请到审批"""
    scrap = MaterialScrap.query.get_or_404(id)
    if scrap.approval_status not in ('draft', 'rejected'):
        flash('当前状态不允许提交', 'warning')
        return redirect(url_for('mobile.scrap_detail', id=id))
    return _scrap_submit_to_approval(scrap)


@bp.route('/scrap/<int:id>')
@login_required
def scrap_detail(id):
    """报废申请详情"""
    scrap = MaterialScrap.query.get_or_404(id)
    # 获取附件
    attachments = Attachment.query.filter_by(
        module='scrap', biz_id=id, is_deleted=False).all()
    # 获取审批实例
    instance = None
    if scrap.approval_instance_id:
        try:
            from app.approval.service import get_instance_by_biz
            instance = get_instance_by_biz('scrap', id)
        except Exception:
            instance = None
    REASON_LABELS = {'expired': '过期', 'damaged': '损坏',
                     'unqualified': '不合格', 'other': '其他'}
    return render_template('mobile/scrap_detail.html', scrap=scrap,
                          attachments=attachments, instance=instance,
                          reason_labels=REASON_LABELS)


@bp.route('/api/scrap/inventory/<int:mid>')
@mobile_auth_required
def api_scrap_inventory(mid):
    """查询物资当前库存"""
    project = _m_require_project()
    if not project:
        return jsonify({'quantity': 0})
    inv = Inventory.query.filter_by(
        project_id=project.id, material_id=mid).first()
    return jsonify({'quantity': float(inv.quantity) if inv else 0})


# ============================================================
# 第五部分：领料申请（移动端）
# ============================================================

def _m_gen_pr_no(project_id):
    """生成领料申请单号 SQ-{pid}-{YYYYMMDD}-{seq}"""
    today_str = datetime.now().strftime('%Y%m%d')
    prefix = f'SQ-{project_id}-{today_str}-'
    existing = PurchaseRequisition.query.filter(
        PurchaseRequisition.pr_no.like(f'{prefix}%')).count()
    return f'{prefix}{existing + 1:03d}'


@bp.route('/pr')
@login_required
def pr_list():
    """领料申请列表"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    status = request.args.get('status', '')
    tab = request.args.get('tab', 'my')  # my=我的申请 / approval=待我审批

    if tab == 'approval':
        # 待我审批的领料申请
        from app.approval.service import get_my_pending_approvals
        instances = get_my_pending_approvals(current_user.id)
        instances = [i for i in instances if i.biz_type == 'purchase_requisition']
        prs = [PurchaseRequisition.query.get(i.biz_id) for i in instances]
        prs = [p for p in prs if p]
    else:
        # 我的申请
        q = PurchaseRequisition.query.filter_by(project_id=project.id)
        if status:
            q = q.filter(PurchaseRequisition.status == status)
        prs = q.order_by(PurchaseRequisition.created_at.desc()).limit(100).all()

    return render_template('mobile/pr_list.html', prs=prs, status=status, tab=tab)


@bp.route('/pr/create', methods=['GET', 'POST'])
@login_required
def pr_create():
    """新建领料申请"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        apply_dept = request.form.get('apply_dept', '').strip()
        demand_date_str = request.form.get('demand_date', '')
        try:
            demand_date = datetime.strptime(demand_date_str, '%Y-%m-%d').date()
        except Exception:
            demand_date = None
        work_number_id = request.form.get('work_number_id', type=int) or None
        construction_part = request.form.get('construction_part', '').strip()
        remark = request.form.get('remark', '').strip()

        pr = PurchaseRequisition(
            pr_no=_m_gen_pr_no(project.id),
            project_id=project.id,
            apply_dept=apply_dept,
            apply_user=current_user.name or current_user.username,
            apply_date=date.today(),
            demand_date=demand_date,
            status='draft',
            remark=remark,
        )
        db.session.add(pr)
        db.session.flush()

        # 明细
        material_ids = request.form.getlist('material_id[]')
        apply_qtys = request.form.getlist('apply_qty[]')
        purposes = request.form.getlist('purpose[]')
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = _m_to_float(apply_qtys[idx] if idx < len(apply_qtys) else 0)
            if qty == 0:
                continue
            mat = Material.query.get(int(mid))
            purpose = (purposes[idx] if idx < len(purposes) else '').strip()
            item = PurchaseRequisitionItem(
                pr_id=pr.id, material_id=int(mid),
                material_name=mat.name if mat else '',
                specification=mat.specification if mat else '',
                unit=mat.unit if mat else '',
                apply_qty=qty, purpose=purpose,
                converted=False,
            )
            db.session.add(item)

        db.session.commit()

        # 自动提交审批
        if request.form.get('submit_type') == 'submit':
            return _pr_submit_to_approval(pr)

        flash('领料申请已保存为草稿', 'success')
        return redirect(url_for('mobile.pr_detail', id=pr.id))

    work_numbers = WorkNumber.query.filter_by(project_id=project.id).order_by(WorkNumber.code).all()
    usage_units = UsageUnit.query.filter_by(project_id=project.id).order_by(UsageUnit.name).all()
    return render_template('mobile/pr_form.html', project=project,
                           work_numbers=work_numbers, usage_units=usage_units,
                           pr=None, today=date.today().isoformat(),
                           default_pr_no=_m_gen_pr_no(project.id))


def _pr_submit_to_approval(pr):
    """提交领料申请到审批流"""
    from app.approval.service import submit_approval
    success, msg, instance = submit_approval('purchase_requisition', pr.id,
                                             applicant_id=current_user.id)
    if success:
        pr.status = 'pending'
        db.session.commit()
        flash('领料申请已提交审批', 'success')
    else:
        flash(f'提交审批失败：{msg}', 'danger')
    return redirect(url_for('mobile.pr_detail', id=pr.id))


@bp.route('/pr/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def pr_edit(id):
    """编辑领料申请（仅草稿/驳回状态可编辑）"""
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status not in ('draft', 'rejected'):
        flash('当前状态不允许编辑', 'warning')
        return redirect(url_for('mobile.pr_detail', id=id))

    if request.method == 'POST':
        pr.apply_dept = request.form.get('apply_dept', '').strip()
        demand_date_str = request.form.get('demand_date', '')
        try:
            pr.demand_date = datetime.strptime(demand_date_str, '%Y-%m-%d').date()
        except Exception:
            pr.demand_date = None
        pr.remark = request.form.get('remark', '').strip()

        # 清空旧明细，重新写入
        for old_item in list(pr.items):
            db.session.delete(old_item)
        db.session.flush()

        material_ids = request.form.getlist('material_id[]')
        apply_qtys = request.form.getlist('apply_qty[]')
        purposes = request.form.getlist('purpose[]')
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = _m_to_float(apply_qtys[idx] if idx < len(apply_qtys) else 0)
            if qty == 0:
                continue
            mat = Material.query.get(int(mid))
            purpose = (purposes[idx] if idx < len(purposes) else '').strip()
            item = PurchaseRequisitionItem(
                pr_id=pr.id, material_id=int(mid),
                material_name=mat.name if mat else '',
                specification=mat.specification if mat else '',
                unit=mat.unit if mat else '',
                apply_qty=qty, purpose=purpose,
                converted=False,
            )
            db.session.add(item)

        db.session.commit()

        if request.form.get('submit_type') == 'submit':
            return _pr_submit_to_approval(pr)

        flash('领料申请已更新', 'success')
        return redirect(url_for('mobile.pr_detail', id=pr.id))

    project = pr.project
    work_numbers = WorkNumber.query.filter_by(project_id=project.id).order_by(WorkNumber.code).all()
    usage_units = UsageUnit.query.filter_by(project_id=project.id).order_by(UsageUnit.name).all()
    return render_template('mobile/pr_form.html', project=project,
                           work_numbers=work_numbers, usage_units=usage_units,
                           pr=pr, today=date.today().isoformat(),
                           default_pr_no=pr.pr_no)


@bp.route('/pr/<int:id>/submit', methods=['POST'])
@login_required
def pr_submit(id):
    """提交领料申请到审批"""
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status not in ('draft', 'rejected'):
        flash('当前状态不允许提交', 'warning')
        return redirect(url_for('mobile.pr_detail', id=id))
    return _pr_submit_to_approval(pr)


@bp.route('/pr/<int:id>/withdraw', methods=['POST'])
@login_required
def pr_withdraw(id):
    """撤回领料申请"""
    from app.approval.service import withdraw
    pr = PurchaseRequisition.query.get_or_404(id)
    if pr.status != 'pending':
        flash('只有待审批状态可撤回', 'warning')
        return redirect(url_for('mobile.pr_detail', id=id))
    if pr.approval_instance_id:
        success, msg = withdraw(pr.approval_instance_id,
                                applicant_id=current_user.id)
        if not success:
            flash(f'撤回失败：{msg}', 'danger')
            return redirect(url_for('mobile.pr_detail', id=id))
    pr.status = 'draft'
    db.session.commit()
    flash('已撤回', 'success')
    return redirect(url_for('mobile.pr_detail', id=id))


@bp.route('/pr/<int:id>')
@login_required
def pr_detail(id):
    """领料申请详情"""
    pr = PurchaseRequisition.query.get_or_404(id)
    instance = None
    if pr.approval_instance_id:
        from app.approval.service import get_instance_by_biz
        instance = get_instance_by_biz('purchase_requisition', id)
    return render_template('mobile/pr_detail.html', pr=pr, instance=instance)


@bp.route('/pr/<int:id>/convert_stock_out', methods=['POST'])
@login_required
def pr_convert_stock_out(id):
    """移动端生成出库单（调用 PC 端逻辑）"""
    # 复用 PC 端 convert_stock_out 逻辑
    from app.purchase_requisition.routes import convert_stock_out
    return convert_stock_out(id)


@bp.route('/api/pr/materials')
@mobile_auth_required
def api_pr_materials():
    # P1-6: 验证项目访问权限
    _pid, _err = _check_project_access()
    if _err:
        return _err
    """物资搜索"""
    project = _m_require_project()
    if not project:
        return jsonify([])
    keyword = request.args.get('keyword', '').strip()
    q = Material.query.filter_by(project_id=project.id)
    if hasattr(Material, 'is_deleted'):
        q = q.filter(Material.is_deleted == False)
    if keyword:
        q = q.filter(or_(Material.name.like(f'%{keyword}%'),
                         Material.code.like(f'%{keyword}%'),
                         Material.specification.like(f'%{keyword}%')))
    materials = q.order_by(Material.name).limit(20).all()
    return jsonify([{
        'id': m.id, 'name': m.name, 'code': m.code or '',
        'spec': m.specification or '', 'unit': m.unit or ''
    } for m in materials])


# ========== 公告相关 ==========
from app.models import SysAnnouncement, SysAnnouncementRead


@bp.context_processor
def inject_announcements():
    """注入公告数据到移动端所有模板"""
    if not current_user.is_authenticated:
        return {'mobile_announcements': [], 'unread_announcement_count': 0}
    
    now = datetime.now()
    query = SysAnnouncement.query.filter(
        SysAnnouncement.status == True,
        SysAnnouncement.is_popup == True,
        SysAnnouncement.publish_time <= now,
        or_(SysAnnouncement.expire_time == None, SysAnnouncement.expire_time >= now)
    ).order_by(SysAnnouncement.created_at.desc())
    
    all_active = query.all()
    
    user_role_id = str(current_user.role_id) if current_user.role_id else ''
    user_dept_id = str(current_user.dept_id) if current_user.dept_id else ''
    
    def is_visible(ann):
        if not ann.visible_scope or ann.visible_scope == 'all':
            return True
        elif ann.visible_scope == 'role' and ann.visible_roles:
            try:
                role_ids = json.loads(ann.visible_roles)
                return user_role_id in role_ids
            except:
                return False
        elif ann.visible_scope == 'dept' and ann.visible_depts:
            try:
                dept_ids = json.loads(ann.visible_depts)
                return user_dept_id in dept_ids
            except:
                return False
        return True
    
    all_active = [a for a in all_active if is_visible(a)]
    
    read_ids = [r.announcement_id for r in SysAnnouncementRead.query.filter_by(user_id=current_user.id).all()]
    unread_count = sum(1 for a in all_active if a.id not in read_ids)
    
    serialized = []
    for ann in all_active:
        serialized.append({
            'id': ann.id,
            'title': ann.title,
            'content': ann.content,
            'is_read': ann.id in read_ids,
            'publish_time': ann.publish_time.strftime('%Y-%m-%d %H:%M:%S') if ann.publish_time else '',
        })
    
    return {
        'mobile_announcements': serialized,
        'unread_announcement_count': unread_count
    }


@bp.route('/api/announcement/<int:id>/read', methods=['POST'])
@mobile_auth_required
def mark_announcement_read(id):
    """标记公告已读"""
    existing = SysAnnouncementRead.query.filter_by(
        user_id=current_user.id, announcement_id=id
    ).first()
    if not existing:
        read = SysAnnouncementRead(
            user_id=current_user.id,
            announcement_id=id
        )
        db.session.add(read)
        db.session.commit()
    return jsonify({'success': True})


# ============================================================
# P2-1: Mobile Contract Viewing
# ============================================================

@bp.route('/contracts')
@login_required
def m_contract_list():
    """Mobile contract list"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    status = request.args.get('status', '')
    keyword = request.args.get('keyword', '').strip()

    q = Contract.query.filter_by(project_id=project.id, is_deleted=False)
    if status:
        q = q.filter(Contract.status == status)
    if keyword:
        q = q.filter(or_(Contract.name.like('%%%s%%' % keyword),
                         Contract.code.like('%%%s%%' % keyword)))
    contracts = q.order_by(Contract.created_at.desc()).limit(50).all()
    return render_template('mobile/m_contract_list.html', contracts=contracts,
                           status=status, keyword=keyword, project=project)


@bp.route('/contracts/<int:id>')
@login_required
def m_contract_detail(id):
    """Mobile contract detail"""
    contract = Contract.query.get_or_404(id)
    items = contract.items.all() if contract.items else []
    payments = contract.payments.all() if contract.payments else []
    invoices = contract.invoices.all() if contract.invoices else []
    paid_amount = sum(p.amount or 0 for p in payments)
    invoiced_amount = sum(i.amount_with_tax or 0 for i in invoices)

    return render_template('mobile/m_contract_detail.html',
                           contract=contract, items=items,
                           payments=payments, invoices=invoices,
                           paid_amount=paid_amount,
                           invoiced_amount=invoiced_amount)


# ============================================================
# P2-4: Mobile Transfer (调拨) Application
# ============================================================

@bp.route('/transfers')
@login_required
def m_transfer_list():
    """Mobile transfer list"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    status = request.args.get('status', '')
    q = MaterialTransfer.query.filter(
        or_(MaterialTransfer.from_project_id == project.id,
            MaterialTransfer.to_project_id == project.id)
    )
    if status:
        q = q.filter(MaterialTransfer.status == status)
    transfers = q.order_by(MaterialTransfer.created_at.desc()).limit(50).all()
    return render_template('mobile/m_transfer_list.html', transfers=transfers,
                           status=status, project=project)


@bp.route('/transfers/create', methods=['GET', 'POST'])
@login_required
def m_transfer_create():
    """Mobile transfer create"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))

    if request.method == 'POST':
        to_project_id = request.form.get('to_project_id', type=int)
        transfer_date_str = request.form.get('transfer_date', '')
        try:
            transfer_date = datetime.strptime(transfer_date_str, '%Y-%m-%d').date()
        except Exception:
            transfer_date = date.today()
        remark = request.form.get('remark', '').strip()

        if not to_project_id:
            flash('Please select target project', 'danger')
            return redirect(url_for('mobile.m_transfer_create'))

        # Generate transfer number
        today = datetime.now().strftime('%Y%m%d')
        prefix = 'DB-%s-%s-' % (project.id, today)
        existing = MaterialTransfer.query.filter(
            MaterialTransfer.transfer_no.like('%s%%' % prefix)
        ).count()
        transfer_no = '%s%03d' % (prefix, existing + 1)

        transfer = MaterialTransfer(
            transfer_no=transfer_no,
            from_project_id=project.id,
            to_project_id=to_project_id,
            transfer_date=transfer_date,
            status='draft',
            applicant=current_user.name or current_user.username,
            remark=remark,
        )
        db.session.add(transfer)
        db.session.flush()

        # Add items
        material_ids = request.form.getlist('material_id[]')
        transfer_qtys = request.form.getlist('transfer_qty[]')
        for idx, mid in enumerate(material_ids):
            if not mid:
                continue
            qty = _m_to_float(transfer_qtys[idx] if idx < len(transfer_qtys) else 0)
            if qty == 0:
                continue
            mat = Material.query.get(int(mid))
            item = MaterialTransferItem(
                transfer_id=transfer.id,
                material_id=int(mid),
                material_name=mat.name if mat else '',
                specification=mat.specification if mat else '',
                unit=mat.unit if mat else '',
                transfer_qty=qty,
            )
            db.session.add(item)

        db.session.commit()

        # Submit to approval if requested
        if request.form.get('submit_type') == 'submit':
            from app.approval.service import submit_approval
            success, msg, instance = submit_approval('material_transfer', transfer.id,
                                                     applicant_id=current_user.id)
            if success:
                transfer.status = 'pending'
                db.session.commit()
                flash('Transfer submitted for approval', 'success')
            else:
                flash('Submit failed: %s' % msg, 'danger')
        else:
            flash('Transfer saved as draft', 'success')

        return redirect(url_for('mobile.m_transfer_detail', id=transfer.id))

    projects = Project.query.filter(Project.id != project.id).all()
    return render_template('mobile/m_transfer_form.html', project=project,
                           projects=projects, today=date.today().isoformat())


@bp.route('/transfers/<int:id>')
@login_required
def m_transfer_detail(id):
    """Mobile transfer detail"""
    transfer = MaterialTransfer.query.get_or_404(id)
    items = transfer.items.all() if transfer.items else []
    instance = None
    if transfer.approval_instance_id:
        from app.approval.service import get_instance_by_biz
        instance = get_instance_by_biz('material_transfer', id)
    return render_template('mobile/m_transfer_detail.html',
                           transfer=transfer, items=items, instance=instance)


# ============================================================
# P2-7/8: Mobile AI API Endpoints
# ============================================================

@bp.route('/api/ai/approval_opinion', methods=['POST'])
@mobile_auth_required
def api_ai_approval_opinion():
    """Mobile: AI-assisted approval opinion generation"""
    from app.ai.service import get_ai_service
    ai = get_ai_service()

    data = request.get_json() or {}
    biz_type = data.get('biz_type', '')
    action = data.get('action', 'approve')
    biz_data = data.get('biz_data', {})

    if not biz_type:
        return jsonify({'success': False, 'message': 'Missing biz_type'})

    result, error = ai.call_with_scene(
        'text:approval_opinion',
        'Generate %s opinion for %s' % (action, biz_type),
        extra_context={'biz_type': biz_type, 'action': action, 'biz_data': biz_data},
    )

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'data': result})


@bp.route('/api/ai/inventory_analysis', methods=['POST'])
@mobile_auth_required
def api_ai_inventory_analysis():
    """Mobile: AI inventory analysis"""
    from app.ai.service import get_ai_service
    ai = get_ai_service()

    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': 'Please select project'})

    inventories = Inventory.query.filter_by(project_id=project_id).all()
    inventory_data = []
    for inv in inventories[:50]:
        mat = inv.material
        safety = getattr(inv, 'safety_stock', 0) or 0
        inventory_data.append({
            'material': mat.name if mat else '',
            'specification': mat.specification if mat else '',
            'quantity': float(inv.quantity or 0),
            'unit': mat.unit if mat else '',
            'safety_stock': float(safety),
        })

    if not inventory_data:
        return jsonify({'success': False, 'message': 'No inventory data'})

    result, error = ai.analyze_inventory({'inventory': inventory_data})

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'data': result})


@bp.route('/api/ai/chat', methods=['POST'])
@mobile_auth_required
def api_ai_chat():
    """Mobile: AI chat"""
    from app.ai.service import get_ai_service
    ai = get_ai_service()

    data = request.get_json() or {}
    message = data.get('message', '')
    if not message:
        return jsonify({'success': False, 'message': 'Empty message'})

    project_id = session.get('current_project_id')
    context_data = {}
    if project_id:
        inventories = Inventory.query.filter_by(project_id=project_id).limit(20).all()
        context_data['inventory'] = [{
            'material': inv.material.name if inv.material else '',
            'quantity': float(inv.quantity or 0),
            'unit': inv.material.unit if inv.material else '',
        } for inv in inventories]

    result, error = ai.chat(message, extra_context=context_data if context_data else None)

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'data': result})





# ============================================================
# 移动端工程计算器
# ============================================================

@bp.route('/calculator')
@login_required
def m_calculator():
    """移动端工程计算器"""
    project = _m_require_project()
    if not project:
        return redirect(url_for('mobile.profile'))
    return render_template('mobile/calculator.html', project=project)


# ============================================================
# P2-5: Mobile Location API
# ============================================================

@bp.route('/api/location', methods=['POST'])
@mobile_auth_required
def api_save_location():
    """Save mobile device geolocation"""
    data = request.get_json() or {}
    lat = data.get('lat')
    lng = data.get('lng')
    accuracy = data.get('accuracy')

    if lat and lng:
        # Store in session for use in stock_in/out
        session['mobile_location'] = {
            'lat': lat,
            'lng': lng,
            'accuracy': accuracy,
            'timestamp': datetime.now().isoformat()
        }
        return jsonify({'success': True, 'message': 'Location saved'})

    return jsonify({'success': False, 'message': 'Invalid location data'}), 400
