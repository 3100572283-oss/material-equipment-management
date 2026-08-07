# app/integration/models.py
"""外部对接中心 — 数据模型（纯新增表，不改动任何现有表）。

表清单：
- ext_credential            外部数据源凭据（api_key 加密存储）
- ext_endpoint              接口地址明细（路径可配置，官方文档变更时零改码）
- ext_query_log             调用流水（成功/失败/耗时/计费次数统计）
- ext_query_cache           查询结果缓存（按次计费接口节流）
- export_template           导出模板（铁建云链资料包/台账）
- export_template_field     模板字段映射（列序/目标字段名/取值表达式/必填）
"""
from datetime import datetime, timedelta

from app import db


# 支持的外部数据源
PROVIDERS = {
    'qcc': '企查查开放平台',
    'crccep': '铁建云链（模板化导出）',
}

# 内置可用的取值源（导出模板字段 source_expr 白名单）
# 形如 supplier.name / profile.admit_scope，避免 eval 注入。
FIELD_SOURCES = {
    'supplier.name': '供应商名称',
    'supplier.credit_code': '统一社会信用代码',
    'supplier.legal_person': '法定代表人',
    'supplier.contact_person': '联系人',
    'supplier.phone': '联系电话',
    'supplier.address': '注册地址',
    'supplier.bank_name': '开户银行',
    'supplier.bank_account': '银行账号',
    'supplier.license_expire_date': '营业执照有效期至',
    'profile.admit_scope': '准入范围/工种',
    'profile.valid_from': '准入有效期起',
    'profile.valid_to': '准入有效期止',
    'profile.admit_status_label': '准入结论',
    'profile.blacklist_label': '黑名单核查结论',
    'profile.qcc_status': '第三方工商核验状态',
    'profile.approved_by': '审批人',
    'profile.approved_at': '审批时间',
    'profile.project_name': '所属项目',
    'agg.qualification_types': '资质类型（合并）',
    'agg.qualification_count': '资质数量',
    'agg.safety_cert_types': '安全资格类型（合并）',
    'agg.safety_cert_count': '安全资格数量',
    'agg.performance_names': '业绩工程名称（合并）',
    'agg.performance_count': '业绩数量',
    'agg.blacklist_hit_lists': '命中名录（合并）',
    'agg.earliest_cert_expire': '最早到期证书日期',
    'const.company_name': '本单位名称（常量）',
    'const.today': '导出日期',
}


class ExtCredential(db.Model):
    """外部数据源凭据。api_key/api_secret 以 ENC: 前缀加密存储，页面仅掩码回显。"""
    __tablename__ = 'ext_credential'
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), unique=True, nullable=False, index=True)  # qcc/crccep
    display_name = db.Column(db.String(64), nullable=True)
    base_url = db.Column(db.String(256), nullable=True)
    api_key = db.Column(db.String(512), nullable=True)      # ENC:...
    api_secret = db.Column(db.String(512), nullable=True)   # ENC:...
    enabled = db.Column(db.Boolean, default=False)
    # 鉴权方式：header_sign=企查查V4(Token=MD5(key+timespan+secret) + Timespan 头)
    #           plain_key=key 直接作为查询参数（部分老版本/自建网关）
    auth_style = db.Column(db.String(32), default='header_sign')
    cache_days = db.Column(db.Integer, default=30)          # 结果缓存天数（省调用次数）
    timeout_sec = db.Column(db.Integer, default=10)
    daily_quota = db.Column(db.Integer, nullable=True)      # 每日调用上限（0/None=不限）
    last_test_at = db.Column(db.DateTime, nullable=True)
    last_test_ok = db.Column(db.Boolean, nullable=True)
    last_test_msg = db.Column(db.String(512), nullable=True)
    remark = db.Column(db.String(512), nullable=True)
    updated_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    @property
    def key_masked(self):
        from app.utils import decrypt_secret
        try:
            raw = decrypt_secret(self.api_key or '')
        except Exception:
            return '****（解密失败，请重新填写）'
        if not raw:
            return ''
        if len(raw) <= 8:
            return raw[:2] + '****'
        return raw[:4] + '****' + raw[-4:]

    @property
    def has_key(self):
        return bool(self.api_key)

    def get_key(self):
        from app.utils import decrypt_secret
        try:
            return decrypt_secret(self.api_key or '') or ''
        except Exception:
            return ''

    def get_secret(self):
        from app.utils import decrypt_secret
        try:
            return decrypt_secret(self.api_secret or '') or ''
        except Exception:
            return ''


class ExtEndpoint(db.Model):
    """接口地址明细。官方文档调整路径时改这里即可，无需改代码。"""
    __tablename__ = 'ext_endpoint'
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), nullable=False, index=True)
    code = db.Column(db.String(64), nullable=False)         # basic/dishonest/executed/abnormal
    name = db.Column(db.String(128), nullable=True)
    path = db.Column(db.String(256), nullable=True)         # /ECIV4/GetBasicDetailsByName
    method = db.Column(db.String(8), default='GET')
    param_style = db.Column(db.String(32), default='keyword')  # keyword=按名称/信用代码查询
    enabled = db.Column(db.Boolean, default=True)
    billable = db.Column(db.Boolean, default=True)          # 是否按次计费
    remark = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (db.UniqueConstraint('provider', 'code', name='uq_ext_endpoint_provider_code'),)


class ExtQueryLog(db.Model):
    """外部接口调用流水（用于配额统计与排障）。"""
    __tablename__ = 'ext_query_log'
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), nullable=False, index=True)
    endpoint_code = db.Column(db.String(64), nullable=True)
    query_key = db.Column(db.String(128), nullable=True)    # 查询关键字（名称或信用代码）
    ok = db.Column(db.Boolean, default=False)
    from_cache = db.Column(db.Boolean, default=False)
    http_status = db.Column(db.Integer, nullable=True)
    elapsed_ms = db.Column(db.Integer, nullable=True)
    message = db.Column(db.String(512), nullable=True)
    operator = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)


class ExtQueryCache(db.Model):
    """查询结果缓存。同一主体在 cache_days 内复用，避免重复计费。"""
    __tablename__ = 'ext_query_cache'
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), nullable=False, index=True)
    endpoint_code = db.Column(db.String(64), nullable=False)
    cache_key = db.Column(db.String(128), nullable=False, index=True)  # md5(provider|code|keyword)
    payload = db.Column(db.Text, nullable=True)             # 原始 JSON 文本
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (db.UniqueConstraint('provider', 'endpoint_code', 'cache_key',
                                          name='uq_ext_cache_key'),)

    @property
    def is_valid(self):
        return bool(self.expires_at and self.expires_at > datetime.now())

    @staticmethod
    def build_key(provider, endpoint_code, keyword):
        import hashlib
        raw = '%s|%s|%s' % (provider, endpoint_code, (keyword or '').strip())
        return hashlib.md5(raw.encode('utf-8')).hexdigest()


class ExportTemplate(db.Model):
    """导出模板（铁建云链准入资料包 / 台账导入模板等）。"""
    __tablename__ = 'export_template'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    scene = db.Column(db.String(32), default='subcontractor')  # 适用场景
    target_platform = db.Column(db.String(64), default='铁建云链')
    version = db.Column(db.String(16), default='v1')
    file_format = db.Column(db.String(16), default='xlsx')     # xlsx/docx
    is_active = db.Column(db.Boolean, default=True)
    is_builtin = db.Column(db.Boolean, default=False)          # 内置模板不可删除，仅可改字段
    remark = db.Column(db.String(512), nullable=True)
    created_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    fields = db.relationship('ExportTemplateField', backref='template',
                             cascade='all, delete-orphan',
                             order_by='ExportTemplateField.seq')


class ExportTemplateField(db.Model):
    """模板字段映射：目标平台列名 ← 本系统取值源。"""
    __tablename__ = 'export_template_field'
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('export_template.id'), nullable=False)
    seq = db.Column(db.Integer, default=0)                  # 列顺序
    target_field = db.Column(db.String(128), nullable=False)  # 云链模板中的列名
    source_expr = db.Column(db.String(128), nullable=True)    # FIELD_SOURCES 的键；为空=留空列
    default_value = db.Column(db.String(256), nullable=True)  # 取不到值时的兜底
    required = db.Column(db.Boolean, default=False)           # 导出前必填校验
    remark = db.Column(db.String(256), nullable=True)


# ============================================================ 内置默认模板
# 说明：以下字段依据中国铁建供应商/分包商准入通用要求整理，作为 v1 起始版本。
# 拿到官方最终模板后，在「导出模板管理」页调整列名与顺序即可，无需改代码。
BUILTIN_SUBCONTRACTOR_TEMPLATE = {
    'code': 'crccep_subcontractor_v1',
    'name': '铁建云链-分包商准入资料包',
    'scene': 'subcontractor',
    'target_platform': '铁建云链',
    'version': 'v1',
    'file_format': 'xlsx',
    'is_builtin': True,
    'remark': '按中国铁建分包商准入通用字段整理；拿到官方模板后可在本页调整列名/顺序/必填。',
    'fields': [
        # (seq, target_field, source_expr, required)
        (1, '企业名称', 'supplier.name', True),
        (2, '统一社会信用代码', 'supplier.credit_code', True),
        (3, '法定代表人', 'supplier.legal_person', True),
        (4, '注册地址', 'supplier.address', False),
        (5, '联系人', 'supplier.contact_person', True),
        (6, '联系电话', 'supplier.phone', True),
        (7, '开户银行', 'supplier.bank_name', False),
        (8, '银行账号', 'supplier.bank_account', False),
        (9, '营业执照有效期至', 'supplier.license_expire_date', False),
        (10, '拟准入范围（工种/专业）', 'profile.admit_scope', True),
        (11, '准入有效期起', 'profile.valid_from', False),
        (12, '准入有效期止', 'profile.valid_to', False),
        (13, '资质类型', 'agg.qualification_types', True),
        (14, '资质证书数量', 'agg.qualification_count', False),
        (15, '安全资格类型', 'agg.safety_cert_types', True),
        (16, '安全资格数量', 'agg.safety_cert_count', False),
        (17, '同类工程业绩', 'agg.performance_names', True),
        (18, '业绩数量', 'agg.performance_count', False),
        (19, '证书最早到期日', 'agg.earliest_cert_expire', False),
        (20, '黑名单核查结论', 'profile.blacklist_label', True),
        (21, '命中名录明细', 'agg.blacklist_hit_lists', False),
        (22, '工商信息核验状态', 'profile.qcc_status', False),
        (23, '准入审批结论', 'profile.admit_status_label', True),
        (24, '审批人', 'profile.approved_by', False),
        (25, '审批时间', 'profile.approved_at', False),
        (26, '所属项目', 'profile.project_name', True),
        (27, '报送单位', 'const.company_name', False),
        (28, '报送日期', 'const.today', False),
    ],
}

# 企查查默认接口配置（V4 开放平台常用接口；路径可在后台修改）
BUILTIN_QCC_ENDPOINTS = [
    ('basic', '企业工商基本信息', '/ECIV4/GetBasicDetailsByName', True),
    ('dishonest', '失信被执行人', '/DishonestV4/GetList', True),
    ('executed', '被执行人', '/ExecutedV4/GetList', True),
    ('abnormal', '经营异常', '/AbnormalV4/GetList', True),
    ('tax_illegal', '重大税收违法', '/TaxIllegalV4/GetList', True),
]


def ensure_builtin_integration_data():
    """幂等初始化：内置模板 + 企查查接口目录 + 空凭据占位。"""
    changed = False

    # 1) 凭据占位（enabled=False，不影响任何行为）
    for provider, name in PROVIDERS.items():
        cred = ExtCredential.query.filter_by(provider=provider).first()
        if not cred:
            cred = ExtCredential(
                provider=provider, display_name=name, enabled=False,
                base_url='https://api.qichacha.com' if provider == 'qcc' else None,
                cache_days=30, timeout_sec=10)
            db.session.add(cred)
            changed = True

    # 2) 企查查接口目录
    for code, name, path, billable in BUILTIN_QCC_ENDPOINTS:
        ep = ExtEndpoint.query.filter_by(provider='qcc', code=code).first()
        if not ep:
            db.session.add(ExtEndpoint(provider='qcc', code=code, name=name,
                                       path=path, method='GET', billable=billable,
                                       enabled=(code in ('basic', 'dishonest'))))
            changed = True

    # 3) 内置导出模板
    tpl_def = BUILTIN_SUBCONTRACTOR_TEMPLATE
    tpl = ExportTemplate.query.filter_by(code=tpl_def['code']).first()
    if not tpl:
        tpl = ExportTemplate(
            code=tpl_def['code'], name=tpl_def['name'], scene=tpl_def['scene'],
            target_platform=tpl_def['target_platform'], version=tpl_def['version'],
            file_format=tpl_def['file_format'], is_builtin=True, is_active=True,
            remark=tpl_def['remark'], created_by='system')
        db.session.add(tpl)
        db.session.flush()
        for seq, target, expr, required in tpl_def['fields']:
            db.session.add(ExportTemplateField(
                template_id=tpl.id, seq=seq, target_field=target,
                source_expr=expr, required=required))
        changed = True

    if changed:
        db.session.commit()
    return changed
