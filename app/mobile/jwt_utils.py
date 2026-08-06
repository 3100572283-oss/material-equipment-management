# -*- coding: utf-8 -*-
"""移动端JWT认证工具

提供token签发、验证、刷新功能。
@mobile_auth_required 装饰器同时支持JWT和Session认证。
"""
import jwt
import time
from functools import wraps
from flask import request, jsonify, g, current_app
from flask_login import current_user, login_user


def generate_access_token(user_id, username, role_id=None):
    """生成access token（24小时有效）"""
    now = int(time.time())
    payload = {
        'user_id': user_id,
        'username': username,
        'role_id': role_id,
        'type': 'access',
        'iat': now,
        'exp': now + current_app.config.get('JWT_EXPIRATION_HOURS', 24) * 3600,
    }
    secret = current_app.config.get('JWT_SECRET_KEY', 'dev-secret')
    return jwt.encode(payload, secret, algorithm='HS256')


def generate_refresh_token(user_id, username):
    """生成refresh token（30天有效）"""
    now = int(time.time())
    payload = {
        'user_id': user_id,
        'username': username,
        'type': 'refresh',
        'iat': now,
        'exp': now + current_app.config.get('JWT_REFRESH_EXPIRATION_DAYS', 30) * 86400,
    }
    secret = current_app.config.get('JWT_SECRET_KEY', 'dev-secret')
    return jwt.encode(payload, secret, algorithm='HS256')


def verify_token(token):
    """验证token，返回payload或None"""
    try:
        secret = current_app.config.get('JWT_SECRET_KEY', 'dev-secret')
        payload = jwt.decode(token, secret, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_token_from_request():
    """从请求中提取token（Authorization: Bearer <token>）"""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    return None


def mobile_auth_required(f):
    """移动端双认证装饰器
    
    优先检查JWT token，如果没有则回退到session认证。
    这样既支持原生App（JWT），也支持浏览器访问（session）。
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 尝试JWT认证
        token = get_token_from_request()
        if token:
            payload = verify_token(token)
            if payload and payload.get('type') == 'access':
                # JWT认证成功，设置当前用户信息
                g.jwt_user = payload
                g.auth_method = 'jwt'
                return f(*args, **kwargs)
            else:
                return jsonify({'success': False, 'message': 'token无效或已过期，请重新登录'}), 401
        
        # 回退到session认证
        if current_user.is_authenticated:
            g.auth_method = 'session'
            return f(*args, **kwargs)
        
        # 两种认证都失败
        return jsonify({'success': False, 'message': '未认证，请登录'}), 401
    
    return decorated_function


def get_current_user_id():
    """获取当前用户ID（兼容JWT和session）"""
    if hasattr(g, 'jwt_user'):
        return g.jwt_user.get('user_id')
    if current_user.is_authenticated:
        return current_user.id
    return None


def get_current_username():
    """获取当前用户名（兼容JWT和session）"""
    if hasattr(g, 'jwt_user'):
        return g.jwt_user.get('username')
    if current_user.is_authenticated:
        return current_user.username
    return None
