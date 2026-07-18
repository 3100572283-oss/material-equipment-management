# -*- coding: utf-8 -*-
"""
全链路业务日志工具

功能:
1. 记录前端接口入参出参
2. 记录后端单据计算全过程
3. 记录打印渲染执行记录
4. 支持偶现问题快速定位

设计原则:
- 日志写入失败不影响主流程
- 敏感信息脱敏
- 支持结构化查询
"""
import json
import logging
from datetime import datetime
from flask import request, g
from app import db

logger = logging.getLogger('business')


class BusinessLogger:
    """全链路业务日志"""

    @staticmethod
    def _get_trace_id():
        """获取请求追踪ID"""
        if hasattr(g, 'trace_id'):
            return g.trace_id
        return '-'

    @staticmethod
    def _mask_sensitive(data):
        """脱敏敏感字段"""
        if not isinstance(data, dict):
            return data
        sensitive_keys = ('password', 'api_key', 'token', 'secret')
        masked = {}
        for k, v in data.items():
            if k.lower() in sensitive_keys:
                masked[k] = '***'
            elif isinstance(v, dict):
                masked[k] = BusinessLogger._mask_sensitive(v)
            else:
                masked[k] = v
        return masked

    @staticmethod
    def log_api_call(module, action, request_data=None, response_data=None, 
                     status='success', error=None, duration_ms=None):
        """记录API调用

        Args:
            module: 模块名(stock_in/stock_out/reconciliation等)
            action: 操作名(create/edit/delete/confirm等)
            request_data: 请求数据
            response_data: 响应数据
            status: 状态(success/fail/error)
            error: 错误信息
            duration_ms: 耗时(毫秒)
        """
        try:
            trace_id = BusinessLogger._get_trace_id()
            req_masked = BusinessLogger._mask_sensitive(request_data) if request_data else None
            resp_masked = BusinessLogger._mask_sensitive(response_data) if response_data else None

            log_entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'trace_id': trace_id,
                'module': module,
                'action': action,
                'status': status,
                'duration_ms': duration_ms,
                'request': req_masked,
                'response': resp_masked,
                'error': str(error) if error else None,
                'path': request.path if request else None,
                'method': request.method if request else None,
                'ip': request.remote_addr if request else None,
            }
            logger.info(json.dumps(log_entry, ensure_ascii=False, default=str))
        except Exception as e:
            # 日志写入失败不影响主流程
            logger.error(f'业务日志写入失败: {e}')

    @staticmethod
    def log_calculation(module, operation, input_data, output_data, formula=None):
        """记录业务计算过程

        Args:
            module: 模块名
            operation: 计算操作(如 weighted_average_cost, reconciliation_adjust)
            input_data: 输入数据
            output_data: 输出数据
            formula: 计算公式说明
        """
        try:
            trace_id = BusinessLogger._get_trace_id()
            log_entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'trace_id': trace_id,
                'type': 'calculation',
                'module': module,
                'operation': operation,
                'formula': formula,
                'input': BusinessLogger._mask_sensitive(input_data) if input_data else None,
                'output': BusinessLogger._mask_sensitive(output_data) if output_data else None,
            }
            logger.info(json.dumps(log_entry, ensure_ascii=False, default=str))
        except Exception as e:
            logger.error(f'计算日志写入失败: {e}')

    @staticmethod
    def log_print(module, doc_id, doc_code, status='success', error=None, duration_ms=None):
        """记录打印渲染

        Args:
            module: 模块名
            doc_id: 单据ID
            doc_code: 单据编号
            status: 状态
            error: 错误信息
            duration_ms: 耗时
        """
        try:
            trace_id = BusinessLogger._get_trace_id()
            log_entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'trace_id': trace_id,
                'type': 'print',
                'module': module,
                'doc_id': doc_id,
                'doc_code': doc_code,
                'status': status,
                'error': str(error) if error else None,
                'duration_ms': duration_ms,
            }
            logger.info(json.dumps(log_entry, ensure_ascii=False, default=str))
        except Exception as e:
            logger.error(f'打印日志写入失败: {e}')

    @staticmethod
    def log_inventory_change(project_id, material_id, change_type, 
                             before_qty, after_qty, 
                             before_amount, after_amount, 
                             reason=None):
        """记录库存变更(用于偶现问题定位)

        Args:
            project_id: 项目ID
            material_id: 物资ID
            change_type: 变更类型(inbound/outbound/reconciliation/reverse)
            before_qty: 变更前数量
            after_qty: 变更后数量
            before_amount: 变更前金额
            after_amount: 变更后金额
            reason: 变更原因
        """
        try:
            trace_id = BusinessLogger._get_trace_id()
            log_entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'trace_id': trace_id,
                'type': 'inventory_change',
                'project_id': project_id,
                'material_id': material_id,
                'change_type': change_type,
                'before_qty': str(before_qty),
                'after_qty': str(after_qty),
                'before_amount': str(before_amount),
                'after_amount': str(after_amount),
                'reason': reason,
            }
            logger.info(json.dumps(log_entry, ensure_ascii=False, default=str))
        except Exception as e:
            logger.error(f'库存变更日志写入失败: {e}')


# 便捷函数
def biz_log(module, action, **kwargs):
    """便捷业务日志函数"""
    BusinessLogger.log_api_call(module, action, **kwargs)
