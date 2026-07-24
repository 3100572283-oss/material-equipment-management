from functools import wraps
import time
import json
from flask import abort, request, jsonify, redirect, flash, url_for, session
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


def module_required(module_key):
    """模块开关校验装饰器

    检查指定业务模块是否在当前项目中启用。
    用法: @module_required('module_turnover')

    模块未启用时：
    - API请求返回JSON 403
    - 页面请求重定向到首页并提示
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)

            # 从session获取当前项目
            project_id = session.get('current_project_id')
            if project_id:
                from app.models import Project
                project = Project.query.get(project_id)
                if project and not project.is_module_enabled(module_key):
                    # API请求返回JSON
                    if request.path.startswith('/api/') or request.is_json:
                        return jsonify({'code': 403, 'message': '该功能模块未在当前项目中启用'}), 403
                    # 页面请求重定向
                    flash('该功能模块未在当前项目中启用', 'warning')
                    return redirect(url_for('main.index'))

            # 同时检查系统级模块开关
            from app.models import SysModule
            sys_module = SysModule.query.filter_by(module_key=module_key).first()
            if sys_module and not sys_module.status:
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({'code': 403, 'message': '该功能模块已被系统关闭'}), 403
                flash('该功能模块已被系统关闭', 'warning')
                return redirect(url_for('main.index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def data_scope_required(get_instance):
    """数据权限校验装饰器 - 单条数据访问级别

    用于详情/编辑/删除接口，检查当前用户是否有权限访问指定的业务对象。

    用法:
        @data_scope_required(lambda id: StockIn.query.get_or_404(id))

    配合 apply_data_scope 使用：列表查询自动注入过滤条件，
    详情/编辑/删除接口需要本装饰器防止直接通过URL绕过。
    """
    from app.models import SysRoleDataScope
    from app.utils import get_sub_dept_ids
    from flask_login import current_user

    def decorator(f):
        from functools import wraps
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if current_user.is_admin():
                return f(*args, **kwargs)

            instance = get_instance(*args, **kwargs)
            if instance is None:
                abort(404)

            role = current_user.role_obj
            data_scope = 'all'
            custom_depts = []
            if role:
                cfg = SysRoleDataScope.query.filter_by(role_id=role.id).first()
                if cfg:
                    data_scope = cfg.data_scope or 'all'
                    if cfg.custom_depts:
                        import json as _json
                        try:
                            raw = _json.loads(cfg.custom_depts)
                            custom_depts = [int(x) for x in raw if str(x).isdigit()]
                        except Exception:
                            custom_depts = [int(x.strip()) for x in cfg.custom_depts.split(',') if x.strip().isdigit()]

            if data_scope == 'all':
                return f(*args, **kwargs)

            allowed = False
            if data_scope == 'self':
                creator = (getattr(instance, 'created_by_id', None) or
                          getattr(instance, 'applicant_id', None) or
                          getattr(instance, 'operator_id', None))
                if creator is not None:
                    allowed = (creator == current_user.id)
                else:
                    cb = getattr(instance, 'created_by', None)
                    allowed = (cb == current_user.username)
            elif data_scope == 'dept':
                if current_user.dept_id and getattr(instance, 'dept_id', None) is not None:
                    allowed = (instance.dept_id == current_user.dept_id)
            elif data_scope == 'dept_and_sub':
                if current_user.dept_id and getattr(instance, 'dept_id', None) is not None:
                    dept_ids = get_sub_dept_ids(current_user.dept_id)
                    dept_ids.append(current_user.dept_id)
                    allowed = (instance.dept_id in dept_ids)
            elif data_scope == 'custom':
                if custom_depts and getattr(instance, 'dept_id', None) is not None:
                    allowed = (instance.dept_id in custom_depts)

            if not allowed:
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({'code': 403, 'message': '无权限访问该数据'}), 403
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
