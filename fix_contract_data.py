"""
修复合同明细数据：
1. 补全不含税单价
2. 关联入库单明细与合同明细，更新累计入库量
"""
import sys
sys.path.insert(0, '/Users/zhengfeilong/.trae-cn/worktrees/物资设备管理/feat-phase1-material-mgmt-system-8uvyDM')

from app import create_app, db
from app.models import ContractItem, StockInItem, StockIn
from app.utils import calc_unit_price_without_tax
from sqlalchemy import func

app = create_app()

with app.app_context():
    print("=== 修复合同明细数据 ===\n")

    # 1. 修复不含税单价
    print("1. 修复不含税单价...")
    items = ContractItem.query.all()
    fixed_count = 0
    for item in items:
        if item.unit_price_without_tax is None or float(item.unit_price_without_tax) == 0:
            if item.unit_price_with_tax and float(item.unit_price_with_tax) > 0:
                item.unit_price_without_tax = calc_unit_price_without_tax(
                    item.unit_price_with_tax, item.tax_rate
                )
                fixed_count += 1
    db.session.commit()
    print(f"   已修复 {fixed_count} 条合同明细的不含税单价")

    # 2. 关联入库单明细与合同明细
    print("\n2. 关联入库单明细与合同明细...")
    # 获取所有有关联合同的入库单
    stock_ins = StockIn.query.filter(StockIn.contract_id != None).all()
    linked_count = 0

    for si in stock_ins:
        # 获取该合同的所有明细
        contract_items = ContractItem.query.filter_by(contract_id=si.contract_id).all()
        if not contract_items:
            continue

        # 为每个入库单明细找到匹配的物资
        for si_item in si.items:
            if si_item.contract_item_id:
                continue  # 已关联，跳过

            # 按物资ID匹配合同明细
            for ci in contract_items:
                if ci.material_id == si_item.material_id:
                    si_item.contract_item_id = ci.id
                    linked_count += 1
                    break

    db.session.commit()
    print(f"   已关联 {linked_count} 条入库明细与合同明细")

    # 3. 重新计算累计入库量
    print("\n3. 重新计算合同明细累计入库量...")
    # 先清零
    ContractItem.query.update({ContractItem.total_in_qty: 0})
    db.session.commit()

    # 按合同明细分组统计已审批的入库数量
    results = db.session.query(
        StockInItem.contract_item_id,
        func.coalesce(func.sum(StockInItem.quantity), 0)
    ).join(StockIn, StockInItem.stock_in_id == StockIn.id).filter(
        StockInItem.contract_item_id != None,
        StockIn.approval_status == 'passed'
    ).group_by(StockInItem.contract_item_id).all()

    updated_count = 0
    for contract_item_id, total_qty in results:
        ci = ContractItem.query.get(contract_item_id)
        if ci:
            ci.total_in_qty = total_qty
            updated_count += 1

    db.session.commit()
    print(f"   已更新 {updated_count} 条合同明细的累计入库量")

    # 4. 验证
    print("\n=== 验证结果 ===")
    zero_ut = ContractItem.query.filter(
        (ContractItem.unit_price_without_tax == 0) | (ContractItem.unit_price_without_tax == None)
    ).count()
    zero_ti = ContractItem.query.filter(
        (ContractItem.total_in_qty == 0) | (ContractItem.total_in_qty == None)
    ).count()
    linked = StockInItem.query.filter(StockInItem.contract_item_id != None).count()
    total_si = StockInItem.query.count()

    print(f"不含税单价为0: {zero_ut}")
    print(f"累计入库量为0: {zero_ti}")
    print(f"入库明细关联合同明细: {linked} / {total_si}")

    # 查看示例
    item = ContractItem.query.first()
    if item:
        print(f"\n示例: unit_price_with_tax={item.unit_price_with_tax}, "
              f"unit_price_without_tax={item.unit_price_without_tax}, "
              f"total_in_qty={item.total_in_qty}")

    print("\n修复完成！")
