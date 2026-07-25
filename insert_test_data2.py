"""用原始SQL插入入库测试数据"""
import sqlite3
import os
from datetime import date, datetime

db_path = '/opt/material-equipment-management/material_mgmt.db'
c = sqlite3.connect(db_path)
cur = c.cursor()

# 检查现有数据
cur.execute('SELECT COUNT(*) FROM stock_ins')
if cur.fetchone()[0] > 0:
    print('已有数据，跳过')
    exit(0)

# 取部门和项目
cur.execute('SELECT id, dept_name FROM sys_dept LIMIT 5')
depts = cur.fetchall()
print(f'使用部门: {[d[1] for d in depts]}')
cur.execute('SELECT id, name FROM projects LIMIT 1')
proj = cur.fetchone()
print(f'使用项目: {proj}')

today = date.today()
for i, d in enumerate(depts):
    for j in range(2):
        code = f"RK{today.strftime('%Y%m%d')}{i*2+j+1:03d}"
        try:
            cur.execute('''
                INSERT INTO stock_ins
                (project_id, code, stock_in_date, stock_in_type, supplier_id,
                 operator, total_quantity, total_amount, approval_status,
                 quality_status, dept_id, status, created_at, is_deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (proj[0], code, today.isoformat(), '采购入库', 1, '测试',
                  10, 1000.00, 'approved', 'qualified', d[0], 'completed',
                  datetime.now().isoformat(), 0))
            print(f'  + 部门{d[1]} code={code}')
        except Exception as e:
            print(f'  ! insert fail: {e}')

c.commit()
cur.execute('SELECT COUNT(*), dept_id FROM stock_ins GROUP BY dept_id')
print('\n入库分布:')
for r in cur.fetchall():
    print(f'  dept_id={r[1]}: {r[0]} 条')
c.close()
