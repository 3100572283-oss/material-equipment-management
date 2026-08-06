# -*- coding: utf-8 -*-
"""
app.services.notification_service - 通知服务统一入口

本模块是对 app.notification_service 的封装和扩展，
提供统一的通知服务接口，供各业务模块调用。

功能：
- 钉钉/企业微信/飞书/邮件 Webhook 通知
- 站内消息（消息中心）
- 审批流程通知
- 库存预警通知
- 每日定时预警
"""
# 重导出已有的通知功能，保持向后兼容
from app.notification_service import (
    send_dingtalk,
    send_wechat,
    send_feishu,
    send_email,
    push_notification,
    notify_pending_approval,
    notify_approval_result,
    send_message,
    send_approval_message,
    notify_daily_warnings,
    MSG_TYPE_APPROVAL,
    MSG_TYPE_SYSTEM,
    MSG_TYPE_WARNING,
)

# 库存预警通知（新增功能）
from app.notification_service import notify_inventory_warning

__all__ = [
    'send_dingtalk',
    'send_wechat',
    'send_feishu',
    'send_email',
    'push_notification',
    'notify_pending_approval',
    'notify_approval_result',
    'send_message',
    'send_approval_message',
    'notify_daily_warnings',
    'notify_inventory_warning',
    'MSG_TYPE_APPROVAL',
    'MSG_TYPE_SYSTEM',
    'MSG_TYPE_WARNING',
]
