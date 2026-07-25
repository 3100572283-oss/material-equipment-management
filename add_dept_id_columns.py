"""批量给业务表添加 dept_id 字段

为以下表添加 dept_id 字段（如尚未存在）：
- stock_outs (StockOut)
- contracts (Contract)
- purchase_requisition (PurchaseRequisition)
- material_transfer (MaterialTransfer)
- reconciliations (Reconciliation)
- material_scrap (MaterialScrap)
- equipment (Equipment)
- concrete_ticket (ConcreteTicket)
- turnover_record (TurnoverRecord)
- payment_applications (PaymentApplication)
- stock_check (StockCheck)
- subcontract_deduction (SubcontractDeduction)
"""
import sqlite3

db_path = '/opt/material-equipment-management/material_mgmt.db'
c = sqlite3.connect(db_path)
cur = c.cursor()

tables = [
    'stock_outs',
    'contracts',
    'purchase_requisition',
    'material_transfer',
    'reconciliations',
    'material_scrap',
    'equipment',
    'concrete_ticket',
    'turnover_record',
    'payment_applications',
    'stock_check',
    'subcontract_deduction',
]

for tbl in tables:
    cur.execute(f'PRAGMA table_info({tbl})')
    cols = [r[1] for r in cur.fetchall()]
    if 'dept_id' in cols:
        print(f'  SKIP {tbl} (已有 dept_id)')
        continue
    try:
        cur.execute(f'ALTER TABLE {tbl} ADD COLUMN dept_id INTEGER REFERENCES sys_dept(id)')
        print(f'  + {tbl} 添加 dept_id')
    except Exception as e:
        print(f'  ! {tbl} 失败: {e}')

c.commit()
c.close()
print('完成。')
