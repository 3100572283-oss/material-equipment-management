# -*- coding: utf-8 -*-
"""
统一库存成本计算服务

功能:
1. 入库时累加库存金额(暂估/实际)
2. 出库时按加权平均法扣减库存金额
3. 对账确认时差额调整(暂估转实际)
4. 撤销对账时还原库存金额

设计原则:
- 所有库存金额变更必须通过此服务,禁止直接修改 Inventory.actual_amount/estimated_amount
- 使用行级锁避免并发更新丢失
- 统一使用 Decimal 类型避免浮点精度问题
"""
from decimal import Decimal
from app import db
from app.models import Inventory


def _to_decimal(value):
    """安全转换为 Decimal"""
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class InventoryCostService:
    """库存成本计算服务"""

    @staticmethod
    def apply_inbound(project_id, material_id, quantity, amount, price_status='estimated'):
        """入库: 累加库存数量和金额

        Args:
            project_id: 项目ID
            material_id: 物资ID
            quantity: 入库数量
            amount: 入库金额
            price_status: 价格状态 estimated(暂估) / confirmed(实际)
        """
        qty = _to_decimal(quantity)
        amt = _to_decimal(amount)
        if qty == 0:
            return

        # 使用行级锁
        inv = Inventory.query.filter_by(
            project_id=project_id, material_id=material_id
        ).with_for_update().first()
        if not inv:
            inv = Inventory(
                project_id=project_id, material_id=material_id,
                quantity=Decimal(0), estimated_amount=Decimal(0), actual_amount=Decimal(0)
            )
            db.session.add(inv)
            db.session.flush()

        inv.quantity = _to_decimal(inv.quantity) + qty
        if price_status == 'confirmed':
            inv.actual_amount = _to_decimal(inv.actual_amount) + amt
        else:
            inv.estimated_amount = _to_decimal(inv.estimated_amount) + amt

    @staticmethod
    def apply_outbound(project_id, material_id, quantity):
        """出库: 按加权平均法扣减库存数量和金额

        Args:
            project_id: 项目ID
            material_id: 物资ID
            quantity: 出库数量(正数)
        """
        qty = _to_decimal(quantity)
        if qty <= 0:
            return Decimal(0)

        inv = Inventory.query.filter_by(
            project_id=project_id, material_id=material_id
        ).with_for_update().first()
        if not inv:
            return Decimal(0)

        current_qty = _to_decimal(inv.quantity)
        if current_qty <= 0:
            return Decimal(0)

        # 加权平均单价 = (实际金额 + 暂估金额) / 数量
        actual = _to_decimal(inv.actual_amount)
        estimated = _to_decimal(inv.estimated_amount)
        total_amount = actual + estimated
        if total_amount <= 0:
            inv.quantity = current_qty - qty
            return Decimal(0)

        avg_price = total_amount / current_qty
        out_amount = avg_price * qty

        # 按比例扣减实际和暂估
        if total_amount > 0:
            actual_ratio = actual / total_amount
            actual_deduction = out_amount * actual_ratio
            est_deduction = out_amount - actual_deduction

            inv.actual_amount = max(Decimal(0), actual - actual_deduction)
            inv.estimated_amount = max(Decimal(0), estimated - est_deduction)

        inv.quantity = max(Decimal(0), current_qty - qty)
        return out_amount

    @staticmethod
    def adjust_reconciliation(project_id, material_id, original_amount, new_amount):
        """对账确认: 暂估转实际(差额调整)

        Args:
            project_id: 项目ID
            material_id: 物资ID
            original_amount: 原暂估金额
            new_amount: 新实际金额
        """
        orig = _to_decimal(original_amount)
        new = _to_decimal(new_amount)

        inv = Inventory.query.filter_by(
            project_id=project_id, material_id=material_id
        ).with_for_update().first()
        if not inv:
            return

        # 冲减暂估金额,累加实际金额
        inv.estimated_amount = max(Decimal(0), _to_decimal(inv.estimated_amount) - orig)
        inv.actual_amount = _to_decimal(inv.actual_amount) + new

    @staticmethod
    def reverse_reconciliation(project_id, material_id, settled_amount, original_amount):
        """撤销对账: 还原为暂估状态

        Args:
            project_id: 项目ID
            material_id: 物资ID
            settled_amount: 对账时的结算金额(需从actual_amount冲减)
            original_amount: 原始暂估金额(需加回estimated_amount)
        """
        settled = _to_decimal(settled_amount)
        original = _to_decimal(original_amount)

        inv = Inventory.query.filter_by(
            project_id=project_id, material_id=material_id
        ).with_for_update().first()
        if not inv:
            return

        inv.actual_amount = max(Decimal(0), _to_decimal(inv.actual_amount) - settled)
        inv.estimated_amount = _to_decimal(inv.estimated_amount) + original

    @staticmethod
    def get_weighted_average_price(project_id, material_id):
        """获取当前加权平均单价"""
        inv = Inventory.query.filter_by(
            project_id=project_id, material_id=material_id
        ).first()
        if not inv:
            return Decimal(0)
        qty = _to_decimal(inv.quantity)
        if qty <= 0:
            return Decimal(0)
        total = _to_decimal(inv.actual_amount) + _to_decimal(inv.estimated_amount)
        return total / qty

    @staticmethod
    def get_inventory_value(project_id, material_id):
        """获取库存总金额(实际+暂估)"""
        inv = Inventory.query.filter_by(
            project_id=project_id, material_id=material_id
        ).first()
        if not inv:
            return Decimal(0)
        return _to_decimal(inv.actual_amount) + _to_decimal(inv.estimated_amount)
