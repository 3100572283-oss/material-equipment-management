import csv
import io
import threading
from decimal import Decimal
from datetime import datetime


# 单号生成进程内锁,避免同进程多线程并发产生重复单号
_code_gen_lock = threading.Lock()
# 单号最大重试次数,避免极端高并发下死循环
_CODE_GEN_MAX_RETRY = 10


def _gen_code_with_seq(prefix, project_id, model_cls, code_field='code'):
    """通用单据编号生成：前缀 + 项目ID + 年月日 + 3位序号

    并发安全策略:
    1. 进程内 threading.Lock 串行化单号生成,避免同进程多线程并发取到相同序号
    2. 使用 func.max() 而非 count(),避免历史脏数据(已删除单据)导致跳号或重复
    3. 生成后主动验证不存在,若冲突则递增重试(最多 10 次)
    4. 超过最大重试次数抛出异常,避免极端情况下死循环
    """
    from app import db
    from sqlalchemy import func

    date_str = datetime.now().strftime('%Y%m%d')
    base_prefix = f"{prefix}{project_id}{date_str}"

    with _code_gen_lock:
        # 查询当天最大的序号
        max_code = db.session.query(func.max(getattr(model_cls, code_field))).filter(
            getattr(model_cls, code_field).like(f"{base_prefix}%")
        ).scalar()

        if max_code:
            try:
                seq = int(max_code[-3:]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1

        # 重试循环: 验证生成的单号不存在,冲突则递增
        for attempt in range(_CODE_GEN_MAX_RETRY):
            candidate = f"{base_prefix}{seq:03d}"
            exists = db.session.query(getattr(model_cls, code_field)).filter(
                getattr(model_cls, code_field) == candidate
            ).first()
            if not exists:
                return candidate
            seq += 1

        # 超过最大重试次数,使用 4 位序号兜底
        candidate = f"{base_prefix}{seq:04d}"
        return candidate


def gen_contract_code(project_id):
    """生成合同编号: HT{项目ID}{年月日}{3位序号}"""
    from app.models import Contract
    return _gen_code_with_seq('HT', project_id, Contract)


def gen_payment_code(project_id):
    """生成付款单号: FK{项目ID}{年月日}{3位序号}"""
    from app.models import Payment
    return _gen_code_with_seq('FK', project_id, Payment)


def gen_stock_in_code(project_id):
    """生成入库单号: RK{项目ID}{年月日}{3位序号}"""
    from app.models import StockIn
    return _gen_code_with_seq('RK', project_id, StockIn)


def gen_stock_out_code(project_id):
    """生成出库单号: CK{项目ID}{年月日}{3位序号}"""
    from app.models import StockOut
    return _gen_code_with_seq('CK', project_id, StockOut)


def gen_reconciliation_code(project_id):
    """生成对账单号: DZ{项目ID}{年月日}{3位序号}"""
    from app.models import Reconciliation
    return _gen_code_with_seq('DZ', project_id, Reconciliation)


def to_decimal(value, default=0):
    try:
        if value is None or value == '':
            return Decimal(str(default))
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def calc_without_tax(amount_with_tax, tax_rate):
    """根据含税金额和税率计算不含税金额"""
    try:
        awt = Decimal(str(amount_with_tax or 0))
        rate = Decimal(str(tax_rate or 0)) / Decimal('100')
        if rate > 0:
            return float(round(awt / (Decimal('1') + rate), 2))
    except Exception:
        pass
    return float(amount_with_tax or 0)


def calc_unit_price_without_tax(unit_price_with_tax, tax_rate):
    """根据含税单价和税率计算不含税单价"""
    try:
        upwt = Decimal(str(unit_price_with_tax or 0))
        rate = Decimal(str(tax_rate or 0)) / Decimal('100')
        if rate > 0:
            return float(round(upwt / (Decimal('1') + rate), 4))
    except Exception:
        pass
    return float(unit_price_with_tax or 0)


def get_contract_stats(contract):
    """计算合同统计字段：已发生金额、开票金额、支付金额、欠款金额、入库完成率
    金额按含税口径为主，同时返回不含税口径用于双口径展示。
    已发生金额：按含税口径计算（实际入库结算额含税），优先取 StockIn.actual_amount，
    若为空则回退到 total_amount。"""
    from app.models import (Invoice, Payment, StockIn, ContractItem,
                            ReconciliationItem, Reconciliation, db)
    from sqlalchemy import func

    invoice_total = db.session.query(func.coalesce(func.sum(Invoice.amount_with_tax), 0)).filter(
        Invoice.contract_id == contract.id).scalar() or 0
    invoice_total_without_tax = db.session.query(func.coalesce(func.sum(Invoice.amount_without_tax), 0)).filter(
        Invoice.contract_id == contract.id).scalar() or 0
    payment_total = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.contract_id == contract.id).scalar() or 0

    # 已发生金额（含税）：已对账入库的实际结算额，优先 actual_amount，回退 total_amount
    occurred_rows = db.session.query(
        StockIn.actual_amount, StockIn.total_amount
    ).filter(
        StockIn.contract_id == contract.id, StockIn.is_reconciled == True
    ).all()
    occurred_total = 0.0
    for actual_amt, total_amt in occurred_rows:
        val = float(actual_amt or 0)
        if val == 0:
            val = float(total_amt or 0)
        occurred_total += val

    # 已发生金额（不含税）：取已确认对账单明细的 amount_without_tax 汇总
    occurred_total_without_tax = db.session.query(
        func.coalesce(func.sum(ReconciliationItem.amount_without_tax), 0)
    ).join(
        Reconciliation, ReconciliationItem.reconciliation_id == Reconciliation.id
    ).filter(
        Reconciliation.contract_id == contract.id,
        Reconciliation.status == '已确认'
    ).scalar() or 0

    items = ContractItem.query.filter_by(contract_id=contract.id).all()
    total_contract_qty = 0
    total_in_qty = 0
    for it in items:
        qty = float(it.quantity or 0)
        in_qty = float(it.total_in_qty or 0)
        if qty > 0:
            total_contract_qty += qty
            total_in_qty += min(in_qty, qty) if in_qty > qty else in_qty

    inbound_rate = 0
    if total_contract_qty > 0:
        inbound_rate = round(total_in_qty / total_contract_qty * 100, 2)

    return {
        'invoice_total': float(invoice_total),
        'invoice_total_without_tax': float(invoice_total_without_tax),
        'payment_total': float(payment_total),
        'occurred_total': float(occurred_total),
        'occurred_total_without_tax': float(occurred_total_without_tax),
        'owing_total': float(Decimal(str(invoice_total)) - Decimal(str(payment_total))),
        'inbound_rate': inbound_rate,
        'total_contract_qty': total_contract_qty,
        'total_in_qty': total_in_qty
    }


# ============== 统一导出工具 ==============

def export_to_csv(headers, rows, filename):
    """导出CSV文件"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    output.seek(0)
    return output.getvalue().encode('utf-8-sig')


def export_to_excel(headers, rows, sheet_title, filename):
    """导出Excel文件，使用 openpyxl"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        # 如果没有 openpyxl，回退到 CSV
        return export_to_csv(headers, rows, filename)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title

    # 标题行
    title_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    title_font = Font(bold=True, color='FFFFFF')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    # 写入导出信息
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    ws.cell(row=1, column=1, value=f'{sheet_title} - 导出时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    ws.cell(row=1, column=1).font = Font(bold=True, size=14)
    ws.cell(row=1, column=1).alignment = Alignment(horizontal='center')

    # 写入表头
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col, value=header)
        cell.font = title_font
        cell.fill = title_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border

    # 写入数据
    for row_idx, row in enumerate(rows, 3):
        for col_idx, value in enumerate(row, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal='right' if isinstance(value, (int, float, Decimal)) else 'left')

    # 自适应列宽
    for col_idx, header in enumerate(headers, 1):
        max_length = len(str(header))
        for row in rows:
            cell_len = len(str(row[col_idx - 1])) if col_idx <= len(row) else 0
            max_length = max(max_length, cell_len)
        ws.column_dimensions[ws.cell(row=2, column=col_idx).column_letter].width = min(max_length + 4, 40)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


# ============== 操作日志 ==============

def log_operation(action, module=None, description=None):
    """记录操作日志"""
    from flask_login import current_user
    from flask import request
    from app import db
    from app.models import OperationLog

    try:
        log = OperationLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            username=current_user.username if current_user.is_authenticated else None,
            action=action,
            module=module,
            description=description,
            ip_address=request.remote_addr if request else None
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


# ============== 审计日志（增强版） ==============

def log_audit(module, operation, biz_type=None, biz_id=None, params=None, status='success', error_msg=None, changes=None, cost_time=0):
    """记录审计日志"""
    from flask_login import current_user
    from flask import request
    from app import db
    from app.models import SysOperationLog
    import json as _json

    try:
        log = SysOperationLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            username=current_user.username if current_user.is_authenticated else None,
            module=module,
            operation=operation,
            biz_type=biz_type,
            biz_id=biz_id,
            method=request.method if request else None,
            params=_json.dumps(params, ensure_ascii=False, default=str) if params else None,
            ip_address=request.remote_addr if request else None,
            user_agent=request.headers.get('User-Agent', '')[:256] if request else None,
            cost_time=cost_time,
            status=status,
            error_msg=error_msg,
            changes=_json.dumps(changes, ensure_ascii=False, default=str) if changes else None
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def record_changes(table_name, record_id, old_data, new_data, module=None, operation='编辑', changed_by=None, changed_by_id=None, reason=None, exclude_fields=None):
    """记录数据变更明细"""
    from flask import request
    from app import db
    from app.models import DataChangeLog

    if not old_data or not new_data:
        return
    if exclude_fields is None:
        exclude_fields = {'id', 'created_at', 'updated_at', 'password_hash'}
    changes = []
    for key in new_data:
        if key in exclude_fields:
            continue
        old_val = old_data.get(key)
        new_val = new_data.get(key)
        if str(old_val) != str(new_val):
            changes.append({
                'field_name': key,
                'field_label': get_field_label(key),
                'old_value': str(old_val) if old_val is not None else '',
                'new_value': str(new_val) if new_val is not None else ''
            })
    if not changes:
        return
    ip = request.remote_addr if request else None
    for ch in changes:
        log = DataChangeLog(
            module=module,
            operation=operation,
            table_name=table_name,
            record_id=record_id,
            field_name=ch['field_name'],
            field_label=ch['field_label'],
            old_value=ch['old_value'],
            new_value=ch['new_value'],
            changed_by=changed_by,
            changed_by_id=changed_by_id,
            ip_address=ip,
            reason=reason
        )
        db.session.add(log)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def get_change_logs(table_name, record_id, group_by_time=True):
    """获取数据变更记录（按时间倒序）"""
    from app.models import DataChangeLog
    logs = DataChangeLog.query.filter_by(
        table_name=table_name, record_id=record_id
    ).order_by(DataChangeLog.changed_at.desc(), DataChangeLog.id.desc()).all()
    if not group_by_time:
        return logs
    groups = {}
    for log in logs:
        key = log.changed_at.strftime('%Y-%m-%d %H:%M:%S') + '_' + str(log.changed_by or '')
        if key not in groups:
            groups[key] = {
                'changed_at': log.changed_at,
                'changed_by': log.changed_by,
                'operation': log.operation,
                'module': log.module,
                'reason': log.reason,
                'ip_address': log.ip_address,
                'changes': []
            }
        groups[key]['changes'].append(log)
    return list(groups.values())


def model_to_dict(instance):
    """将SQLAlchemy模型对象转为字典（用于对比）"""
    if not instance:
        return {}
    result = {}
    for col in instance.__table__.columns:
        val = getattr(instance, col.name)
        result[col.name] = val
    return result


FIELD_LABELS = {
    'code': '编号',
    'name': '名称',
    'supplier_id': '供应商',
    'contract_type': '合同类型',
    'business_type': '业务类型',
    'procurement_method': '采购方式',
    'sign_date': '签订日期',
    'tax_rate': '税率',
    'amount_with_tax': '含税金额',
    'amount_without_tax': '不含税金额',
    'status': '状态',
    'is_final_settled': '是否最终结算',
    'is_litigated': '是否涉诉',
    'remark': '备注',
    'attachment': '附件',
    'stock_in_date': '入库日期',
    'stock_in_type': '入库类型',
    'warehouse_id': '仓库',
    'quality_status': '质检状态',
    'quality_remark': '质检备注',
    'apply_amount': '申请金额',
    'payment_method': '付款方式',
    'expected_payment_date': '预计付款日期',
    'payment_description': '付款说明',
}


def get_field_label(field_name):
    """获取字段中文标签"""
    return FIELD_LABELS.get(field_name, field_name)


# ============== 数据权限过滤 ==============

def apply_data_scope(query, model_cls, user=None):
    """对查询追加数据权限过滤"""
    from flask_login import current_user
    from app.models import SysRole, SysDept, SysRoleDept, User
    from flask import session
    import sqlalchemy as sa

    if user is None:
        user = current_user
    if not user.is_authenticated:
        return query.filter(db.false())

    role = user.role_obj
    if role and role.role_code == 'super_admin':
        return query

    data_scope = role.data_scope if role else user.data_scope or 'all'

    if data_scope == 'all':
        return query

    project_id = session.get('current_project_id')
    if project_id:
        query = query.filter(model_cls.project_id == project_id)

    if data_scope == 'self':
        if hasattr(model_cls, 'created_by'):
            query = query.filter(model_cls.created_by == user.id)
        elif hasattr(model_cls, 'applicant_id'):
            query = query.filter(model_cls.applicant_id == user.id)
        elif hasattr(model_cls, 'user_id'):
            query = query.filter(model_cls.user_id == user.id)
    elif data_scope == 'dept':
        if user.dept_id and hasattr(model_cls, 'dept_id'):
            query = query.filter(model_cls.dept_id == user.dept_id)
        elif user.dept_id and hasattr(model_cls, 'created_by'):
            sub_query = db.session.query(SysDept).filter(
                SysDept.id == user.dept_id
            ).subquery()
            query = query.join(
                User, model_cls.created_by == User.id
            ).filter(User.dept_id == user.dept_id)
    elif data_scope == 'dept_and_sub':
        if user.dept_id and hasattr(model_cls, 'dept_id'):
            dept_ids = get_sub_dept_ids(user.dept_id)
            dept_ids.append(user.dept_id)
            query = query.filter(model_cls.dept_id.in_(dept_ids))
        elif user.dept_id and hasattr(model_cls, 'created_by'):
            dept_ids = get_sub_dept_ids(user.dept_id)
            dept_ids.append(user.dept_id)
            query = query.join(
                User, model_cls.created_by == User.id
            ).filter(User.dept_id.in_(dept_ids))
    elif data_scope == 'custom':
        if role:
            dept_ids = [rd.dept_id for rd in SysRoleDept.query.filter_by(role_id=role.id).all()]
            if dept_ids:
                if hasattr(model_cls, 'dept_id'):
                    query = query.filter(model_cls.dept_id.in_(dept_ids))
                elif hasattr(model_cls, 'created_by'):
                    query = query.join(
                        User, model_cls.created_by == User.id
                    ).filter(User.dept_id.in_(dept_ids))

    return query


def get_sub_dept_ids(dept_id):
    """获取指定部门及其所有子部门ID"""
    from app.models import SysDept
    dept_ids = []
    stack = [dept_id]
    while stack:
        did = stack.pop()
        children = SysDept.query.filter(SysDept.parent_id == did).all()
        for child in children:
            dept_ids.append(child.id)
            stack.append(child.id)
    return dept_ids


# ============== 系统配置 ==============

class ConfigCache:
    """系统配置缓存(带 TTL,解决多进程部署下缓存不一致问题)"""
    # 缓存结构: {key: (value, expire_at)}
    _cache = {}
    _ttl = 30  # 缓存有效期(秒),容忍短时不一致

    @classmethod
    def get(cls, key, default=None):
        import time as _time
        now = _time.time()
        cached = cls._cache.get(key)
        if cached and cached[1] > now:
            return cached[0]
        # 未命中或已过期,从 DB 加载
        from app.models import SystemConfig
        config = SystemConfig.query.filter_by(config_key=key).first()
        value = config.config_value if config else default
        cls._cache[key] = (value, now + cls._ttl)
        return value

    @classmethod
    def set(cls, key, value):
        import time as _time
        cls._cache[key] = (value, _time.time() + cls._ttl)

    @classmethod
    def clear(cls):
        cls._cache.clear()

    @classmethod
    def reload(cls):
        """从数据库重新加载所有配置"""
        cls.clear()
        # 预热缓存
        from app.models import SystemConfig
        import time as _time
        now = _time.time()
        configs = SystemConfig.query.all()
        for c in configs:
            cls._cache[c.config_key] = (c.config_value, now + cls._ttl)


def get_config(key, default=None):
    """获取系统配置值"""
    return ConfigCache.get(key, default)


def init_system_config():
    """初始化默认系统配置"""
    from app import db
    from app.models import SystemConfig

    defaults = [
        ('allow_negative_stock', 'false', '是否允许负库存（true/false）'),
        ('decimal_places', '2', '金额小数位数'),
        ('auto_generate_code', 'true', '是否自动生成单据编号（true/false）'),
        ('stock_warning_threshold', '0', '库存预警阈值（低于此值时预警）'),
        ('evaluation_weights', '{"quality":40,"price":20,"delivery":25,"service":15}', '供应商评价维度权重配置'),
        ('evaluation_thresholds', '{"A":90,"B":80,"C":60}', '供应商评级分数线配置'),
        # 消息通知配置
        ('notify_dingtalk_enabled', 'false', '钉钉通知开关'),
        ('notify_dingtalk_webhook', '', '钉钉Webhook地址'),
        ('notify_dingtalk_secret', '', '钉钉加签密钥'),
        ('notify_wechat_enabled', 'false', '企业微信通知开关'),
        ('notify_wechat_webhook', '', '企业微信Webhook地址'),
        ('notify_feishu_enabled', 'false', '飞书通知开关'),
        ('notify_feishu_webhook', '', '飞书Webhook地址'),
        ('notify_email_enabled', 'false', '邮件通知开关'),
        ('notify_email_smtp_host', '', 'SMTP服务器'),
        ('notify_email_smtp_port', '465', 'SMTP端口'),
        ('notify_email_account', '', '邮箱账号'),
        ('notify_email_password', '', '邮箱密码'),
        ('notify_email_recipients', '', '默认收件人'),
        ('notify_title_prefix', '[物资系统]', '消息标题前缀'),
        # 审计日志保留天数
        ('audit_log_retention_days', '365', '审计日志保留天数'),
        # 回收站保留天数
        ('recycle_bin_retention_days', '30', '回收站保留天数'),
        # 模块开关
        ('module_turnover_enabled', 'true', '是否启用周转材管理模块（true/false）'),
        ('module_equipment_enabled', 'true', '是否启用设备管理模块（true/false）'),
        # AI助手配置
        ('ai_enabled', 'false', '是否启用AI助手（true/false）'),
        ('ai_api_key', '', 'AI助手API Key（加密存储）'),
        ('ai_model', 'doubao-pro-32k', 'AI助手模型名称'),
        ('ai_vision_model', 'doubao-vision-pro-32k', 'AI视觉识别模型名称'),
        ('ai_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions', 'AI助手API地址'),
        ('ai_max_tokens', '2000', 'AI助手最大输出token数'),
        # 期初库存
        ('allow_initial_stock', 'false', '是否允许期初库存录入（true/false）'),
        # 入库质检
        ('enable_quality_check', 'false', '是否启用入库质检流程（true/false）'),
        # 限额领料配置
        ('enable_quota_control', 'false', '是否启用限额领料控制（true/false）'),
        ('quota_warning_ratio', '80', '限额预警比例（%），达到此比例黄色预警'),
        ('quota_force_block', 'false', '超限额是否强制拦截（true/false，否则仅提示）'),
        # 单价异常校验
        ('enable_price_check', 'true', '是否启用入库单价异常校验（true/false）'),
        ('price_deviation_threshold', '20', '单价异常偏差阈值（%），超过此值预警'),
        ('price_check_force_block', 'false', '单价异常是否强制拦截（true/false，否则仅警告）'),
    ]

    for key, value, desc in defaults:
        if not SystemConfig.query.filter_by(config_key=key).first():
            db.session.add(SystemConfig(
                config_key=key,
                config_value=value,
                description=desc
            ))
    db.session.commit()
    ConfigCache.reload()


# ============== 统一附件上传服务 ==============

ALLOWED_ATTACHMENT_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'pdf', 'doc', 'docx', 'xls', 'xlsx'}
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10MB


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_ATTACHMENT_EXTENSIONS


def upload_attachment(file_storage, module, biz_id=None, project_id=None):
    """统一附件上传服务
    返回 Attachment 对象，失败返回 None，错误信息在第二个返回值
    """
    import os
    import uuid
    from flask import current_app, request
    from flask_login import current_user
    from app import db
    from app.models import Attachment

    if not file_storage or not file_storage.filename:
        return None, '请选择文件'

    filename = file_storage.filename
    if not allowed_file(filename):
        return None, f'不支持的文件类型，仅支持：{", ".join(sorted(ALLOWED_ATTACHMENT_EXTENSIONS))}'

    ext = filename.rsplit('.', 1)[1].lower()
    new_filename = f'{uuid.uuid4().hex}.{ext}'
    sub_dir = module
    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], sub_dir)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, new_filename)
    file_storage.save(file_path)

    file_size = os.path.getsize(file_path)
    if file_size > MAX_ATTACHMENT_SIZE:
        os.remove(file_path)
        return None, f'文件大小不能超过 {MAX_ATTACHMENT_SIZE // (1024*1024)}MB'

    import mimetypes
    mime_type, _ = mimetypes.guess_type(filename)

    att = Attachment(
        project_id=project_id,
        module=module,
        biz_id=biz_id,
        original_name=filename,
        file_name=new_filename,
        file_path=os.path.join('uploads', sub_dir, new_filename),
        file_size=file_size,
        file_type=ext,
        mime_type=mime_type,
        uploaded_by=current_user.name or current_user.username if current_user.is_authenticated else None,
        uploaded_by_id=current_user.id if current_user.is_authenticated else None,
    )
    db.session.add(att)
    db.session.commit()
    return att, None


def get_attachment_url(att_id):
    """获取附件访问URL（带鉴权）"""
    from flask import url_for
    return url_for('admin.download_attachment', id=att_id)


def format_file_size(size_bytes):
    """格式化文件大小"""
    if size_bytes < 1024:
        return f'{size_bytes} B'
    elif size_bytes < 1024 * 1024:
        return f'{size_bytes / 1024:.1f} KB'
    elif size_bytes < 1024 * 1024 * 1024:
        return f'{size_bytes / (1024 * 1024):.1f} MB'
    else:
        return f'{size_bytes / (1024 * 1024 * 1024):.2f} GB'


# ============== 字典管理 ==============

import time as _time
import threading as _threading

# 字典缓存: {dict_type: (items, expire_at)}
_dict_cache = {}
_dict_cache_ttl = 60  # 缓存有效期(秒)
_dict_cache_lock = _threading.Lock()


def get_dict_items(dict_type):
    """获取指定字典类型的启用途项列表，返回 [(label, value), ...]"""
    now = _time.time()
    # 双重检查锁定,提升并发性能
    cached = _dict_cache.get(dict_type)
    if cached and cached[1] > now:
        return cached[0]

    with _dict_cache_lock:
        cached = _dict_cache.get(dict_type)
        if cached and cached[1] > now:
            return cached[0]
        from app.models import SysDictType, SysDictItem
        dt = SysDictType.query.filter_by(dict_type=dict_type, is_active=True).first()
        items = []
        if dt:
            for item in SysDictItem.query.filter_by(dict_type_id=dt.id, is_active=True).order_by(SysDictItem.sort_order).all():
                items.append((item.item_label, item.item_value))
        _dict_cache[dict_type] = (items, now + _dict_cache_ttl)
        return items


def clear_dict_cache():
    """清除字典缓存(用于字典修改后主动刷新)"""
    with _dict_cache_lock:
        _dict_cache.clear()


def init_dict_data():
    """初始化预置字典数据"""
    from app import db
    from app.models import SysDictType, SysDictItem

    presets = {
        # ---- 基础数据类 ----
        'equipment_category': ('设备分类', [
            ('起重设备', '起重设备'), ('运输设备', '运输设备'), ('加工设备', '加工设备'),
            ('测量设备', '测量设备'), ('焊接设备', '焊接设备'), ('其他', '其他')
        ]),
        'equipment_source': ('设备来源', [
            ('自有设备', 'self'), ('租赁设备', 'rent'), ('劳务队自带', 'labor')
        ]),
        'equipment_status': ('设备状态', [
            ('在用', 'in_use'), ('闲置', 'idle'), ('维修中', 'repairing'),
            ('已退场', 'exited'), ('已报废', 'scrapped')
        ]),
        'rent_type': ('租赁方式', [
            ('台班', 'shift'), ('月租', 'monthly'), ('年租', 'yearly')
        ]),
        'material_unit': ('物资单位', [
            ('吨', '吨'), ('kg', 'kg'), ('m', 'm'), ('m²', 'm²'), ('m³', 'm³'),
            ('个', '个'), ('套', '套'), ('台', '台'), ('辆', '辆'), ('项', '项')
        ]),
        'tax_rate': ('税率选项', [
            ('13%', '13'), ('9%', '9'), ('6%', '6'), ('3%', '3'), ('1%', '1')
        ]),
        # ---- 合同管理类 ----
        'contract_type': ('合同类型', [
            ('采购合同', '采购合同'), ('租赁合同', '租赁合同'), ('劳务合同', '劳务合同'),
            ('服务合同', '服务合同'), ('其他', '其他')
        ]),
        'selection_proc': ('选用程序', [
            ('招标', '招标'), ('议标', '议标'), ('直接采购', '直接采购'),
            ('竞争性谈判', '竞争性谈判'), ('询价', '询价')
        ]),
        'contract_status': ('履约状况', [
            ('正常履约', '正常履约'), ('履约异常', '履约异常'),
            ('已终止', '已终止'), ('已结算', '已结算')
        ]),
        # ---- 出入库类 ----
        'stockin_type': ('入库类型', [
            ('采购入库', '采购入库'), ('退货入库', '退货入库'),
            ('调拨入库', '调拨入库'), ('盘盈入库', '盘盈入库')
        ]),
        'stockout_type': ('出库类型', [
            ('工程领用', '工程领用'), ('调拨出库', '调拨出库'),
            ('退货出库', '退货出库'), ('盘亏出库', '盘亏出库')
        ]),
        'payment_method': ('付款方式', [
            ('银行转账', '银行转账'), ('现金', '现金'), ('承兑汇票', '承兑汇票'),
            ('电汇', '电汇'), ('支票', '支票')
        ]),
        # ---- 采购申请类 ----
        'pr_status': ('采购申请状态', [
            ('草稿', 'draft'), ('待审批', 'pending'), ('审批中', 'approving'),
            ('已通过', 'approved'), ('已驳回', 'rejected'), ('已完成', 'completed')
        ]),
        # ---- 周转材类 ----
        'turnover_type': ('周转材类型', [
            ('自有', 'self'), ('租赁', 'rent')
        ]),
        'turnover_category': ('周转材分类', [
            ('模板类', '模板类'), ('脚手架类', '脚手架类'),
            ('支撑类', '支撑类'), ('其他', '其他')
        ]),
        # ---- 系统类 ----
        'user_status': ('用户状态', [
            ('正常', 'active'), ('禁用', 'disabled')
        ]),
        'data_scope': ('数据权限范围', [
            ('全部数据', 'all'), ('本部门数据', 'dept'), ('本部门及下级', 'dept_and_sub'),
            ('仅本人数据', 'self'), ('自定义', 'custom')
        ]),
        # ---- 其他业务字典 ----
        'cost_subject': ('成本科目', [
            ('主要材料', '主要材料'), ('周转材料', '周转材料'), ('机械费', '机械费'),
            ('人工费', '人工费'), ('其他费用', '其他费用')
        ]),
        'price_type': ('单价类型', [
            ('固定单价', '固定单价'), ('浮动单价', '浮动单价')
        ]),
    }

    for dict_type, (dict_name, items) in presets.items():
        dt = SysDictType.query.filter_by(dict_type=dict_type).first()
        if not dt:
            dt = SysDictType(dict_type=dict_type, dict_name=dict_name, is_active=True)
            db.session.add(dt)
            db.session.flush()
            for idx, (label, value) in enumerate(items):
                db.session.add(SysDictItem(
                    dict_type_id=dt.id, item_label=label, item_value=value,
                    sort_order=idx, is_active=True
                ))
        else:
            # 字典类型已存在，补充缺失的字典项
            existing_values = {i.item_value for i in dt.items.all()}
            next_sort = max((i.sort_order for i in dt.items.all()), default=-1) + 1
            for label, value in items:
                if value not in existing_values:
                    db.session.add(SysDictItem(
                        dict_type_id=dt.id, item_label=label, item_value=value,
                        sort_order=next_sort, is_active=True
                    ))
                    next_sort += 1
    db.session.commit()
    clear_dict_cache()


# ============== 大写金额转换 ==============

def num_to_chinese(n):
    """数字金额转中文大写"""
    if n is None:
        return '零元整'
    try:
        n = float(n)
    except (TypeError, ValueError):
        return '零元整'

    units = ['', '拾', '佰', '仟']
    nums = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']
    decimal_unit = ['角', '分']

    if n == 0:
        return '零元整'

    integer_part = int(n)
    decimal_part = round((n - integer_part) * 100)

    def int_to_chinese(num):
        if num == 0:
            return '零'
        s = str(num)
        res = ''
        zero_flag = False
        for i, ch in enumerate(s):
            digit = int(ch)
            pos = len(s) - i - 1
            if digit == 0:
                if not zero_flag and pos % 4 == 0 and res:
                    res += nums[0]
                zero_flag = True
            else:
                zero_flag = False
                res += nums[digit] + units[pos % 4]
            if pos % 4 == 0 and pos > 0:
                if pos == 4:
                    res += '万'
                elif pos == 8:
                    res += '亿'
        res = res.replace('零万', '万').replace('零亿', '亿')
        res = res.replace('零零', '零').replace('零元', '元')
        return res

    result = int_to_chinese(integer_part) + '元'

    if decimal_part == 0:
        result += '整'
    else:
        jiao = decimal_part // 10
        fen = decimal_part % 10
        if jiao > 0:
            result += nums[jiao] + decimal_unit[0]
        if fen > 0:
            result += nums[fen] + decimal_unit[1]

    return result


# ============== 价格方案计算 ==============

class PriceCalculator:
    """浮动价计算工具类
    根据价格方案配置，从基准价出发逐步计算结算单价。
    """

    @staticmethod
    def calculate(base_price, formula, tax_rate=None):
        """
        根据价格方案计算结算单价。
        :param base_price: 基准价（float/Decimal）
        :param formula: PriceFormula 对象或字典
        :param tax_rate: 可选税率覆盖（%），为空则取方案税率
        :return: dict {
            settlement_price,      # 结算单价(含税)
            price_without_tax,     # 不含税单价
            tax_amount,            # 单位税额
            detail: { base, after_discount, service_fee, capital_fee, tax_rate, ... }
        }
        """
        base = float(base_price or 0)

        # 兼容 ORM 对象和字典
        def _get(key, default=None):
            if isinstance(formula, dict):
                return formula.get(key, default)
            return getattr(formula, key, default) or default

        discount_type = _get('discount_type', '不下浮')
        discount_value = float(_get('discount_value', 0) or 0)
        service_fee_rate = float(_get('service_fee_rate', 0) or 0)
        service_fee_fixed = float(_get('service_fee_fixed', 0) or 0)
        capital_fee_rate = float(_get('capital_fee_rate', 0) or 0)
        capital_fee_days = _get('capital_fee_days', None)
        rate = float(tax_rate if tax_rate is not None else (_get('tax_rate', 13) or 13))
        tax_included = _get('tax_included', True)

        # 1. 下浮计算
        if discount_type == '比例':
            after_discount = base * (1 - discount_value / 100)
        elif discount_type == '金额':
            after_discount = base - discount_value
        else:
            after_discount = base
        after_discount = round(after_discount, 4)

        # 2. 服务费
        if service_fee_rate and service_fee_rate > 0:
            service_fee = after_discount * (service_fee_rate / 100)
        else:
            service_fee = service_fee_fixed or 0
        after_service = round(after_discount + service_fee, 4)

        # 3. 资金使用费
        if capital_fee_rate and capital_fee_rate > 0 and capital_fee_days:
            capital_fee = after_discount * (capital_fee_rate / 100) * (float(capital_fee_days) / 30)
        else:
            capital_fee = 0
        settlement = round(after_service + capital_fee, 4)

        # 4. 含税/不含税处理
        if tax_included:
            settlement_with_tax = settlement
            price_without_tax = round(settlement / (1 + rate / 100), 4) if rate > 0 else settlement
        else:
            price_without_tax = settlement
            settlement_with_tax = round(settlement * (1 + rate / 100), 4) if rate > 0 else settlement
        tax_amount_per_unit = round(settlement_with_tax - price_without_tax, 4)

        detail = {
            'base_price': round(base, 4),
            'discount_type': discount_type,
            'discount_value': discount_value,
            'after_discount': after_discount,
            'service_fee_rate': service_fee_rate,
            'service_fee_fixed': service_fee_fixed,
            'service_fee': round(service_fee, 4),
            'after_service': after_service,
            'capital_fee_rate': capital_fee_rate,
            'capital_fee_days': capital_fee_days,
            'capital_fee': round(capital_fee, 4),
            'tax_rate': rate,
            'tax_included': tax_included,
            'settlement_with_tax': settlement_with_tax,
            'price_without_tax': price_without_tax,
        }
        return {
            'settlement_price': settlement_with_tax,
            'price_without_tax': price_without_tax,
            'tax_amount': tax_amount_per_unit,
            'detail': detail,
        }

    @staticmethod
    def format_detail(detail):
        """将计算明细格式化为可读字符串"""
        parts = [f"基准价: {detail['base_price']}"]
        if detail['discount_type'] == '比例':
            parts.append(f"下浮{detail['discount_value']}%: {detail['after_discount']}")
        elif detail['discount_type'] == '金额':
            parts.append(f"下浮{detail['discount_value']}元: {detail['after_discount']}")
        if detail['service_fee'] > 0:
            if detail['service_fee_rate'] > 0:
                parts.append(f"服务费{detail['service_fee_rate']}%: +{detail['service_fee']}")
            else:
                parts.append(f"服务费固定: +{detail['service_fee']}")
        if detail['capital_fee'] > 0:
            parts.append(f"资金费({detail['capital_fee_rate']}%/{detail['capital_fee_days']}天): +{detail['capital_fee']}")
        parts.append(f"含税单价: {detail['settlement_with_tax']}")
        parts.append(f"税率{detail['tax_rate']}% 不含税: {detail['price_without_tax']}")
        return ' → '.join(parts)

