from flask import render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_required, current_user
from app.profile import bp
from app import db
from app.models import User
import json
from app.decorators import log_audit

@bp.route('/settings', methods=['GET', 'POST'])
@login_required
@log_audit(module='profile', operation='保存个人设置')
def settings():
    user = User.query.get_or_404(current_user.id)
    if request.method == 'POST':
        user.name = request.form.get('name', user.name)
        user.email = request.form.get('email', user.email)
        user.phone = request.form.get('phone', user.phone)
        user.department = request.form.get('department', user.department)
        per_page = request.form.get('per_page', 10, type=int)
        user.per_page = per_page if per_page in [10, 20, 50, 100] else 10
        # 通知渠道偏好
        channels = request.form.getlist('notify_channels')
        user.notify_channels = json.dumps(channels)
        db.session.commit()
        flash('个人设置已保存', 'success')
        return redirect(url_for('profile.settings'))
    return render_template('profile/settings.html', user=user)

@bp.route('/change_password', methods=['POST'])
@login_required
@log_audit(module='profile', operation='修改密码')
def change_password():
    user = User.query.get_or_404(current_user.id)
    old_pwd = request.form.get('old_password', '')
    new_pwd = request.form.get('new_password', '')
    confirm_pwd = request.form.get('confirm_password', '')
    from werkzeug.security import check_password_hash, generate_password_hash
    if not check_password_hash(user.password_hash, old_pwd):
        flash('原密码错误', 'danger')
        return redirect(url_for('profile.settings'))
    if new_pwd != confirm_pwd:
        flash('两次输入的新密码不一致', 'danger')
        return redirect(url_for('profile.settings'))
    if len(new_pwd) < 6:
        flash('密码长度不能少于6位', 'danger')
        return redirect(url_for('profile.settings'))
    user.password_hash = generate_password_hash(new_pwd)
    db.session.commit()
    flash('密码修改成功', 'success')
    return redirect(url_for('profile.settings'))
