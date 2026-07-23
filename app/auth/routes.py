from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from flask import session as flask_session
from app.auth import bp
from app import db
from app.models import User, LoginLog
from app.utils import log_operation
from app.decorators import log_audit
import uuid


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


@bp.route('/login', methods=['GET', 'POST'])
@log_audit(module='auth', operation='登录')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(username=username).first()

        if user and user.locked_until and user.locked_until > datetime.now():
            _record_login_log(user, 'failed', fail_reason='账号已锁定')
            remaining = int((user.locked_until - datetime.now()).total_seconds() / 60)
            flash(f'账号已被锁定，请 {remaining} 分钟后再试。', 'danger')
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
        elif len(new_password) < 6:
            flash('新密码长度不能少于6位。', 'danger')
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
