from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from app.org_sync import bp
from app import db
from app.models import SysDept, User, SysRole, SystemConfig, SysOperationLog
from app.decorators import admin_required
from datetime import datetime
import json
import requests


# ============== 配置键常量 ==============

_PLATFORM_CONFIG_KEYS = {
    'dingtalk': 'org_sync_dingtalk_config',
    'wechat': 'org_sync_wechat_config',
    'feishu': 'org_sync_feishu_config',
}

_PLATFORM_LABELS = {
    'dingtalk': '钉钉',
    'wechat': '企业微信',
    'feishu': '飞书',
}

_DEFAULT_CONFIG = {
    'app_key': '',
    'app_secret': '',
    'enabled': False,
    'last_sync_time': None,
    'last_sync_status': None,
    'last_sync_message': '',
}


# ============== 辅助函数 ==============

def _mask_secret(secret):
    """掩码处理 AppSecret，只显示前后各4位字符"""
    if not secret:
        return ''
    if len(secret) <= 8:
        return '****'
    return secret[:4] + '****' + secret[-4:]


def _get_platform_config(platform):
    """从 SystemConfig 表读取平台配置"""
    key = _PLATFORM_CONFIG_KEYS.get(platform)
    if not key:
        return dict(_DEFAULT_CONFIG)
    cfg = SystemConfig.query.filter_by(config_key=key).first()
    if not cfg or not cfg.config_value:
        return dict(_DEFAULT_CONFIG)
    try:
        data = json.loads(cfg.config_value)
        # 合并默认值，防止字段缺失
        merged = dict(_DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except (ValueError, TypeError):
        return dict(_DEFAULT_CONFIG)


def _save_platform_config(platform, config_dict):
    """保存平台配置到 SystemConfig 表"""
    key = _PLATFORM_CONFIG_KEYS.get(platform)
    if not key:
        return
    cfg = SystemConfig.query.filter_by(config_key=key).first()
    value = json.dumps(config_dict, ensure_ascii=False)
    if cfg:
        cfg.config_value = value
    else:
        cfg = SystemConfig(
            config_key=key,
            config_value=value,
            description=f'组织架构同步-{_PLATFORM_LABELS.get(platform, platform)}配置',
        )
        db.session.add(cfg)
    db.session.commit()


def _record_sync_log(platform, operation, status, message, params=None, cost_time=0):
    """记录同步操作日志到 SysOperationLog"""
    try:
        log = SysOperationLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            username=current_user.username if current_user.is_authenticated else None,
            module='org_sync',
            operation=operation,
            biz_type=platform,
            biz_id=None,
            method=request.method if request else None,
            params=json.dumps(params, ensure_ascii=False) if params else None,
            ip_address=request.remote_addr if request else None,
            user_agent=(request.headers.get('User-Agent', '')[:256] if request else None),
            operation_time=datetime.utcnow(),
            cost_time=cost_time,
            status=status,
            error_msg=message if status != 'success' else None,
            changes=message if status == 'success' else None,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()


# ============== 第三方平台对接框架 ==============

def _sync_dingtalk(app_key, app_secret):
    """钉钉同步"""
    # 1. 获取access_token
    # 2. 获取部门列表
    # 3. 获取用户列表
    # 4. 更新本地数据
    try:
        # 获取access_token
        resp = requests.get('https://oapi.dingtalk.com/gettoken', params={
            'appkey': app_key, 'appsecret': app_secret
        }, timeout=10)
        data = resp.json()
        if data.get('errcode') != 0:
            return {'success': False, 'message': data.get('errmsg', '获取token失败')}
        token = data['access_token']

        # 获取部门列表
        resp = requests.get('https://oapi.dingtalk.com/department/list', params={
            'access_token': token
        }, timeout=10)
        dept_data = resp.json()
        new_depts = 0
        updated_depts = 0
        if dept_data.get('errcode') == 0:
            for dept in dept_data.get('department', []):
                dept_id = dept.get('id')
                dept_name = dept.get('name', '')
                parent_id = dept.get('parentid') or 0
                code = f'DT_{dept_id}'
                existing = SysDept.query.filter_by(dept_code=code).first()
                if existing:
                    existing.dept_name = dept_name
                    existing.parent_id = parent_id
                    updated_depts += 1
                else:
                    new_dept = SysDept(
                        dept_code=code,
                        dept_name=dept_name,
                        parent_id=parent_id,
                        sort=dept.get('order', 0),
                    )
                    db.session.add(new_dept)
                    new_depts += 1
            db.session.commit()
        else:
            return {'success': False, 'message': dept_data.get('errmsg', '获取部门列表失败')}

        # 获取用户列表（按部门拉取，此处仅做框架）
        # TODO: 完整实现需要遍历每个部门获取成员
        new_users = 0
        updated_users = 0

        return {
            'success': True,
            'message': '同步成功',
            'new_depts': new_depts,
            'updated_depts': updated_depts,
            'new_users': new_users,
            'updated_users': updated_users,
            'errors': [],
        }
    except Exception as e:
        return {'success': False, 'message': str(e), 'new_depts': 0, 'updated_depts': 0,
                'new_users': 0, 'updated_users': 0, 'errors': [str(e)]}


def _sync_wechat(app_key, app_secret):
    """企业微信同步"""
    # 类似钉钉的逻辑
    try:
        resp = requests.get('https://qyapi.weixin.qq.com/cgi-bin/gettoken', params={
            'corpid': app_key, 'corpsecret': app_secret
        }, timeout=10)
        data = resp.json()
        if data.get('errcode') != 0:
            return {'success': False, 'message': data.get('errmsg', '获取token失败')}
        token = data['access_token']
        # 框架占位：完整同步逻辑待配置完整后启用
        # TODO: 调用 /cgi-bin/department/list 和 /cgi-bin/user/simplelist
        return {
            'success': True,
            'message': '连接成功（同步功能待配置完整后启用）',
            'new_depts': 0,
            'updated_depts': 0,
            'new_users': 0,
            'updated_users': 0,
            'errors': [],
        }
    except Exception as e:
        return {'success': False, 'message': str(e), 'new_depts': 0, 'updated_depts': 0,
                'new_users': 0, 'updated_users': 0, 'errors': [str(e)]}


def _sync_feishu(app_key, app_secret):
    """飞书同步"""
    try:
        resp = requests.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', json={
            'app_id': app_key, 'app_secret': app_secret
        }, timeout=10)
        data = resp.json()
        if data.get('code') != 0:
            return {'success': False, 'message': data.get('msg', '获取token失败')}
        token = data['tenant_access_token']
        # 框架占位：完整同步逻辑待配置完整后启用
        # TODO: 调用 /open-apis/contact/v3/departments 和 /open-apis/contact/v3/users
        return {
            'success': True,
            'message': '连接成功（同步功能待配置完整后启用）',
            'new_depts': 0,
            'updated_depts': 0,
            'new_users': 0,
            'updated_users': 0,
            'errors': [],
        }
    except Exception as e:
        return {'success': False, 'message': str(e), 'new_depts': 0, 'updated_depts': 0,
                'new_users': 0, 'updated_users': 0, 'errors': [str(e)]}


_SYNC_FUNCS = {
    'dingtalk': _sync_dingtalk,
    'wechat': _sync_wechat,
    'feishu': _sync_feishu,
}


def _test_connection(platform, app_key, app_secret):
    """测试第三方平台连接，仅验证能否获取 access_token"""
    try:
        if platform == 'dingtalk':
            resp = requests.get('https://oapi.dingtalk.com/gettoken', params={
                'appkey': app_key, 'appsecret': app_secret
            }, timeout=10)
            data = resp.json()
            if data.get('errcode') != 0:
                return {'success': False, 'message': data.get('errmsg', '获取token失败')}
            return {'success': True, 'message': '连接成功，access_token 获取正常'}
        elif platform == 'wechat':
            resp = requests.get('https://qyapi.weixin.qq.com/cgi-bin/gettoken', params={
                'corpid': app_key, 'corpsecret': app_secret
            }, timeout=10)
            data = resp.json()
            if data.get('errcode') != 0:
                return {'success': False, 'message': data.get('errmsg', '获取token失败')}
            return {'success': True, 'message': '连接成功，access_token 获取正常'}
        elif platform == 'feishu':
            resp = requests.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', json={
                'app_id': app_key, 'app_secret': app_secret
            }, timeout=10)
            data = resp.json()
            if data.get('code') != 0:
                return {'success': False, 'message': data.get('msg', '获取token失败')}
            return {'success': True, 'message': '连接成功，tenant_access_token 获取正常'}
        else:
            return {'success': False, 'message': f'不支持的平台: {platform}'}
    except requests.exceptions.Timeout:
        return {'success': False, 'message': '请求超时，请检查网络或稍后重试'}
    except requests.exceptions.ConnectionError as e:
        return {'success': False, 'message': f'网络连接失败: {e}'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ============== 路由 ==============

@bp.route('/')
@login_required
@admin_required
def index():
    """同步配置页面"""
    platforms = ['dingtalk', 'wechat', 'feishu']
    configs = {}
    for p in platforms:
        cfg = _get_platform_config(p)
        # 页面展示时对 app_secret 掩码
        cfg['app_secret_masked'] = _mask_secret(cfg.get('app_secret', ''))
        cfg['platform_label'] = _PLATFORM_LABELS[p]
        configs[p] = cfg
    return render_template('org_sync/config.html', configs=configs)


@bp.route('/save_config', methods=['POST'])
@login_required
@admin_required
def save_config():
    """保存配置"""
    platform = request.form.get('platform', '').strip()
    app_key = request.form.get('app_key', '').strip()
    app_secret = request.form.get('app_secret', '').strip()
    enabled = request.form.get('enabled', 'false').lower() == 'true'

    if platform not in _PLATFORM_CONFIG_KEYS:
        return jsonify({'success': False, 'message': '无效的平台类型'})

    # 读取现有配置以保留 last_sync_* 字段
    existing = _get_platform_config(platform)
    # 如果 app_secret 为空，则保留原值（用户未修改密钥）
    if not app_secret:
        app_secret = existing.get('app_secret', '')

    new_config = {
        'app_key': app_key,
        'app_secret': app_secret,
        'enabled': enabled,
        'last_sync_time': existing.get('last_sync_time'),
        'last_sync_status': existing.get('last_sync_status'),
        'last_sync_message': existing.get('last_sync_message', ''),
    }
    _save_platform_config(platform, new_config)

    _record_sync_log(platform, 'save_config', 'success',
                     f'保存{_PLATFORM_LABELS[platform]}配置，启用状态: {enabled}')

    return jsonify({'success': True, 'message': '配置保存成功'})


@bp.route('/sync/<platform>', methods=['POST'])
@login_required
@admin_required
def sync(platform):
    """手动同步"""
    if platform not in _PLATFORM_CONFIG_KEYS:
        return jsonify({'success': False, 'message': '无效的平台类型'})

    cfg = _get_platform_config(platform)
    if not cfg.get('enabled'):
        return jsonify({'success': False, 'message': f'{_PLATFORM_LABELS[platform]}同步未启用，请先在配置中开启'})

    app_key = cfg.get('app_key', '')
    app_secret = cfg.get('app_secret', '')
    if not app_key or not app_secret:
        return jsonify({'success': False, 'message': 'AppKey 或 AppSecret 未配置'})

    start = datetime.utcnow()
    sync_func = _SYNC_FUNCS[platform]
    result = sync_func(app_key, app_secret)
    cost = int((datetime.utcnow() - start).total_seconds() * 1000)

    status = 'success' if result.get('success') else 'failed'
    message = result.get('message', '')

    # 更新配置中的同步状态
    cfg['last_sync_time'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cfg['last_sync_status'] = status
    cfg['last_sync_message'] = message
    _save_platform_config(platform, cfg)

    _record_sync_log(platform, 'sync', status, message,
                     params={
                         'new_depts': result.get('new_depts', 0),
                         'updated_depts': result.get('updated_depts', 0),
                         'new_users': result.get('new_users', 0),
                         'updated_users': result.get('updated_users', 0),
                     }, cost_time=cost)

    return jsonify(result)


@bp.route('/sync_log')
@login_required
@admin_required
def sync_log():
    """同步日志页面"""
    page = request.args.get('page', 1, type=int)
    platform_filter = request.args.get('platform', '', type=str)
    status_filter = request.args.get('status', '', type=str)

    query = SysOperationLog.query.filter_by(module='org_sync')
    if platform_filter:
        query = query.filter_by(biz_type=platform_filter)
    if status_filter:
        query = query.filter_by(status=status_filter)
    pagination = query.order_by(SysOperationLog.operation_time.desc()).paginate(
        page=page, per_page=20, error_out=False)

    return render_template('org_sync/sync_log.html',
                           pagination=pagination,
                           platform_filter=platform_filter,
                           status_filter=status_filter,
                           platform_labels=_PLATFORM_LABELS)


@bp.route('/api/test_connection')
@login_required
@admin_required
def test_connection():
    """测试连接"""
    platform = request.args.get('platform', '').strip()
    if platform not in _PLATFORM_CONFIG_KEYS:
        return jsonify({'success': False, 'message': '无效的平台类型'})

    cfg = _get_platform_config(platform)
    app_key = cfg.get('app_key', '')
    app_secret = cfg.get('app_secret', '')
    if not app_key or not app_secret:
        return jsonify({'success': False, 'message': 'AppKey 或 AppSecret 未配置'})

    result = _test_connection(platform, app_key, app_secret)
    _record_sync_log(platform, 'test_connection',
                     'success' if result.get('success') else 'failed',
                     result.get('message', ''))
    return jsonify(result)
