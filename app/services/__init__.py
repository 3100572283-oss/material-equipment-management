# -*- coding: utf-8 -*-
"""
app.services - 公共业务服务层

统一封装核心业务计算逻辑,避免各模块重复实现导致不一致:
- InventoryCostService: 库存成本计算(加权平均、出入库金额处理)
- BusinessLogger: 全链路业务日志
- NotificationService: 通知推送服务(钉钉/企业微信/飞书/邮件/站内消息)
"""
from .inventory_cost import InventoryCostService
from .business_logger import BusinessLogger, biz_log

__all__ = ['InventoryCostService', 'BusinessLogger', 'biz_log']
