# app/integration/qcc_client.py
"""企查查开放平台客户端。

关键设计：
1) 凭据、base_url、接口路径、鉴权方式全部读自 ext_credential / ext_endpoint（后台可配）。
2) 命中缓存不计费；缓存过期才真实调用；调用流水入 ext_query_log。
3) 任何异常（未配置/网络/限额/解析失败）都返回结构化结果，不抛出、不阻断主流程。
"""
import hashlib
import json
import time
from datetime import datetime, timedelta

from app import db
from app.integration.models import (ExtCredential, ExtEndpoint, ExtQueryCache,
                                    ExtQueryLog)

PROVIDER = 'qcc'


class QccResult(dict):
    """统一返回结构：{ok, status, code, message, data, from_cache}"""

    @property
    def ok(self):
        return bool(self.get('ok'))


def _result(ok, status, message, data=None, from_cache=False, code=None):
    return QccResult(ok=ok, status=status, message=message, data=data or {},
                     from_cache=from_cache, code=code)


import re as _re


def _sanitize_message(msg):
    """脱敏：调用异常字符串可能包含完整请求 URL（?key=明文&...），入库/回显前必须清除凭据。"""
    if not msg:
        return msg
    cred = get_credential()
    if cred:
        k = cred.get_key()
        s = cred.get_secret()
        if k:
            msg = msg.replace(k, '***')
        if s:
            msg = msg.replace(s, '***')
    # 兜底：抹掉 URL 中的 key= 参数（无论明文还是已部分编码）
    msg = _re.sub(r'([\?&]key=)[^&\s\'"]+', r'\1***', msg)
    return msg


def get_credential():
    return ExtCredential.query.filter_by(provider=PROVIDER).first()


def is_configured():
    cred = get_credential()
    return bool(cred and cred.enabled and cred.get_key())


def _today_call_count():
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (ExtQueryLog.query
            .filter(ExtQueryLog.provider == PROVIDER,
                    ExtQueryLog.from_cache.is_(False),
                    ExtQueryLog.created_at >= start).count())


def _log(endpoint_code, keyword, ok, from_cache, http_status, elapsed_ms, message, operator):
    message = _sanitize_message(message)
    try:
        db.session.add(ExtQueryLog(
            provider=PROVIDER, endpoint_code=endpoint_code,
            query_key=(keyword or '')[:128], ok=ok, from_cache=from_cache,
            http_status=http_status, elapsed_ms=elapsed_ms,
            message=(message or '')[:512], operator=operator))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _read_cache(endpoint_code, keyword):
    key = ExtQueryCache.build_key(PROVIDER, endpoint_code, keyword)
    row = (ExtQueryCache.query
           .filter_by(provider=PROVIDER, endpoint_code=endpoint_code, cache_key=key)
           .first())
    if row and row.is_valid and row.payload:
        try:
            return json.loads(row.payload)
        except Exception:
            return None
    return None


def _write_cache(endpoint_code, keyword, payload, cache_days):
    key = ExtQueryCache.build_key(PROVIDER, endpoint_code, keyword)
    row = (ExtQueryCache.query
           .filter_by(provider=PROVIDER, endpoint_code=endpoint_code, cache_key=key)
           .first())
    if not row:
        row = ExtQueryCache(provider=PROVIDER, endpoint_code=endpoint_code, cache_key=key)
        db.session.add(row)
    try:
        row.payload = json.dumps(payload, ensure_ascii=False)
    except Exception:
        row.payload = str(payload)
    row.expires_at = datetime.now() + timedelta(days=max(int(cache_days or 0), 0) or 1)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def _build_auth(cred, keyword):
    """按 auth_style 构造 params / headers。"""
    key = cred.get_key()
    secret = cred.get_secret()
    params = {'key': key, 'keyword': keyword}
    headers = {'Accept': 'application/json'}
    if (cred.auth_style or 'header_sign') == 'header_sign':
        timespan = str(int(time.time()))
        token = hashlib.md5(('%s%s%s' % (key, timespan, secret)).encode('utf-8')).hexdigest().upper()
        headers['Token'] = token
        headers['Timespan'] = timespan
    return params, headers


def call(endpoint_code, keyword, operator=None, force_refresh=False):
    """调用指定接口。keyword 建议传统一社会信用代码（更精确），否则传企业全称。"""
    keyword = (keyword or '').strip()
    if not keyword:
        return _result(False, 'skip', '查询关键字为空（需企业名称或统一社会信用代码）')

    cred = get_credential()
    if not cred or not cred.enabled:
        return _result(False, 'manual', '企查查数据源未启用，请在「外部数据源配置」中开启')
    if not cred.get_key():
        return _result(False, 'manual', '未配置企查查 API Key，请人工核验并录入结论')

    ep = ExtEndpoint.query.filter_by(provider=PROVIDER, code=endpoint_code).first()
    if not ep or not ep.enabled:
        return _result(False, 'skip', '接口 %s 未启用或未配置路径' % endpoint_code)
    if not ep.path:
        return _result(False, 'skip', '接口 %s 未配置请求路径' % endpoint_code)

    # 1) 缓存优先
    if not force_refresh:
        cached = _read_cache(endpoint_code, keyword)
        if cached is not None:
            _log(endpoint_code, keyword, True, True, None, 0, '命中缓存', operator)
            return _result(True, 'ok', '命中缓存（未消耗调用次数）', cached, from_cache=True)

    # 2) 配额检查
    if cred.daily_quota and _today_call_count() >= cred.daily_quota:
        return _result(False, 'quota', '今日调用已达上限 %s 次，请明日再试或调整配额'
                       % cred.daily_quota)

    # 3) 真实调用
    base = (cred.base_url or 'https://api.qichacha.com').rstrip('/')
    url = base + '/' + ep.path.lstrip('/')
    params, headers = _build_auth(cred, keyword)
    started = time.time()
    try:
        import requests
        resp = requests.request(ep.method or 'GET', url, params=params, headers=headers,
                                timeout=int(cred.timeout_sec or 10))
        elapsed = int((time.time() - started) * 1000)
        http_status = resp.status_code
        if http_status != 200:
            msg = 'HTTP %s：%s' % (http_status, (resp.text or '')[:200])
            _log(endpoint_code, keyword, False, False, http_status, elapsed, msg, operator)
            return _result(False, 'fail', msg)
        try:
            payload = resp.json()
        except Exception:
            msg = '返回非 JSON：%s' % (resp.text or '')[:200]
            _log(endpoint_code, keyword, False, False, http_status, elapsed, msg, operator)
            return _result(False, 'fail', msg)
    except Exception as e:  # 网络异常 / requests 缺失
        elapsed = int((time.time() - started) * 1000)
        msg = _sanitize_message('调用异常：%s' % str(e)[:200])
        _log(endpoint_code, keyword, False, False, None, elapsed, msg, operator)
        return _result(False, 'fail', msg)

    # 4) 业务状态码（企查查 Status: 200 成功；201 无数据；其余为错误）
    status_code = str(payload.get('Status', payload.get('status', '')))
    message = payload.get('Message', payload.get('message', '')) or ''
    if status_code in ('200', '0'):
        _write_cache(endpoint_code, keyword, payload, cred.cache_days)
        _log(endpoint_code, keyword, True, False, 200, elapsed, message or 'OK', operator)
        return _result(True, 'ok', message or '查询成功', payload, code=status_code)
    if status_code == '201':
        _write_cache(endpoint_code, keyword, payload, cred.cache_days)
        _log(endpoint_code, keyword, True, False, 200, elapsed, '无匹配数据', operator)
        return _result(True, 'empty', '未查询到匹配数据', payload, code=status_code)

    _log(endpoint_code, keyword, False, False, 200, elapsed,
         '业务错误 %s：%s' % (status_code, message), operator)
    return _result(False, 'fail', '企查查返回 %s：%s' % (status_code, message),
                   payload, code=status_code)


def test_connection(operator=None):
    """连通性自检：用一个稳定存在的主体做一次 basic 查询。"""
    cred = get_credential()
    if not cred:
        return False, '未初始化凭据记录'
    if not cred.get_key():
        return False, '未填写 API Key'
    probe = (cred.remark or '').strip() or '中国铁建股份有限公司'
    r = call('basic', probe, operator=operator, force_refresh=True)
    cred.last_test_at = datetime.now()
    cred.last_test_ok = bool(r.ok)
    cred.last_test_msg = r.get('message')
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return bool(r.ok), r.get('message')


# ============================================================ 结果解析
def parse_basic(payload):
    """从工商基本信息返回中提取关键字段（兼容 Result 为 dict / list）。"""
    if not payload:
        return {}
    res = payload.get('Result') or payload.get('result') or {}
    if isinstance(res, list):
        res = res[0] if res else {}
    if not isinstance(res, dict):
        return {}

    def pick(*names):
        for n in names:
            v = res.get(n)
            if v not in (None, '', []):
                return v
        return None

    return {
        'name': pick('Name', 'CompanyName'),
        'credit_code': pick('CreditCode', 'UniformSocialCreditCode'),
        'legal_person': pick('OperName', 'LegalPerson', 'Oper'),
        'status': pick('Status', 'RegStatus'),
        'reg_capital': pick('RegistCapi', 'RegisteredCapital'),
        'start_date': pick('StartDate', 'EstablishDate'),
        'address': pick('Address', 'RegLocation'),
        'scope': pick('Scope', 'BusinessScope'),
        'term_end': pick('TermEnd', 'ToTime'),
        'org_no': pick('OrgNo'),
    }


def parse_list_count(payload):
    """从列表型接口（失信/被执行/异常）提取命中条数。"""
    if not payload:
        return 0
    res = payload.get('Result') or payload.get('result') or []
    if isinstance(res, dict):
        # 有的接口包一层 {Result:{Data:[...]}}
        for k in ('Data', 'data', 'List', 'Items'):
            if isinstance(res.get(k), list):
                return len(res[k])
        paging = payload.get('Paging') or {}
        return int(paging.get('TotalRecords') or 0)
    if isinstance(res, list):
        return len(res)
    return 0
