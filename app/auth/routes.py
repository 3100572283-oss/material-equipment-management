import re
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from flask import session as flask_session
from app.auth import bp
from app import db
from app.models import User, LoginLog, SysMenu, SysRoleMenu, SysRole, SysDept, Project, SysModule
from app.utils import log_operation
from app.decorators import log_audit
import os
import uuid
import random
import string
import io
import base64
import json
from PIL import Image, ImageDraw, ImageFont


def _parse_user_agent(user_agent_str):
    """简单解析User-Agent获取浏览器和操作系统"""
    browser = 'Unknown'
    os = 'Unknown'
    if not user_agent_str:
        return browser, os
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
        os = 'macOS'
    elif 'windows' in ua:
        os = 'Windows'
    elif 'linux' in ua:
        os = 'Linux'
    elif 'android' in ua:
        os = 'Android'
    elif 'iphone' in ua or 'ipad' in ua:
        os = 'iOS'
    return browser, os


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




@bp.route('/captcha')
def captcha():
    """生成验证码图片"""
    # 生成4位随机验证码
    chars = string.ascii_uppercase + string.digits
    # 排除容易混淆的字符
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '').replace('L', '')
    captcha_text = ''.join(random.choices(chars, k=4))

    # 存入session
    session['captcha'] = captcha_text
    session['captcha_time'] = datetime.now().timestamp()

    # 生成图片
    width, height = 120, 40
    image = Image.new('RGB', (width, height), color=(248, 249, 250))
    draw = ImageDraw.Draw(image)

    # 尝试加载字体
    font = None
    font_paths = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                  '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf']
    for fp in font_paths:
        if os.path.exists(fp):
            font = ImageFont.truetype(fp, 28)
            break
    if not font:
        font = ImageFont.load_default()

    # 绘制验证码字符（带随机偏移）
    for i, char in enumerate(captcha_text):
        x = 10 + i * 26 + random.randint(-3, 3)
        y = random.randint(0, 8)
        # 随机颜色
        color = (random.randint(20, 100), random.randint(20, 100), random.randint(80, 150))
        draw.text((x, y), char, fill=color, font=font)

    # 绘制干扰线
    for _ in range(4):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(150, 200), random.randint(150, 200), random.randint(150, 200)), width=1)

    # 绘制干扰点
    for _ in range(50):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)))

    # 输出为base64
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode()

    from flask import send_file
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@bp.route('/forgot_password')
def forgot_password():
    """忘记密码页面"""
    return render_template('auth/forgot_password.html')



@bp.route('/login', methods=['GET', 'POST'])
@log_audit(module='auth', operation='登录')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        # P2: 验证码校验
        captcha_input = request.form.get('captcha', '').strip().upper()
        captcha_session = session.pop('captcha', '')
        if not captcha_input or not captcha_session or captcha_input != captcha_session:
            flash('验证码错误或已过期，请重新输入。', 'danger')
            return render_template('auth/login.html')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(username=username).first()

        if user and user.locked_until and user.locked_until > datetime.now():
            _record_login_log(user, 'failed', fail_reason='账号已锁定')
            remaining = int((user.locked_until - datetime.now()).total_seconds() / 60)
            flash(f'账号已被锁定，请 {remaining} 分钟后再试。', 'danger')
            return render_template('auth/login.html')

        if user and user.status == 'inactive':
            _record_login_log(user, 'failed', fail_reason='账号已停用')
            flash('账号已停用，请联系管理员。', 'danger')
            return render_template('auth/login.html')

        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=remember)
            user.last_login_at = datetime.now()
            user.last_login_ip = request.remote_addr
            user.failed_login_count = 0
            user.locked_until = None
            # 用户所属部门为项目部且未设置主项目时，自动关联部门对应项目
            if not user.project_id and user.dept_id:
                from app.models import SysDept
                dept = SysDept.query.get(user.dept_id)
                if dept and dept.dept_type == 'project' and dept.project_id:
                    user.project_id = dept.project_id
            log_id = _record_login_log(user, 'success')
            flask_session['login_log_id'] = log_id
            db.session.commit()
            log_operation('登录', module='系统', description=f'用户 {username} 登录系统')
            # 强制修改密码拦截
            if user.must_change_password:
                flash('为了账户安全，请先修改密码。', 'warning')
                return redirect(url_for('auth.change_password', forced='1'))
            next_page = request.args.get('next')
            flash('登录成功！', 'success')
            return redirect(next_page if next_page else url_for('main.index'))
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

    return render_template('auth/login.html')


@bp.route('/logout')
@login_required
@log_audit(module='auth', operation='退出')
def logout():
    log_id = flask_session.get('login_log_id')
    if log_id:
        log = LoginLog.query.get(log_id)
        if log:
            log.logout_time = datetime.now()
            db.session.commit()
    log_operation('退出', module='系统', description=f'用户 {current_user.username} 退出系统')
    logout_user()
    session.pop('current_project_id', None)
    session.pop('current_project_name', None)
    flask_session.pop('login_log_id', None)
    flash('您已退出登录。', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/change_password', methods=['GET', 'POST'])
@login_required
@log_audit(module='auth', operation='修改密码')
def change_password():
    forced = request.args.get('forced') == '1'

    if request.method == 'POST':
        old_password = request.form.get('old_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not check_password_hash(current_user.password_hash, old_password):
            flash('当前密码错误。', 'danger')
        elif new_password != confirm_password:
            flash('两次输入的新密码不一致。', 'danger')
        elif len(new_password) < 8:
            flash('新密码长度不能少于8位。', 'danger')
        elif not (re.search(r'[a-z]', new_password) and re.search(r'[A-Z]', new_password) and re.search(r'[0-9]', new_password)):
            flash('密码必须包含大写字母、小写字母和数字。', 'danger')
        elif new_password == old_password:
            flash('新密码不能与原密码相同。', 'danger')
        else:
            current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
            current_user.must_change_password = False
            db.session.commit()
            log_operation('修改密码', module='系统', description=f'用户 {current_user.username} 修改密码')
            flash('密码修改成功。', 'success')
            if forced:
                return redirect(url_for('main.index'))
            logout_user()
            return redirect(url_for('auth.login'))

    return render_template('auth/change_password.html', forced=forced)


@bp.route('/api/user/permissions')
@login_required
def api_user_permissions():
    """获取当前用户全部权限信息（统一权限服务唯一出口）

    前端登录后仅调用这一次，缓存全局使用。
    返回内容包含：
    - userInfo: 用户基础信息
    - menus: 有权限的菜单树（用于渲染左侧菜单）
    - permissions: 所有页面的按钮权限标识集合
    - dataScope: 组织数据范围
    - orgDataScope: 组织数据范围（部门ID列表）
    - projects: 可访问项目列表
    - modules: 模块启用状态
    """
    from app.services.permission_service import permission_service

    user = current_user
    result = {
        'userInfo': {},
        'menus': [],
        'permissions': [],
        'dataScope': {},
        'orgDataScope': {},
        'projects': [],
        'modules': {}
    }

    # 用户基础信息
    result['userInfo'] = {
        'id': user.id,
        'username': user.username,
        'realName': getattr(user, 'name', None) or user.username,
        'deptId': user.dept_id,
        'roleId': user.role_id,
        'isAdmin': user.is_admin()
    }

    # 获取启用的模块列表（系统级）
    enabled_modules = {}
    for m in SysModule.query.all():
        enabled_modules[m.module_key] = bool(m.status)
    result['modules'] = enabled_modules

    # 1. 获取数据权限配置（委托给统一权限服务）
    scope_info = permission_service.get_user_data_scope(user)
    role = user.role_obj
    if role:
        result['dataScope'] = {
            'scope': scope_info['scope'],
            'roleName': role.role_name,
            'roleCode': role.role_code
        }

    # 2. 获取组织数据范围（部门维度）
    result['orgDataScope'] = permission_service.get_org_data_scope(user)

    # 3. 获取用户所有按钮权限标识（委托给统一权限服务）
    result['permissions'] = permission_service.get_user_permissions_list(user)

    # 4. 获取可见项目列表（委托给统一权限服务）
    result['projects'] = permission_service.get_accessible_projects(user)

    # 5. 获取有权限的菜单树（委托给统一权限服务）
    result['menus'] = permission_service.get_user_menu_tree(user)

    return json.dumps(result, ensure_ascii=False)


@bp.route('/api/user/permission-detail')
@login_required
def api_user_permission_detail():
    """权限自检接口：获取当前用户所有权限明细

    用于排查问题，确保配置、内核计算、前端显示三者可追溯、可核对。
    """
    from app.services.permission_service import permission_service
    detail = permission_service.get_permission_detail(current_user)
    return json.dumps(detail, ensure_ascii=False)
