"""插入测试入库数据，分布在不同部门，用于数据权限测试"""
import sys
sys.path.insert(0, '/opt/material-equipment-management')
from datetime import datetime, date
from app import create_app, db
from app.models import StockIn, SysDept, Project, Supplier, Material

app = create_app()
with app.app_context():
    # 检查现有数据
    existing = StockIn.query.count()
    if existing > 0:
        print(f"已有 {existing} 条入库数据，跳过插入")
        sys.exit(0)

    # 取部门、项目、供应商、物资
    depts = SysDept.query.limit(5).all()
    if not depts:
        print("ERROR: 无部门数据")
        sys.exit(1)
    project = Project.query.first()
    if not project:
        print("ERROR: 无项目数据")
        sys.exit(1)
    supplier = Supplier.query.first()
    if not supplier:
        print("ERROR: 无供应商")
        sys.exit(1)
    material = Material.query.first()
    if not material:
        print("ERROR: 无物资")
        sys.exit(1)

    print(f"使用项目: {project.project_name}, 供应商: {supplier.supplier_name}, 物资: {material.name}")

    # 为每个部门插入2条入库单
    today = date.today()
    for i, d in enumerate(depts):
        for j in range(2):
            stock_in = StockIn(
                project_id=project.id,
                code=f"RK{today.strftime('%Y%m%d')}{i*2+j+1:03d}",
                stock_in_date=today,
                stock_in_type='采购入库',
                contract_id=None,
                supplier_id=supplier.id,
                operator='测试',
                total_quantity=10,
                total_amount=1000.00,
                estimated_amount=1000.00,
                actual_amount=1000.00,
                is_reconciled=False,
                is_initial=False,
                approval_status='approved',
                quality_status='qualified',
                dept_id=d.id,
                status='completed',
                created_at=datetime.now(),
                is_deleted=False,
            )
            db.session.add(stock_in)
        print(f"部门 {d.dept_name} (id={d.id}) 添加 2 条入库单")

    db.session.commit()
    total = StockIn.query.count()
    print(f"\n入库测试数据: {total} 条")
