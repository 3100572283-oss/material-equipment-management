# app/subcontractor/models.py
"""M6 分包商核验 — 数据模型（V7.3 扩展，纯新增表，不改动现有 suppliers）。

设计：四维度准入（资质/安全/业绩/信誉）+ 7 类黑名单联动 + 企查查快照 + 准入资料包导出。
所有新表通过 subcontractor_profile.supplier_id 关联现有 suppliers 主数据，保证可回滚。
"""
from datetime import datetime, date

from app import db
from app.models import Supplier


# 7 类必须联动的黑名单名录（对标铁建云链准入信誉要求）
BLACKLIST_LISTS = [
    '严重违法失信企业名单',
    '重大税收违法案件当事人名单',
    '拖欠农民工工资黑名单',
    '失信被执行人名单',
    '中国铁建不合格分包商名录',
    '中国铁建合作方风险警示名录',
    '中国铁建行贿人黑名单',
]

# 准入状态
ADMIT_STATUS = {
    'pending': '待审',
    'approved': '通过',
    'rejected': '驳回',
}


class SubcontractorProfile(db.Model):
    """分包商准入档案（关联 suppliers）。"""
    __tablename__ = 'subcontractor_profile'
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    admit_status = db.Column(db.String(16), default='pending')  # pending/approved/rejected
    admit_scope = db.Column(db.String(256), nullable=True)       # 准入范围/工种
    valid_from = db.Column(db.Date, nullable=True)
    valid_to = db.Column(db.Date, nullable=True)
    blacklist_hit = db.Column(db.Boolean, default=False)         # 是否命中黑名单
    qcc_status = db.Column(db.String(16), default='uncheck')     # uncheck/ok/manual/fail
    reject_reason = db.Column(db.String(512), nullable=True)
    approved_by = db.Column(db.String(64), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    supplier = db.relationship('Supplier', backref='subcontractor_profiles')


class SubcontractorQualification(db.Model):
    """资质条件明细（营业执照/准入资格证/专业承包资质）。"""
    __tablename__ = 'subcontractor_qualification'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('subcontractor_profile.id'), nullable=False)
    cert_type = db.Column(db.String(64), nullable=False)   # 资质类型
    cert_no = db.Column(db.String(64), nullable=True)
    issuer = db.Column(db.String(128), nullable=True)      # 发证机关
    valid_to = db.Column(db.Date, nullable=True)
    attachment = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    profile = db.relationship('SubcontractorProfile', backref='qualifications')


class SubcontractorSafetyCert(db.Model):
    """安全资格（安许证/C证/特种作业证）。"""
    __tablename__ = 'subcontractor_safety_cert'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('subcontractor_profile.id'), nullable=False)
    cert_type = db.Column(db.String(64), nullable=False)   # 安许证/C证/特种作业证
    cert_no = db.Column(db.String(64), nullable=True)
    valid_to = db.Column(db.Date, nullable=True)
    attachment = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    profile = db.relationship('SubcontractorProfile', backref='safety_certs')


class SubcontractorPerformance(db.Model):
    """业绩要求（近 3-5 年同类工程）。"""
    __tablename__ = 'subcontractor_performance'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('subcontractor_profile.id'), nullable=False)
    project_name = db.Column(db.String(256), nullable=False)
    scale = db.Column(db.String(256), nullable=True)       # 规模/金额
    period = db.Column(db.String(64), nullable=True)       # 工期/年份
    proof_attachment = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    profile = db.relationship('SubcontractorProfile', backref='performances')


class SubcontractorBlacklist(db.Model):
    """黑名单名录源（7 类，company 级共享池，不参与项目隔离）。

    由用户维护（内置名录导入/人工新增）；run_blacklist_check 据此实时比对。
    """
    __tablename__ = 'subcontractor_blacklist'
    id = db.Column(db.Integer, primary_key=True)
    list_name = db.Column(db.String(64), nullable=False)   # 见 BLACKLIST_LISTS
    entity_name = db.Column(db.String(128), nullable=True)
    credit_code = db.Column(db.String(64), nullable=True)   # 统一社会信用代码
    reason = db.Column(db.String(256), nullable=True)
    source = db.Column(db.String(32), default='manual')     # manual/import
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class SubcontractorBlacklistCheck(db.Model):
    """黑名单核查记录（每个 profile × 7 类名录一行）。"""
    __tablename__ = 'subcontractor_blacklist_check'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('subcontractor_profile.id'), nullable=False)
    list_name = db.Column(db.String(64), nullable=False)
    hit = db.Column(db.Boolean, default=False)
    checked_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(32), default='内置')        # 内置/企查查/人工

    profile = db.relationship('SubcontractorProfile', backref='blacklist_checks')


class SubcontractorQccSnapshot(db.Model):
    """企查查核验快照（Adapter 预留；无 key 时录人工核验结论）。"""
    __tablename__ = 'subcontractor_qcc_snapshot'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('subcontractor_profile.id'), nullable=False)
    raw_json = db.Column(db.Text, nullable=True)            # 企查查原始返回（有 key 时）
    checked_at = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(16), default='manual')     # manual/ok/fail

    profile = db.relationship('SubcontractorProfile', backref='qcc_snapshots')


class SubcontractorAdmitPackage(db.Model):
    """准入资料包导出记录。"""
    __tablename__ = 'subcontractor_admit_package'
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('subcontractor_profile.id'), nullable=False)
    file_name = db.Column(db.String(256), nullable=True)
    exported_at = db.Column(db.DateTime, default=datetime.now)
    template_version = db.Column(db.String(16), default='v1')
    exported_by = db.Column(db.String(64), nullable=True)

    profile = db.relationship('SubcontractorProfile', backref='admit_packages')


def run_blacklist_check(profile):
    """对 profile 关联的 supplier 实时比对 7 类黑名单名录。

    返回是否命中（bool）。每条名录生成/更新一行 subcontractor_blacklist_check。
    """
    supplier = Supplier.query.get(profile.supplier_id)
    if not supplier:
        return False
    hit_any = False
    for lst in BLACKLIST_LISTS:
        exists = None
        if supplier.credit_code or supplier.name:
            q = SubcontractorBlacklist.query.filter_by(list_name=lst, is_active=True)
            conditions = []
            if supplier.name:
                conditions.append(SubcontractorBlacklist.entity_name == supplier.name)
            if supplier.credit_code:
                conditions.append(SubcontractorBlacklist.credit_code == supplier.credit_code)
            if conditions:
                from sqlalchemy import or_
                exists = q.filter(or_(*conditions)).first()
        hit = bool(exists)
        if hit:
            hit_any = True
        chk = SubcontractorBlacklistCheck.query.filter_by(
            profile_id=profile.id, list_name=lst).first()
        if not chk:
            chk = SubcontractorBlacklistCheck(profile_id=profile.id, list_name=lst)
            db.session.add(chk)
        chk.hit = hit
        chk.source = '内置'
        chk.checked_at = datetime.now()
    profile.blacklist_hit = hit_any
    db.session.commit()
    return hit_any


class QccAdapter:
    """企查查核验适配器（预留接口）。

    - 配置了 QCC_API_KEY 时，verify() 调企查查工商/司法/失信接口（此处为预留骨架，
      实际 URL/签名按官方文档补全，不阻断主流程）。
    - 未配置时 verify() 返回 configured=False，由人工在页面录入核验结论。
    """

    def __init__(self, api_key=None):
        self.api_key = api_key

    def verify(self, credit_code=None, name=None):
        if not self.api_key:
            return {
                'configured': False,
                'status': 'manual',
                'message': '未配置企查查 API Key，请人工核验并录入结论',
            }
        # 预留：实际调用企查查接口
        # import requests
        # resp = requests.get(url, params={...}, headers={'Token': self.api_key}, timeout=10)
        # return {'configured': True, 'status': 'ok', 'raw': resp.json()}
        return {
            'configured': True,
            'status': 'unimplemented',
            'message': '企查查实时接口待接入（Adapter 已预留）',
        }
