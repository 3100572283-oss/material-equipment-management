"""消息推送服务（站内消息 + 外部渠道）"""
import json
import hmac
import hashlib
import base64
import time
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from flask import current_app, url_for
from app import db
from app.models import NotificationLog, Message
from app.utils import get_config


def _get_prefix():
    return get_config('notify_title_prefix', '[物资系统]')


def send_dingtalk(title, content, webhook=None, secret=None):
    """发送钉钉群机器人消息"""
    webhook = webhook or get_config('notify_dingtalk_webhook', '')
    secret = secret or get_config('notify_dingtalk_secret', '')
    if not webhook:
        return False, '钉钉Webhook未配置'

    headers = {'Content-Type': 'application/json'}
    url = webhook
    if secret:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f'{timestamp}\n{secret}'
        hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f'{webhook}&timestamp={timestamp}&sign={sign}'

    body = {
        'msgtype': 'markdown',
        'markdown': {
            'title': f'{_get_prefix()} {title}',
            'text': content
        }
    }
    try:
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get('errcode') == 0:
            return True, None
        return False, result.get('errmsg', '未知错误')
    except Exception as e:
        return False, str(e)


def send_wechat(title, content, webhook=None):
    """发送企业微信群机器人消息"""
    webhook = webhook or get_config('notify_wechat_webhook', '')
    if not webhook:
        return False, '企业微信Webhook未配置'

    body = {
        'msgtype': 'markdown',
        'markdown': {
            'content': f'{_get_prefix()} **{title}**\n\n{content}'
        }
    }
    try:
        import urllib.request
        req = urllib.request.Request(webhook, data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get('errcode') == 0:
            return True, None
        return False, result.get('errmsg', '未知错误')
    except Exception as e:
        return False, str(e)


def send_feishu(title, content, webhook=None):
    """发送飞书群机器人消息"""
    webhook = webhook or get_config('notify_feishu_webhook', '')
    if not webhook:
        return False, '飞书Webhook未配置'

    body = {
        'msg_type': 'text',
        'content': {
            'text': f'{_get_prefix()} {title}\n\n{content}'
        }
    }
    try:
        import urllib.request
        req = urllib.request.Request(webhook, data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get('code') == 0:
            return True, None
        return False, result.get('msg', '未知错误')
    except Exception as e:
        return False, str(e)


def send_email(title, content, recipients=None):
    """发送邮件通知"""
    host = get_config('notify_email_smtp_host', '')
    port = int(get_config('notify_email_smtp_port', '465'))
    account = get_config('notify_email_account', '')
    password = get_config('notify_email_password', '')
    recipients = recipients or get_config('notify_email_recipients', '')

    if not host or not account:
        return False, '邮件SMTP未配置'
    if not recipients:
        return False, '收件人未配置'

    try:
        msg = MIMEMultipart()
        msg['From'] = account
        msg['To'] = recipients
        msg['Subject'] = f'{_get_prefix()} {title}'
        msg.attach(MIMEText(content, 'html', 'utf-8'))

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        server.login(account, password)
        server.sendmail(account, recipients.split(','), msg.as_string())
        server.quit()
        return True, None
    except Exception as e:
        return False, str(e)


def push_notification(title, content, channels=None):
    """统一推送入口，自动遍历已配置的渠道"""
    if channels is None:
        channels = []
        if get_config('notify_dingtalk_enabled') == 'true':
            channels.append('dingtalk')
        if get_config('notify_wechat_enabled') == 'true':
            channels.append('wechat')
        if get_config('notify_feishu_enabled') == 'true':
            channels.append('feishu')
        if get_config('notify_email_enabled') == 'true':
            channels.append('email')

    results = []
    for ch in channels:
        success, err = False, ''
        if ch == 'dingtalk':
            success, err = send_dingtalk(title, content)
        elif ch == 'wechat':
            success, err = send_wechat(title, content)
        elif ch == 'feishu':
            success, err = send_feishu(title, content)
        elif ch == 'email':
            success, err = send_email(title, content)

        log = NotificationLog(
            channel=ch,
            title=title,
            content=content[:500],
            status='success' if success else 'failed',
            error_msg=err
        )
        db.session.add(log)
        db.session.commit()
        results.append((ch, success, err))

    return results


# ============ 推送触发场景 ============

def notify_pending_approval(approver_name, biz_type, biz_no, url=''):
    """待审批通知"""
    title = f'待审批通知'
    content = f'**审批人**: {approver_name}\n\n**单据类型**: {biz_type}\n\n**单据编号**: {biz_no}\n\n请尽快处理审批。'
    if url:
        content += f'\n\n[点击查看]({url})'
    push_notification(title, content)


def notify_approval_result(applicant_name, biz_type, biz_no, result, remark=''):
    """审批结果通知"""
    title = f'审批结果通知 - {result}'
    content = f'**申请人**: {applicant_name}\n\n**单据类型**: {biz_type}\n\n**单据编号**: {biz_no}\n\n**审批结果**: {result}'
    if remark:
        content += f'\n\n**审批意见**: {remark}'
    push_notification(title, content)


# ============ 站内消息（消息中心） ============

# 消息类型常量
MSG_TYPE_APPROVAL = 'approval'   # 审批通知
MSG_TYPE_SYSTEM = 'system'        # 系统通知
MSG_TYPE_WARNING = 'warning'      # 预警通知


def send_message(user_id, msg_type, title, content, biz_type=None, biz_id=None, url=None):
    """统一站内消息发送方法

    所有业务场景统一调用此方法，不重复写代码。
    :param user_id: 接收人ID
    :param msg_type: 消息类型（approval/system/warning）
    :param title: 标题
    :param content: 内容
    :param biz_type: 业务类型（requisition/contract/payment/stock_in/reconciliation 等）
    :param biz_id: 业务单据ID
    :param url: 跳转路径
    :return: Message 实例
    """
    msg = Message(
        user_id=user_id,
        msg_type=msg_type,
        title=title[:128],
        content=content,
        biz_type=biz_type,
        biz_id=biz_id,
        url=url,
        is_read=False
    )
    db.session.add(msg)
    db.session.commit()
    return msg


def send_approval_message(instance, event_type, actor=None, opinion=''):
    """审批流程自动触发站内消息

    在审批流转的四个关键节点自动发消息：
    1. submit  - 提交审批 → 给首节点审批人发"待审批"消息
    2. approve  - 节点通过 → 给下一节点审批人发"待审批"消息
    3. reject   - 审批驳回 → 给提交人发"已驳回"消息
    4. complete - 全部通过 → 给提交人发"已通过"消息

    支持或签/会签：所有审批人都收到消息。

    :param instance: ApprovalInstance 实例
    :param event_type: submit/approve/reject/complete
    :param actor: 触发用户对象
    :param opinion: 审批意见
    """
    biz_name_map = {
        'requisition': '采购申请',
        'contract': '合同',
        'payment': '付款申请',
        'stock_in': '入库单',
        'reconciliation': '对账单',
        'stock_check': '盘点单',
        'material_transfer': '调拨单',
        'scrap': '报废单',
    }
    biz_name = biz_name_map.get(instance.biz_type, instance.biz_type)
    biz_title = instance.biz_title or f'{biz_name}审批'
    biz_no = ''
    biz_obj = None
    try:
        from app.approval.service import get_biz_obj
        biz_obj = get_biz_obj(instance.biz_type, instance.biz_id)
        if biz_obj:
            biz_no = getattr(biz_obj, 'code', '') or getattr(biz_obj, 'req_code', '') or ''
    except Exception:
        pass

    # 审批详情页URL
    detail_url = f'/approval/detail/{instance.id}?from=msg_center'

    actor_name = ''
    if actor:
        actor_name = actor.name or actor.username

    if event_type == 'submit':
        # 提交审批：给首节点所有审批人发消息
        approvers = _get_node_approvers(instance.current_node) if instance.current_node else []
        title = f'待审批：{biz_title}'
        content = f'{biz_name}「{biz_no or biz_title}」已提交审批，请尽快处理。'
        for approver in approvers:
            if approver.id != instance.applicant_id:  # 不给自己发
                send_message(
                    user_id=approver.id,
                    msg_type=MSG_TYPE_APPROVAL,
                    title=title,
                    content=content,
                    biz_type=instance.biz_type,
                    biz_id=instance.biz_id,
                    url=detail_url
                )

    elif event_type == 'approve':
        # 节点通过：给下一节点审批人发消息
        if instance.current_node and instance.status in ('pending', 'approving'):
            approvers = _get_node_approvers(instance.current_node)
            title = f'待审批：{biz_title}'
            content = f'{biz_name}「{biz_no or biz_title}」已流转到您，请尽快处理。'
            for approver in approvers:
                if approver.id != (actor.id if actor else None):
                    send_message(
                        user_id=approver.id,
                        msg_type=MSG_TYPE_APPROVAL,
                        title=title,
                        content=content,
                        biz_type=instance.biz_type,
                        biz_id=instance.biz_id,
                        url=detail_url
                    )

    elif event_type == 'reject':
        # 审批驳回：给提交人发消息
        title = f'审批驳回：{biz_title}'
        content = f'您提交的{biz_name}「{biz_no or biz_title}」已被驳回。'
        if opinion:
            content += f'\n驳回原因：{opinion}'
        if actor:
            content += f'\n审批人：{actor_name}'
        send_message(
            user_id=instance.applicant_id,
            msg_type=MSG_TYPE_APPROVAL,
            title=title,
            content=content,
            biz_type=instance.biz_type,
            biz_id=instance.biz_id,
            url=detail_url
        )

    elif event_type == 'complete':
        # 全部通过：给提交人发消息
        title = f'审批通过：{biz_title}'
        content = f'您提交的{biz_name}「{biz_no or biz_title}」已全部审批通过。'
        if actor:
            content += f'\n最后审批人：{actor_name}'
        send_message(
            user_id=instance.applicant_id,
            msg_type=MSG_TYPE_APPROVAL,
            title=title,
            content=content,
            biz_type=instance.biz_type,
            biz_id=instance.biz_id,
            url=detail_url
        )


def _get_node_approvers(node):
    """获取审批节点的所有审批人列表

    支持角色审批和指定用户审批，返回 User 列表
    """
    from app.models import User, SysRole
    if not node:
        return []
    approvers = []
    if node.approve_type == 'role':
        role_codes = [c.strip() for c in (node.approve_role or '').split(',') if c.strip()]
        if role_codes:
            users = User.query.filter(
                User.status == 'active',
                User.role.in_(role_codes)
            ).distinct().all()
            approvers.extend(users)
    elif node.approve_type == 'user' and node.approver_user_id:
        user = User.query.get(node.approver_user_id)
        if user and user.status == 'active':
            approvers.append(user)
    else:
        # 默认取指定审批人
        if node.approver_user_id:
            user = User.query.get(node.approver_user_id)
            if user and user.status == 'active':
                approvers.append(user)
    return approvers


def notify_daily_warnings():
    """每日定时推送预警信息"""
    from app.models import Supplier, Equipment, EquipmentMaintenance
    from datetime import date, timedelta

    today = date.today()
    warning_date = today + timedelta(days=30)

    sections = []

    # 供应商资质到期
    expired_suppliers = []
    for s in Supplier.query.filter(
        db.or_(
            db.and_(Supplier.license_expire_date != None, Supplier.license_expire_date <= warning_date),
            db.and_(Supplier.certificate_expire_date != None, Supplier.certificate_expire_date <= warning_date)
        )
    ).all():
        expired_suppliers.append(s)
    if expired_suppliers:
        sections.append('**供应商资质到期预警:**')
        for s in expired_suppliers:
            status = '已过期' if (s.license_expire_date and s.license_expire_date < today) or \
                                  (s.certificate_expire_date and s.certificate_expire_date < today) else '即将到期'
            sections.append(f'  - {s.name} ({status})')

    # 设备维保到期
    expired_maint = EquipmentMaintenance.query.join(Equipment).filter(
        EquipmentMaintenance.next_maintain_date != None,
        EquipmentMaintenance.next_maintain_date <= warning_date
    ).all()
    if expired_maint:
        sections.append('\n**设备维保到期预警:**')
        for m in expired_maint:
            status = '已过期' if m.next_maintain_date < today else '即将到期'
            sections.append(f'  - {m.equipment.name} - 下次维保: {m.next_maintain_date} ({status})')

    if not sections:
        return

    title = f'每日预警通知 ({today.strftime("%Y-%m-%d")})'
    content = '\n'.join(sections)
    push_notification(title, content)
