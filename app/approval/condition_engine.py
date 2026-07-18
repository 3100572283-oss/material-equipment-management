"""审批分支条件表达式引擎"""
import operator
from datetime import datetime


FIELD_LABELS = {
    'total_amount': '总金额',
    'amount': '金额',
    'contract_type': '合同类型',
    'business_type': '业务类型',
    'supplier': '供应商',
    'supplier_category': '供应商分类',
    'stock_in_type': '入库类型',
    'stock_out_type': '出库类型',
    'usage_unit_id': '领料单位',
    'apply_dept': '申请部门',
    'payment_method': '付款方式',
}


OPERATOR_LABELS = {
    '>': '大于',
    '<': '小于',
    '>=': '大于等于',
    '<=': '小于等于',
    '==': '等于',
    '!=': '不等于',
    'contains': '包含',
    'not_contains': '不包含',
    'between': '在范围内',
}


class ConditionEngine:
    """条件表达式引擎"""

    @classmethod
    def evaluate_condition(cls, field, operator, value, biz_data):
        """评估单个条件是否满足"""
        actual_value = cls.get_field_value(field, biz_data)
        if actual_value is None:
            return False

        try:
            return cls.compare(actual_value, operator, value)
        except Exception:
            return False

    @classmethod
    def get_field_value(cls, field, biz_data):
        """从业务数据中获取字段值"""
        if isinstance(biz_data, dict):
            return biz_data.get(field)
        return getattr(biz_data, field, None)

    @classmethod
    def compare(cls, actual, op, expected):
        """执行比较操作"""
        op_func = cls.get_operator_func(op)
        if op_func is None:
            return False

        if op in ('between',):
            try:
                parts = expected.split(',')
                if len(parts) == 2:
                    min_val = cls.parse_value(parts[0].strip(), type(actual))
                    max_val = cls.parse_value(parts[1].strip(), type(actual))
                    return min_val <= actual <= max_val
            except Exception:
                return False
            return False

        if op in ('contains', 'not_contains'):
            actual_str = str(actual) if actual is not None else ''
            expected_str = str(expected) if expected is not None else ''
            contains = expected_str in actual_str
            return contains if op == 'contains' else not contains

        expected_val = cls.parse_value(expected, type(actual))
        return op_func(actual, expected_val)

    @classmethod
    def get_operator_func(cls, op):
        """获取运算符对应的函数"""
        ops = {
            '>': operator.gt,
            '<': operator.lt,
            '>=': operator.ge,
            '<=': operator.le,
            '==': operator.eq,
            '!=': operator.ne,
        }
        return ops.get(op)

    @classmethod
    def parse_value(cls, value, target_type):
        """将字符串值转换为目标类型"""
        if target_type is None:
            return value
        if target_type in (int, float):
            try:
                return float(value)
            except ValueError:
                return 0
        if target_type == datetime:
            try:
                return datetime.strptime(str(value), '%Y-%m-%d')
            except (ValueError, TypeError):
                return datetime.min
        return str(value)

    @classmethod
    def evaluate_branch(cls, branch, biz_data):
        """评估分支条件是否满足"""
        if branch.is_default:
            return True

        conditions = branch.conditions.all()
        if not conditions:
            if branch.condition_field:
                return cls.evaluate_condition(
                    branch.condition_field,
                    branch.condition_operator or '==',
                    branch.condition_value or '',
                    biz_data
                )
            return False

        logic = branch.condition_logic or 'AND'
        results = []
        for cond in conditions:
            result = cls.evaluate_condition(
                cond.condition_field,
                cond.condition_operator,
                cond.condition_value,
                biz_data
            )
            results.append(result)

        if logic == 'AND':
            return all(results)
        else:
            return any(results)

    @classmethod
    def build_condition_expression(cls, branch):
        """构建可读的条件表达式"""
        conditions = branch.conditions.all()
        if not conditions:
            if branch.condition_field:
                field_label = FIELD_LABELS.get(branch.condition_field, branch.condition_field)
                op_label = OPERATOR_LABELS.get(branch.condition_operator, branch.condition_operator)
                return f"{field_label} {op_label} {branch.condition_value}"
            return '无条件'

        logic_label = '且' if branch.condition_logic == 'AND' else '或'
        expr_parts = []
        for cond in conditions:
            field_label = FIELD_LABELS.get(cond.condition_field, cond.condition_field)
            op_label = OPERATOR_LABELS.get(cond.condition_operator, cond.condition_operator)
            expr_parts.append(f"{field_label} {op_label} {cond.condition_value}")
        return f" {logic_label} ".join(expr_parts)

    @classmethod
    def get_available_fields(cls, biz_type):
        """获取指定业务类型的可用条件字段"""
        field_groups = {
            'contract': [
                {'value': 'total_amount', 'label': '合同总金额'},
                {'value': 'contract_type', 'label': '合同类型'},
                {'value': 'business_type', 'label': '业务类型'},
                {'value': 'supplier', 'label': '供应商'},
            ],
            'stockin': [
                {'value': 'total_amount', 'label': '入库总金额'},
                {'value': 'stock_in_type', 'label': '入库类型'},
                {'value': 'supplier', 'label': '供应商'},
            ],
            'stockout': [
                {'value': 'total_amount', 'label': '出库总金额'},
                {'value': 'stock_out_type', 'label': '出库类型'},
                {'value': 'usage_unit_id', 'label': '领料单位'},
            ],
            'payment': [
                {'value': 'amount', 'label': '付款金额'},
                {'value': 'payment_method', 'label': '付款方式'},
            ],
            'purchase': [
                {'value': 'total_amount', 'label': '申请总金额'},
                {'value': 'apply_dept', 'label': '申请部门'},
            ],
        }
        return field_groups.get(biz_type, [])

    @classmethod
    def get_operators(cls, field_type='numeric'):
        """获取可用运算符"""
        if field_type == 'text':
            return [
                {'value': '==', 'label': '等于'},
                {'value': '!=', 'label': '不等于'},
                {'value': 'contains', 'label': '包含'},
                {'value': 'not_contains', 'label': '不包含'},
            ]
        return [
            {'value': '>', 'label': '大于'},
            {'value': '<', 'label': '小于'},
            {'value': '>=', 'label': '大于等于'},
            {'value': '<=', 'label': '小于等于'},
            {'value': '==', 'label': '等于'},
            {'value': '!=', 'label': '不等于'},
            {'value': 'between', 'label': '在范围内'},
        ]
