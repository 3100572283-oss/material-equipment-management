from functools import wraps
import time
import json
from flask import abort, request
from flask_login import current_user


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def editor_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_edit():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def permission_required(permission):
    """权限校验装饰器，检查用户是否拥有指定操作权限"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if not current_user.has_permission(permission):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def log_audit(module, operation):
    """审计日志装饰器，自动记录请求参数和耗时"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            start = time.time()
            status = 'success'
            error_msg = None
            try:
                result = f(*args, **kwargs)
                return result
            except Exception as e:
                status = 'failed'
                error_msg = str(e)
                raise
            finally:
                from app.utils import log_audit as _log
                cost = int((time.time() - start) * 1000)
                params = {}
                if request:
                    if request.method == 'POST':
                        params = dict(request.form)
                        # 过滤密码等敏感字段
                        for k in list(params.keys()):
                            if 'password' in k.lower() or 'secret' in k.lower():
                                params[k] = '***'
                    elif request.method == 'GET':
                        params = dict(request.args)
                biz_id = kwargs.get('id')
                _log(module=module, operation=operation, biz_type=module,
                     biz_id=biz_id, params=params, status=status,
                     error_msg=error_msg, cost_time=cost)
        return wrapped
    return decorator
