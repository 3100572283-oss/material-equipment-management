# app/integration/services.py
"""外部对接中心 — 业务编排。

1) verify_subcontractor：企查查核验编排（工商基本信息 + 失信/被执行/异常/税收违法）
   - 自动回填 subcontractor_qcc_snapshot
   - 自动回填 subcontractor_blacklist_check（source='企查查'）
   - 与本地 suppliers 主数据做差异比对，输出待修正清单
   - 未配置 key / 调用失败时，全部降级为 manual，绝不阻断主流程
2) 模板化导出：按 export_template 字段映射生成 xlsx（多主体台账 / 单主体资料包）
"""
import json
from datetime import datetime, date

from app import db
from app.integration.models import (ExportTemplate, ExportTemplateField, FIELD_SOURCES)
from app.integration import qcc_client


# ============================================================ 企查查核验编排
# 企查查接口 code → 对应的黑名单名录名（用于自动回填核查记录）
QCC_LIST_MAPPING = {
    'dishonest': '失信被执行人名单',
    'tax_illegal': '重大税收违法案件当事人名单',
    'abnormal': '严重违法失信企业名单',
}


def verify_subcontractor(profile, operator=None, force_refresh=False):
    """对单个准入档案执行企查查核验。

    返回 dict：
      {configured, status, message, basic, diffs[], list_hits{}, snapshot_id}
    status: ok / empty / manual / fail
    """
    from app.subcontractor.models import (SubcontractorQccSnapshot,
                                          SubcontractorBlacklistCheck)

    supplier = profile.supplier
    if not supplier:
        return {'configured': False, 'status': 'manual', 'message': '档案未关联供应商',
                'basic': {}, 'diffs': [], 'list_hits': {}}

    keyword = (supplier.credit_code or '').strip() or (supplier.name or '').strip()
    if not keyword:
        return {'configured': False, 'status': 'manual',
                'message': '供应商缺少名称与统一社会信用代码，无法发起工商核验',
                'basic': {}, 'diffs': [], 'list_hits': {}}

    if not qcc_client.is_configured():
        return {'configured': False, 'status': 'manual',
                'message': '未启用/未配置企查查 API Key，请人工核验并录入结论',
                'basic': {}, 'diffs': [], 'list_hits': {}}

    # 1) 工商基本信息
    r_basic = qcc_client.call('basic', keyword, operator=operator, force_refresh=force_refresh)
    basic = qcc_client.parse_basic(r_basic.get('data')) if r_basic.ok else {}
    status = r_basic.get('status') if r_basic.ok else 'fail'
    message = r_basic.get('message')

    # 2) 与本地主数据比对
    diffs = _diff_supplier(supplier, basic) if basic else []

    # 3) 名录类接口 → 回填黑名单核查
    list_hits = {}
    for ep_code, list_name in QCC_LIST_MAPPING.items():
        r = qcc_client.call(ep_code, keyword, operator=operator, force_refresh=force_refresh)
        if not r.ok:
            continue
        cnt = qcc_client.parse_list_count(r.get('data'))
        list_hits[list_name] = cnt
        chk = SubcontractorBlacklistCheck.query.filter_by(
            profile_id=profile.id, list_name=list_name).first()
        if not chk:
            chk = SubcontractorBlacklistCheck(profile_id=profile.id, list_name=list_name)
            db.session.add(chk)
        # 企查查命中优先级高于内置名录（内置未命中但外部命中 → 判命中）
        if cnt > 0:
            chk.hit = True
        chk.source = '企查查'
        chk.checked_at = datetime.now()

    # 4) 快照落库
    snap = SubcontractorQccSnapshot(profile_id=profile.id, status=status)
    snap.raw_json = json.dumps({
        'keyword': keyword,
        'from_cache': bool(r_basic.get('from_cache')),
        'basic': basic,
        'list_hits': list_hits,
        'diffs': diffs,
        'message': message,
    }, ensure_ascii=False, default=str)
    db.session.add(snap)

    profile.qcc_status = status
    if any(v > 0 for v in list_hits.values()):
        profile.blacklist_hit = True
    db.session.commit()

    return {'configured': True, 'status': status, 'message': message,
            'basic': basic, 'diffs': diffs, 'list_hits': list_hits,
            'snapshot_id': snap.id, 'from_cache': bool(r_basic.get('from_cache'))}


def _norm(v):
    if v is None:
        return ''
    if isinstance(v, (date, datetime)):
        return v.strftime('%Y-%m-%d')
    return str(v).strip()


def _diff_supplier(supplier, basic):
    """本地主数据 vs 工商登记信息差异清单。"""
    pairs = [
        ('企业名称', _norm(supplier.name), _norm(basic.get('name'))),
        ('统一社会信用代码', _norm(supplier.credit_code), _norm(basic.get('credit_code'))),
        ('法定代表人', _norm(supplier.legal_person), _norm(basic.get('legal_person'))),
        ('注册地址', _norm(supplier.address), _norm(basic.get('address'))),
    ]
    diffs = []
    for label, local, remote in pairs:
        if not remote:
            continue
        if local and local != remote:
            diffs.append({'field': label, 'local': local, 'remote': remote,
                          'level': 'warn'})
        elif not local:
            diffs.append({'field': label, 'local': '（空）', 'remote': remote,
                          'level': 'fill'})
    reg_status = _norm(basic.get('status'))
    if reg_status and reg_status not in ('存续', '在业', '存续（在营、开业、在册）'):
        diffs.append({'field': '登记状态', 'local': '-', 'remote': reg_status,
                      'level': 'danger'})
    return diffs


# ============================================================ 模板化导出
def resolve_field(profile, source_expr, default_value=None):
    """按白名单表达式取值。source_expr 必须命中 FIELD_SOURCES，杜绝任意属性访问。"""
    if not source_expr:
        return default_value or ''
    if source_expr not in FIELD_SOURCES:
        return default_value or ''

    supplier = profile.supplier
    ns, _, attr = source_expr.partition('.')

    val = None
    if ns == 'supplier' and supplier is not None:
        val = getattr(supplier, attr, None)
    elif ns == 'profile':
        if attr == 'admit_status_label':
            from app.subcontractor.models import ADMIT_STATUS
            val = ADMIT_STATUS.get(profile.admit_status, profile.admit_status)
        elif attr == 'blacklist_label':
            val = '命中' if profile.blacklist_hit else '未命中'
        elif attr == 'project_name':
            from app.models import Project
            p = Project.query.get(profile.project_id) if profile.project_id else None
            val = p.name if p else None
        else:
            val = getattr(profile, attr, None)
    elif ns == 'agg':
        val = _resolve_agg(profile, attr)
    elif ns == 'const':
        if attr == 'company_name':
            from app.utils import get_config
            val = get_config('company_name', '中铁二十一局集团第六工程有限公司')
        elif attr == 'today':
            val = date.today()

    s = _norm(val)
    return s if s else (default_value or '')


def _resolve_agg(profile, attr):
    quals = list(profile.qualifications or [])
    safes = list(profile.safety_certs or [])
    perfs = list(profile.performances or [])
    if attr == 'qualification_types':
        return '、'.join([q.cert_type for q in quals if q.cert_type])
    if attr == 'qualification_count':
        return len(quals)
    if attr == 'safety_cert_types':
        return '、'.join([s.cert_type for s in safes if s.cert_type])
    if attr == 'safety_cert_count':
        return len(safes)
    if attr == 'performance_names':
        return '；'.join([p.project_name for p in perfs if p.project_name])
    if attr == 'performance_count':
        return len(perfs)
    if attr == 'blacklist_hit_lists':
        hits = [c.list_name for c in (profile.blacklist_checks or []) if c.hit]
        return '、'.join(hits)
    if attr == 'earliest_cert_expire':
        dates = [q.valid_to for q in quals if q.valid_to] + \
                [s.valid_to for s in safes if s.valid_to]
        return min(dates) if dates else None
    return None


def validate_template(profile, template):
    """导出前必填校验，返回缺失字段名列表。"""
    missing = []
    for f in template.fields:
        if not f.required:
            continue
        if not resolve_field(profile, f.source_expr, f.default_value):
            missing.append(f.target_field)
    return missing


def export_xlsx(profiles, template):
    """按模板生成 xlsx（第一行=目标平台列名，后续每行一个主体）。返回 BytesIO。"""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = (template.name or '导出')[:28]

    fields = sorted(template.fields, key=lambda x: (x.seq or 0, x.id))
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='2F5597')
    for idx, f in enumerate(fields, start=1):
        c = ws.cell(row=1, column=idx, value=f.target_field)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        width = max(12, min(32, len(f.target_field or '') * 2 + 6))
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = 'A2'

    for r, profile in enumerate(profiles, start=2):
        for idx, f in enumerate(fields, start=1):
            ws.cell(row=r, column=idx,
                    value=resolve_field(profile, f.source_expr, f.default_value))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def get_active_template(code=None, scene='subcontractor'):
    q = ExportTemplate.query.filter_by(is_active=True)
    if code:
        t = q.filter_by(code=code).first()
        if t:
            return t
    return q.filter_by(scene=scene).order_by(ExportTemplate.id).first()
