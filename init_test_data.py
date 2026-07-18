"""
物资设备管理系统 - 测试数据初始化脚本
运行方式: python3 init_test_data.py
"""
import os
import sys
import random
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import (
    Project, Supplier, Material, Category, UsageUnit, WorkNumber,
    User, SysRole, SysDept, Contract, ContractItem, StockIn, StockInItem,
    StockOut, StockOutItem, Inventory, StockCheck, StockCheckItem,
    Reconciliation, ReconciliationItem, PaymentApplication, Payment,
    Equipment, TurnoverMaterial, TurnoverRecord, ConcreteTicket,
    PurchaseRequisition, PurchaseRequisitionItem,
    SysDictType, SysDictItem
)
from app.utils import init_dict_data, init_system_config

app = create_app()

def random_date(start_date, end_date):
    """生成随机日期"""
    delta = end_date - start_date
    random_days = random.randint(0, delta.days)
    return start_date + timedelta(days=random_days)

def create_basic_data():
    """创建基础测试数据"""
    with app.app_context():
        print("初始化系统配置和字典...")
        init_system_config()
        init_dict_data()
        
        print("创建测试部门...")
        depts = [
            {'code': 'HQ', 'name': '总部', 'parent_code': None},
            {'code': 'WH', 'name': '物资部', 'parent_code': 'HQ'},
            {'code': 'FN', 'name': '财务部', 'parent_code': 'HQ'},
            {'code': 'PR', 'name': '项目管理部', 'parent_code': 'HQ'},
        ]
        for d in depts:
            if not SysDept.query.filter_by(dept_code=d['code']).first():
                parent = SysDept.query.filter_by(dept_code=d['parent_code']).first() if d['parent_code'] else None
                dept = SysDept(dept_code=d['code'], dept_name=d['name'], parent_id=parent.id if parent else 0)
                db.session.add(dept)
        
        print("创建测试角色...")
        roles = [
            {'code': 'super_admin', 'name': '超级管理员', 'data_scope': 'all'},
            {'code': 'material_admin', 'name': '物资管理员', 'data_scope': 'dept'},
            {'code': 'finance', 'name': '财务', 'data_scope': 'dept'},
            {'code': 'viewer', 'name': '查看用户', 'data_scope': 'self'},
        ]
        for r in roles:
            if not SysRole.query.filter_by(role_code=r['code']).first():
                role = SysRole(role_code=r['code'], role_name=r['name'], data_scope=r['data_scope'])
                db.session.add(role)
        
        print("创建测试用户...")
        users = [
            {'username': 'admin', 'name': '管理员', 'role_code': 'super_admin', 'dept_code': 'HQ'},
            {'username': 'wuzhi', 'name': '物资员小王', 'role_code': 'material_admin', 'dept_code': 'WH'},
            {'username': 'caiwu', 'name': '财务小李', 'role_code': 'finance', 'dept_code': 'FN'},
        ]
        for u in users:
            if not User.query.filter_by(username=u['username']).first():
                role = SysRole.query.filter_by(role_code=u['role_code']).first()
                dept = SysDept.query.filter_by(dept_code=u['dept_code']).first()
                user = User(
                    username=u['username'],
                    password_hash=generate_password_hash('123456', method='pbkdf2:sha256'),
                    name=u['name'],
                    role='admin' if u['role_code'] == 'super_admin' else 'editor',
                    role_id=role.id if role else None,
                    dept_id=dept.id if dept else None
                )
                db.session.add(user)
        
        print("创建测试项目...")
        projects = [
            {'code': 'JCYY', 'name': '锦橙苑项目', 'address': '北京市朝阳区锦橙路88号'},
            {'code': 'SBZ', 'name': '孙八砦项目', 'address': '河南省郑州市二七区孙八砦街'},
        ]
        for p in projects:
            if not Project.query.filter_by(code=p['code']).first():
                project = Project(
                    code=p['code'],
                    name=p['name'],
                    address=p['address'],
                    start_date=datetime(2024, 1, 1)
                )
                db.session.add(project)
        
        db.session.commit()
        print("基础数据创建完成")

def create_material_data():
    """创建物资分类和常用材料"""
    with app.app_context():
        projects = Project.query.all()
        
        for project in projects:
            print(f"为项目 {project.name} 创建物资分类...")
            categories = [
                {'code': 'GC', 'name': '钢材', 'parent_code': None},
                {'code': 'JC', 'name': '建材', 'parent_code': None},
                {'code': 'QT', 'name': '其他', 'parent_code': None},
                {'code': 'GC-GJ', 'name': '钢筋', 'parent_code': 'GC'},
                {'code': 'GC-GG', 'name': '钢管', 'parent_code': 'GC'},
                {'code': 'JC-NM', 'name': '水泥', 'parent_code': 'JC'},
                {'code': 'JC-SG', 'name': '砂石', 'parent_code': 'JC'},
                {'code': 'GC-GJ-HRB400', 'name': 'HRB400钢筋', 'parent_code': 'GC-GJ'},
                {'code': 'JC-NM-PO425', 'name': 'PO42.5水泥', 'parent_code': 'JC-NM'},
            ]
            for c in categories:
                if not Category.query.filter_by(project_id=project.id, category_code=c['code']).first():
                    parent = Category.query.filter_by(project_id=project.id, category_code=c['parent_code']).first() if c['parent_code'] else None
                    cat = Category(project_id=project.id, category_code=c['code'], name=c['name'], parent_id=parent.id if parent else 0)
                    db.session.add(cat)
            
            print(f"为项目 {project.name} 创建常用材料...")
            materials = [
                {'code': 'HRB400-12', 'name': 'HRB400钢筋Φ12', 'specification': 'Φ12', 'unit': '吨', 'category_code': 'GC-GJ-HRB400', 'unit_price': 4850.00},
                {'code': 'HRB400-16', 'name': 'HRB400钢筋Φ16', 'specification': 'Φ16', 'unit': '吨', 'category_code': 'GC-GJ-HRB400', 'unit_price': 4780.00},
                {'code': 'HRB400-20', 'name': 'HRB400钢筋Φ20', 'specification': 'Φ20', 'unit': '吨', 'category_code': 'GC-GJ-HRB400', 'unit_price': 4750.00},
                {'code': 'HRB400-25', 'name': 'HRB400钢筋Φ25', 'specification': 'Φ25', 'unit': '吨', 'category_code': 'GC-GJ-HRB400', 'unit_price': 4720.00},
                {'code': 'GG-48', 'name': '钢管φ48×3.5', 'specification': 'φ48×3.5', 'unit': '吨', 'category_code': 'GC-GG', 'unit_price': 5200.00},
                {'code': 'PO425', 'name': 'PO42.5水泥', 'specification': '袋装50kg', 'unit': '吨', 'category_code': 'JC-NM-PO425', 'unit_price': 380.00},
                {'code': 'SHASHI', 'name': '砂石', 'specification': '中砂', 'unit': 'm³', 'category_code': 'JC-SG', 'unit_price': 120.00},
                {'code': 'SHUINI', 'name': '混凝土', 'specification': 'C30', 'unit': 'm³', 'category_code': 'QT', 'unit_price': 420.00},
                {'code': 'MUQI', 'name': '木模板', 'specification': '18mm', 'unit': 'm²', 'category_code': 'QT', 'unit_price': 65.00},
                {'code': 'DIANLAN', 'name': '电缆', 'specification': 'YJV-4×25', 'unit': '米', 'category_code': 'QT', 'unit_price': 85.00},
            ]
            for m in materials:
                if not Material.query.filter_by(project_id=project.id, code=m['code']).first():
                    cat = Category.query.filter_by(project_id=project.id, category_code=m['category_code']).first()
                    mat = Material(
                    project_id=project.id,
                    code=m['code'],
                    name=m['name'],
                    specification=m['specification'],
                    unit=m['unit'],
                    category_id=cat.id if cat else None
                )
                    db.session.add(mat)
            
            print(f"为项目 {project.name} 创建用料单位...")
            units = [
                {'code': 'BZ1', 'name': '一班', 'is_subcontractor': False},
                {'code': 'BZ2', 'name': '二班', 'is_subcontractor': False},
                {'code': 'BZ3', 'name': '三班', 'is_subcontractor': False},
                {'code': 'FB1', 'name': '分包单位A', 'is_subcontractor': True},
                {'code': 'FB2', 'name': '分包单位B', 'is_subcontractor': True},
                {'code': 'FB3', 'name': '分包单位C', 'is_subcontractor': True},
            ]
            for u in units:
                if not UsageUnit.query.filter_by(project_id=project.id, code=u['code']).first():
                    unit = UsageUnit(project_id=project.id, code=u['code'], name=u['name'], is_subcontractor=u['is_subcontractor'])
                    db.session.add(unit)
            
            print(f"为项目 {project.name} 创建工号...")
            work_numbers = [
                {'code': 'GH001', 'division_name': '土建', 'item_name': '1#楼基础工程'},
                {'code': 'GH002', 'division_name': '土建', 'item_name': '2#楼主体结构'},
                {'code': 'GH003', 'division_name': '土建', 'item_name': '地下车库'},
                {'code': 'GH004', 'division_name': '安装', 'item_name': '室外管网'},
                {'code': 'GH005', 'division_name': '装修', 'item_name': '装修工程'},
            ]
            for wn in work_numbers:
                if not WorkNumber.query.filter_by(project_id=project.id, code=wn['code']).first():
                    w = WorkNumber(project_id=project.id, code=wn['code'], division_name=wn['division_name'], item_name=wn['item_name'])
                    db.session.add(w)
        
        db.session.commit()
        print("物资数据创建完成")

def create_supplier_data():
    """创建供应商数据"""
    with app.app_context():
        projects = Project.query.all()
        
        for project in projects:
            print(f"为项目 {project.name} 创建供应商...")
            suppliers = [
                {'code': 'GY001', 'name': '北京钢铁贸易有限公司', 'contact_person': '张三', 'phone': '13800138001'},
                {'code': 'GY002', 'name': '河北建材集团', 'contact_person': '李四', 'phone': '13800138002'},
                {'code': 'GY003', 'name': '河南商砼有限公司', 'contact_person': '王五', 'phone': '13800138003'},
                {'code': 'GY004', 'name': '上海设备租赁公司', 'contact_person': '赵六', 'phone': '13800138004'},
                {'code': 'GY005', 'name': '广州周转材租赁站', 'contact_person': '孙七', 'phone': '13800138005'},
                {'code': 'GY006', 'name': '天津电缆厂', 'contact_person': '周八', 'phone': '13800138006'},
                {'code': 'GY007', 'name': '山东木材加工厂', 'contact_person': '吴九', 'phone': '13800138007'},
                {'code': 'GY008', 'name': '江苏防水材料公司', 'contact_person': '郑十', 'phone': '13800138008'},
            ]
            for s in suppliers:
                if not Supplier.query.filter_by(project_id=project.id, code=s['code']).first():
                    supplier = Supplier(
                        project_id=project.id,
                        code=s['code'],
                        name=s['name'],
                        contact_person=s['contact_person'],
                        phone=s['phone'],
                        opening_balance=random.uniform(0, 100000)
                    )
                    db.session.add(supplier)
        
        db.session.commit()
        print("供应商数据创建完成")

def create_contract_data():
    """创建采购合同数据"""
    with app.app_context():
        projects = Project.query.all()
        users = User.query.all()
        
        for project in projects:
            suppliers = Supplier.query.filter_by(project_id=project.id).all()
            materials = Material.query.filter_by(project_id=project.id).all()
            
            print(f"为项目 {project.name} 创建采购合同...")
            for i in range(5):
                code = f"HT-{project.code}-{i+1:03d}"
                if Contract.query.filter_by(code=code).first():
                    continue
                
                supplier = random.choice(suppliers)
                contract = Contract(
                    project_id=project.id,
                    code=code,
                    name=f"采购合同-{supplier.name}",
                    supplier_id=supplier.id,
                    contract_type='采购合同',
                    sign_date=random_date(datetime(2024, 1, 1), datetime(2024, 6, 30)),
                    amount_with_tax=0,
                    status='正常履约',
                    approval_status='passed'
                )
                db.session.add(contract)
                db.session.flush()
                
                item_count = random.randint(5, 10)
                total_amount = 0
                for j in range(item_count):
                    material = random.choice(materials)
                    quantity = random.uniform(10, 500)
                    unit_price = random.uniform(100, 10000)
                    amount = quantity * unit_price
                    total_amount += amount
                    
                    item = ContractItem(
                        contract_id=contract.id,
                        material_id=material.id,
                        quantity=quantity,
                        tax_rate=13,
                        unit_price_with_tax=unit_price,
                        amount_with_tax=amount
                    )
                    db.session.add(item)
                
                contract.amount_with_tax = total_amount
            
            print(f"为项目 {project.name} 创建采购申请...")
            for i in range(3):
                code = f"SQ-{project.code}-{i+1:03d}"
                if PurchaseRequisition.query.filter_by(pr_no=code).first():
                    continue
                
                status = random.choice(['draft', 'pending', 'passed'])
                req = PurchaseRequisition(
                    pr_no=code,
                    project_id=project.id,
                    apply_date=random_date(datetime(2024, 3, 1), datetime(2024, 9, 30)),
                    status=status
                )
                db.session.add(req)
                db.session.flush()
                
                item_count = random.randint(3, 6)
                for j in range(item_count):
                    material = random.choice(materials)
                    item = PurchaseRequisitionItem(
                        pr_id=req.id,
                        material_id=material.id,
                        material_name=material.name,
                        specification=material.specification,
                        unit=material.unit,
                        apply_qty=random.uniform(5, 100),
                        purpose='工程施工'
                    )
                    db.session.add(item)
        
        db.session.commit()
        print("合同数据创建完成")

def create_stock_data():
    """创建出入库数据"""
    with app.app_context():
        projects = Project.query.all()
        users = User.query.all()
        
        for project in projects:
            suppliers = Supplier.query.filter_by(project_id=project.id).all()
            materials = Material.query.filter_by(project_id=project.id).all()
            units = UsageUnit.query.filter_by(project_id=project.id).all()
            work_numbers = WorkNumber.query.filter_by(project_id=project.id).all()
            
            print(f"为项目 {project.name} 创建入库单...")
            stock_in_types = ['采购入库', '退库入库', '调拨入库', '期初入库']
            contracts = Contract.query.filter_by(project_id=project.id).all()
            
            for i in range(10):
                code = f"RK-{project.code}-{i+1:03d}"
                if StockIn.query.filter_by(code=code).first():
                    continue
                
                supplier = random.choice(suppliers)
                contract = random.choice(contracts) if contracts else None
                
                stock_in = StockIn(
                    project_id=project.id,
                    code=code,
                    supplier_id=supplier.id,
                    stock_in_type=random.choice(stock_in_types),
                    stock_in_date=random_date(datetime(2024, 2, 1), datetime(2024, 9, 30)),
                    approval_status='passed',
                    contract_id=contract.id if contract else None
                )
                db.session.add(stock_in)
                db.session.flush()
                
                item_count = random.randint(3, 8)
                total_amount = 0
                total_quantity = 0
                for j in range(item_count):
                    material = random.choice(materials)
                    quantity = random.uniform(5, 200)
                    unit_price = random.uniform(100, 10000)
                    amount = quantity * unit_price
                    total_amount += amount
                    total_quantity += quantity
                    
                    item = StockInItem(
                        stock_in_id=stock_in.id,
                        material_id=material.id,
                        quantity=quantity,
                        unit_price=unit_price,
                        amount=amount,
                        price_status='priced'
                    )
                    db.session.add(item)
                
                stock_in.total_amount = total_amount
                stock_in.total_quantity = total_quantity
            
            print(f"为项目 {project.name} 创建出库单...")
            stock_out_types = ['工程领用', '调拨出库', '退库出库', '报废出库']
            for i in range(10):
                code = f"CK-{project.code}-{i+1:03d}"
                if StockOut.query.filter_by(code=code).first():
                    continue
                
                usage_unit = random.choice(units)
                work_number = random.choice(work_numbers) if work_numbers else None
                
                stock_out = StockOut(
                    project_id=project.id,
                    code=code,
                    usage_unit_id=usage_unit.id,
                    work_number_id=work_number.id if work_number else None,
                    stock_out_type=random.choice(stock_out_types),
                    stock_out_date=random_date(datetime(2024, 3, 1), datetime(2024, 10, 31)),
                    approval_status='passed'
                )
                db.session.add(stock_out)
                db.session.flush()
                
                item_count = random.randint(3, 6)
                total_quantity = 0
                for j in range(item_count):
                    material = random.choice(materials)
                    quantity = random.uniform(1, 100)
                    
                    item = StockOutItem(
                        stock_out_id=stock_out.id,
                        material_id=material.id,
                        quantity=quantity
                    )
                    db.session.add(item)
                    total_quantity += quantity
                
                stock_out.total_quantity = total_quantity
        
        db.session.commit()
        print("出入库数据创建完成")

def create_other_data():
    """创建其他业务数据"""
    with app.app_context():
        projects = Project.query.all()
        users = User.query.all()
        
        for project in projects:
            materials = Material.query.filter_by(project_id=project.id).all()
            suppliers = Supplier.query.filter_by(project_id=project.id).all()
            
            print(f"为项目 {project.name} 创建库存盘点...")
            for i in range(1):
                check_no = f"PD-{project.code}-{i+1:03d}"
                if StockCheck.query.filter_by(check_no=check_no).first():
                    continue
                
                check = StockCheck(
                    check_no=check_no,
                    project_id=project.id,
                    check_date=random_date(datetime(2024, 6, 1), datetime(2024, 8, 31)),
                    check_type='full',
                    status='confirmed',
                    checker='张三'
                )
                db.session.add(check)
                db.session.flush()
                
                sample_materials = random.sample(materials, min(10, len(materials)))
                for mat in sample_materials:
                    book_qty = random.uniform(0, 500)
                    actual_qty = book_qty * random.uniform(0.98, 1.02)
                    item = StockCheckItem(
                        check_id=check.id,
                        material_id=mat.id,
                        material_name=mat.name,
                        specification=mat.specification,
                        unit=mat.unit,
                        book_qty=book_qty,
                        actual_qty=actual_qty
                    )
                    db.session.add(item)
            
            print(f"为项目 {project.name} 创建对账单...")
            contracts = Contract.query.filter_by(project_id=project.id).all()
            for i in range(2):
                code = f"DZ-{project.code}-{i+1:03d}"
                if Reconciliation.query.filter_by(code=code).first():
                    continue
                
                supplier = random.choice(suppliers)
                contract = random.choice(contracts) if contracts else None
                if not contract:
                    continue
                
                status = random.choice(['草稿', '对账中', '已确认'])
                recon = Reconciliation(
                    project_id=project.id,
                    code=code,
                    supplier_id=supplier.id,
                    contract_id=contract.id,
                    start_date=datetime(2024, 1, 1),
                    end_date=datetime(2024, 6, 30),
                    status=status,
                    approval_status='passed'
                )
                db.session.add(recon)
            
            print(f"为项目 {project.name} 创建设备...")
            equipments = [
                {'code': 'SB001', 'name': '塔式起重机', 'specification': 'QTZ80', 'source_type': 'self', 'status': 'in_use'},
                {'code': 'SB002', 'name': '施工电梯', 'specification': 'SC200', 'source_type': 'self', 'status': 'in_use'},
                {'code': 'SB003', 'name': '挖掘机', 'specification': 'PC200', 'source_type': 'rent', 'status': 'in_use'},
                {'code': 'SB004', 'name': '装载机', 'specification': 'ZL50', 'source_type': 'rent', 'status': 'repairing'},
                {'code': 'SB005', 'name': '压路机', 'specification': 'YZ18', 'source_type': 'self', 'status': 'idle'},
            ]
            for eq in equipments:
                if not Equipment.query.filter_by(project_id=project.id, code=eq['code']).first():
                    equipment = Equipment(
                        project_id=project.id,
                        code=eq['code'],
                        name=eq['name'],
                        specification=eq['specification'],
                        source_type=eq['source_type'],
                        status=eq['status'],
                        purchase_date=datetime(2023, 1, 1),
                        original_value=random.uniform(100000, 500000)
                    )
                    db.session.add(equipment)
            
            print(f"为项目 {project.name} 创建周转材...")
            turnovers = [
                {'code': 'ZZ001', 'name': '钢管', 'specification': 'φ48×3.5', 'unit': '吨', 'material_type': 'own'},
                {'code': 'ZZ002', 'name': '扣件', 'specification': '十字', 'unit': '个', 'material_type': 'own'},
                {'code': 'ZZ003', 'name': '脚手板', 'specification': '4m', 'unit': '块', 'material_type': 'own'},
                {'code': 'ZZ004', 'name': '工字钢', 'specification': 'I16', 'unit': '吨', 'material_type': 'own'},
                {'code': 'ZZ005', 'name': '安全网', 'specification': '1.8×6', 'unit': '张', 'material_type': 'own'},
            ]
            for t in turnovers:
                if not TurnoverMaterial.query.filter_by(project_id=project.id, code=t['code']).first():
                    tm = TurnoverMaterial(
                        project_id=project.id,
                        code=t['code'],
                        name=t['name'],
                        specification=t['specification'],
                        unit=t['unit'],
                        material_type=t['material_type']
                    )
                    db.session.add(tm)
            
            print(f"为项目 {project.name} 创建商砼小票...")
            work_numbers = WorkNumber.query.filter_by(project_id=project.id).all()
            for i in range(5):
                ticket_no = f"ST-{project.code}-{i+1:04d}"
                if ConcreteTicket.query.filter_by(ticket_no=ticket_no).first():
                    continue
                
                supplier = random.choice(suppliers)
                work_number = random.choice(work_numbers) if work_numbers else None
                
                ticket = ConcreteTicket(
                    project_id=project.id,
                    ticket_no=ticket_no,
                    supplier_id=supplier.id,
                    strength_grade='C30',
                    pour_part=f"1#楼{i+1}层",
                    work_number_id=work_number.id if work_number else None,
                    volume=random.uniform(8, 12),
                    arrival_time=random_date(datetime(2024, 3, 1), datetime(2024, 9, 30))
                )
                db.session.add(ticket)
        
        db.session.commit()
        print("其他业务数据创建完成")

if __name__ == '__main__':
    try:
        create_basic_data()
        create_material_data()
        create_supplier_data()
        create_contract_data()
        create_stock_data()
        create_other_data()
        print("=" * 60)
        print("测试数据初始化完成！")
        print("=" * 60)
    except Exception as e:
        print(f"初始化失败: {e}")
        import traceback
        traceback.print_exc()
        with app.app_context():
            db.session.rollback()