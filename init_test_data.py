"""
物资设备管理系统 - 全量测试数据初始化脚本
运行方式: python3 init_test_data.py

测试方案: 多级组织架构 + 差异化配置 + 全业务流程测试
"""
import os
import sys
import json
import random
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import (
    Project, Supplier, Material, Category, UsageUnit, WorkNumber, UnitTeam,
    User, SysRole, SysDept, SysUserProject,
    Contract, ContractItem, StockIn, StockInItem,
    StockOut, StockOutItem, Inventory,
    StockCheck, StockCheckItem,
    Reconciliation, ReconciliationItem, PaymentApplication, Payment, Invoice,
    Equipment, EquipmentMaintenance, EquipmentRentSettle,
    TurnoverMaterial, TurnoverRecord, TurnoverInventory,
    ConcreteTicket,
    PurchaseRequisition, PurchaseRequisitionItem,
    MaterialTransfer, MaterialTransferItem,
    ApprovalFlow, ApprovalNode, ApprovalBranch, ApprovalInstance, ApprovalRecord,
    SysDictType, SysDictItem,
    MaterialScrap, MaterialScrapItem
)
from app.utils import init_dict_data, init_system_config

app = create_app()

def random_date(start_date, end_date):
    delta = end_date - start_date
    random_days = random.randint(0, delta.days)
    return start_date + timedelta(days=random_days)

def create_org_structure():
    """创建多级组织架构"""
    with app.app_context():
        print("创建组织架构...")
        
        company = SysDept.query.filter_by(dept_code='ZZGS').first()
        if not company:
            company = SysDept(
                dept_code='ZZGS',
                dept_name='中国铁建中原建设总公司',
                parent_id=0,
                dept_type='company',
                sort=1
            )
            db.session.add(company)
            db.session.flush()
        
        hq = SysDept.query.filter_by(dept_code='HQ').first()
        if not hq:
            hq = SysDept(
                dept_code='HQ',
                dept_name='总公司机关',
                parent_id=company.id,
                dept_type='branch',
                sort=1
            )
            db.session.add(hq)
            db.session.flush()
        
        dept_wh = SysDept.query.filter_by(dept_code='WHB').first()
        if not dept_wh:
            dept_wh = SysDept(
                dept_code='WHB',
                dept_name='物资管理部',
                parent_id=hq.id,
                dept_type='dept',
                sort=1
            )
            db.session.add(dept_wh)
        
        dept_cw = SysDept.query.filter_by(dept_code='CWB').first()
        if not dept_cw:
            dept_cw = SysDept(
                dept_code='CWB',
                dept_name='财务管理部',
                parent_id=hq.id,
                dept_type='dept',
                sort=2
            )
            db.session.add(dept_cw)
        
        dept_zh = SysDept.query.filter_by(dept_code='ZHB').first()
        if not dept_zh:
            dept_zh = SysDept(
                dept_code='ZHB',
                dept_name='综合办公室',
                parent_id=hq.id,
                dept_type='dept',
                sort=3
            )
            db.session.add(dept_zh)
            db.session.flush()
        
        branch_zz = SysDept.query.filter_by(dept_code='ZZFB').first()
        if not branch_zz:
            branch_zz = SysDept(
                dept_code='ZZFB',
                dept_name='郑州分公司',
                parent_id=company.id,
                dept_type='branch',
                sort=2
            )
            db.session.add(branch_zz)
            db.session.flush()
        
        branch_ly = SysDept.query.filter_by(dept_code='LYFB').first()
        if not branch_ly:
            branch_ly = SysDept(
                dept_code='LYFB',
                dept_name='洛阳分公司',
                parent_id=company.id,
                dept_type='branch',
                sort=3
            )
            db.session.add(branch_ly)
            db.session.flush()
        
        db.session.commit()
        print("组织架构创建完成")
        
        return {
            'company': company,
            'hq': hq,
            'dept_wh': dept_wh,
            'dept_cw': dept_cw,
            'branch_zz': branch_zz,
            'branch_ly': branch_ly
        }

def create_projects(branches):
    """创建三个差异化配置的项目"""
    with app.app_context():
        print("创建测试项目...")
        
        branch_zz = SysDept.query.filter_by(dept_code='ZZFB').first()
        branch_ly = SysDept.query.filter_by(dept_code='LYFB').first()
        
        project_a = Project.query.filter_by(code='JCYY').first()
        if not project_a:
            project_a = Project(
                code='JCYY',
                name='锦橙苑项目部',
                address='郑州市高新区科学大道与春兰路交叉口',
                start_date=datetime(2024, 1, 1),
                planned_end_date=datetime(2025, 12, 31),
                manager='张建国',
                contact_phone='13800138000',
                building_area=80000,
                contract_amount=250000000,
                status='active',
                project_type='住宅'
            )
            project_a.set_module_config({
                'module_turnover': True,
                'module_equipment': True,
                'module_quality_check': True,
                'module_batch': True,
                'module_approval': True,
                'module_ai': True,
                'module_industry_tools': True,
                'module_scrap': True,
                'module_period_close': True,
                'module_subcontract': False
            })
            db.session.add(project_a)
        
        project_b = Project.query.filter_by(code='SBS').first()
        if not project_b:
            project_b = Project(
                code='SBS',
                name='孙八砦项目部',
                address='郑州市二七区孙八砦社区',
                start_date=datetime(2024, 3, 1),
                planned_end_date=datetime(2025, 6, 30),
                manager='李明辉',
                contact_phone='13900139000',
                building_area=50000,
                contract_amount=150000000,
                status='active',
                project_type='住宅'
            )
            project_b.set_module_config({
                'module_turnover': False,
                'module_equipment': False,
                'module_quality_check': False,
                'module_batch': False,
                'module_approval': False,
                'module_ai': False,
                'module_industry_tools': True,
                'module_scrap': True,
                'module_period_close': True,
                'module_subcontract': False
            })
            db.session.add(project_b)
        
        project_c = Project.query.filter_by(code='LYXM').first()
        if not project_c:
            project_c = Project(
                code='LYXM',
                name='洛阳项目部',
                address='洛阳市洛龙区开元大道与学府街交叉口',
                start_date=datetime(2024, 2, 1),
                planned_end_date=datetime(2026, 3, 31),
                manager='王志强',
                contact_phone='13700137000',
                building_area=60000,
                contract_amount=180000000,
                status='active',
                project_type='商业'
            )
            project_c.set_module_config({
                'module_turnover': False,
                'module_equipment': True,
                'module_quality_check': False,
                'module_batch': False,
                'module_approval': True,
                'module_ai': True,
                'module_industry_tools': True,
                'module_scrap': True,
                'module_period_close': True,
                'module_subcontract': False
            })
            db.session.add(project_c)
        db.session.flush()
        
        dept_jcy_wh = SysDept.query.filter_by(dept_code='JCY-WH').first()
        if not dept_jcy_wh:
            dept_jcy_wh = SysDept(
                dept_code='JCY-WH',
                dept_name='物资组',
                parent_id=branch_zz.id,
                dept_type='team',
                project_id=project_a.id,
                sort=1
            )
            db.session.add(dept_jcy_wh)
        
        dept_jcy_gc = SysDept.query.filter_by(dept_code='JCY-GC').first()
        if not dept_jcy_gc:
            dept_jcy_gc = SysDept(
                dept_code='JCY-GC',
                dept_name='工程组',
                parent_id=branch_zz.id,
                dept_type='team',
                project_id=project_a.id,
                sort=2
            )
            db.session.add(dept_jcy_gc)
        
        dept_jcy_cw = SysDept.query.filter_by(dept_code='JCY-CW').first()
        if not dept_jcy_cw:
            dept_jcy_cw = SysDept(
                dept_code='JCY-CW',
                dept_name='财务组',
                parent_id=branch_zz.id,
                dept_type='team',
                project_id=project_a.id,
                sort=3
            )
            db.session.add(dept_jcy_cw)
        
        dept_sbs_wh = SysDept.query.filter_by(dept_code='SBS-WH').first()
        if not dept_sbs_wh:
            dept_sbs_wh = SysDept(
                dept_code='SBS-WH',
                dept_name='物资组',
                parent_id=branch_zz.id,
                dept_type='team',
                project_id=project_b.id,
                sort=1
            )
            db.session.add(dept_sbs_wh)
        
        dept_sbs_gc = SysDept.query.filter_by(dept_code='SBS-GC').first()
        if not dept_sbs_gc:
            dept_sbs_gc = SysDept(
                dept_code='SBS-GC',
                dept_name='工程组',
                parent_id=branch_zz.id,
                dept_type='team',
                project_id=project_b.id,
                sort=2
            )
            db.session.add(dept_sbs_gc)
        
        dept_ly_wh = SysDept.query.filter_by(dept_code='LY-WH').first()
        if not dept_ly_wh:
            dept_ly_wh = SysDept(
                dept_code='LY-WH',
                dept_name='物资组',
                parent_id=branch_ly.id,
                dept_type='team',
                project_id=project_c.id,
                sort=1
            )
            db.session.add(dept_ly_wh)
        
        dept_ly_sb = SysDept.query.filter_by(dept_code='LY-SB').first()
        if not dept_ly_sb:
            dept_ly_sb = SysDept(
                dept_code='LY-SB',
                dept_name='设备组',
                parent_id=branch_ly.id,
                dept_type='team',
                project_id=project_c.id,
                sort=2
            )
            db.session.add(dept_ly_sb)
        
        db.session.commit()
        print("项目创建完成")
        
        return {
            'project_a': project_a,
            'project_b': project_b,
            'project_c': project_c,
            'dept_jcy_wh': dept_jcy_wh,
            'dept_jcy_gc': dept_jcy_gc,
            'dept_jcy_cw': dept_jcy_cw,
            'dept_sbs_wh': dept_sbs_wh,
            'dept_sbs_gc': dept_sbs_gc,
            'dept_ly_wh': dept_ly_wh,
            'dept_ly_sb': dept_ly_sb
        }

def create_roles():
    """创建角色"""
    with app.app_context():
        print("创建角色...")
        
        roles = [
            {'code': 'super_admin', 'name': '超级管理员', 'data_scope': 'all'},
            {'code': 'material_admin', 'name': '公司物资部长', 'data_scope': 'all'},
            {'code': 'material_manager', 'name': '项目经理', 'data_scope': 'dept_and_sub'},
            {'code': 'material_staff', 'name': '项目物资员', 'data_scope': 'dept'},
            {'code': 'editor', 'name': '普通录入员', 'data_scope': 'self'},
            {'code': 'finance_user', 'name': '财务', 'data_scope': 'all'},
            {'code': 'viewer', 'name': '查看用户', 'data_scope': 'self'},
        ]
        
        role_map = {}
        for r in roles:
            existing = SysRole.query.filter_by(role_code=r['code']).first()
            if existing:
                role_map[r['code']] = existing
            else:
                role = SysRole(role_code=r['code'], role_name=r['name'], data_scope=r['data_scope'])
                db.session.add(role)
                role_map[r['code']] = role
        
        db.session.commit()
        print("角色创建完成")
        return role_map

def create_users(roles, orgs, projects):
    """创建测试用户"""
    with app.app_context():
        print("创建测试用户...")
        
        users = []
        
        def add_user(username, name, role_code, dept_code, project_code=None, is_main=True):
            existing = User.query.filter_by(username=username).first()
            if existing:
                users.append({'username': username, 'name': name, 'role': role_code})
                return existing
            
            role = SysRole.query.filter_by(role_code=role_code).first()
            dept = SysDept.query.filter_by(dept_code=dept_code).first()
            project = None
            if project_code:
                project = Project.query.filter_by(code=project_code).first()
            
            user = User(
                username=username,
                password_hash=generate_password_hash('123456', method='pbkdf2:sha256'),
                name=name,
                role='admin' if role_code == 'super_admin' else 'editor',
                role_id=role.id if role else None,
                dept_id=dept.id if dept else None,
                data_scope=role.data_scope if role else 'self',
                can_view_amount=True
            )
            db.session.add(user)
            db.session.flush()
            
            if project:
                existing_up = SysUserProject.query.filter_by(user_id=user.id, project_id=project.id).first()
                if not existing_up:
                    up = SysUserProject(user_id=user.id, project_id=project.id, is_main=is_main)
                    db.session.add(up)
            
            users.append({'username': username, 'name': name, 'role': role_code})
            return user
        
        add_user('admin', '超级管理员', 'super_admin', 'ZZGS')
        add_user('wuzibuzhang', '公司物资部长', 'material_admin', 'WHB')
        add_user('caiwuzhuguan', '财务主管', 'finance_user', 'CWB')
        
        add_user('jcy_xmjl', '锦橙苑项目经理', 'material_manager', 'JCY-GC', 'JCYY')
        add_user('jcy_wzy', '锦橙苑物资员', 'material_staff', 'JCY-WH', 'JCYY')
        add_user('jcy_luruyuan', '锦橙苑录入员', 'editor', 'JCY-WH', 'JCYY')
        
        add_user('sbs_xmjl', '孙八砦项目经理', 'material_manager', 'SBS-GC', 'SBS')
        add_user('sbs_wzy', '孙八砦物资员', 'material_staff', 'SBS-WH', 'SBS')
        
        add_user('ly_xmjl', '洛阳项目经理', 'material_manager', 'LY-WH', 'LYXM')
        add_user('ly_wzy', '洛阳物资员', 'material_staff', 'LY-WH', 'LYXM')
        
        jianzhi_user = add_user('jianzhi_wzy', '兼职物资员', 'material_staff', 'JCY-WH', 'JCYY', is_main=True)
        project_b = Project.query.filter_by(code='SBS').first()
        existing_up = SysUserProject.query.filter_by(user_id=jianzhi_user.id, project_id=project_b.id).first()
        if not existing_up:
            db.session.add(SysUserProject(user_id=jianzhi_user.id, project_id=project_b.id, is_main=False))
        
        db.session.commit()
        print("用户创建完成")
        
        print("\n=== 测试账号清单 ===")
        for u in users:
            print(f"账号: {u['username']} | 姓名: {u['name']} | 角色: {u['role']} | 密码: 123456")
        print("==================")
        
        return users

def create_company_materials(company_dept):
    """创建公司级主数据（物资分类、物资主库、供应商）"""
    with app.app_context():
        print("创建公司级主数据...")
        
        default_project = Project.query.filter_by(code='JCYY').first()
        
        if not default_project:
            default_project = Project.query.first()
            if not default_project:
                print("错误：没有项目")
                return
        
        print("创建物资分类...")
        categories = [
            {'code': 'GC', 'name': '钢材', 'parent_code': None, 'level': 1},
            {'code': 'JC', 'name': '建材', 'parent_code': None, 'level': 1},
            {'code': 'QT', 'name': '其他', 'parent_code': None, 'level': 1},
            {'code': 'GC-GJ', 'name': '钢筋', 'parent_code': 'GC', 'level': 2},
            {'code': 'GC-GG', 'name': '钢管', 'parent_code': 'GC', 'level': 2},
            {'code': 'GC-GD', 'name': '钢板', 'parent_code': 'GC', 'level': 2},
            {'code': 'JC-NM', 'name': '水泥', 'parent_code': 'JC', 'level': 2},
            {'code': 'JC-SG', 'name': '砂石', 'parent_code': 'JC', 'level': 2},
            {'code': 'JC-TC', 'name': '涂料', 'parent_code': 'JC', 'level': 2},
            {'code': 'QT-SHT', 'name': '商砼', 'parent_code': 'QT', 'level': 2},
            {'code': 'QT-WJ', 'name': '五金', 'parent_code': 'QT', 'level': 2},
            {'code': 'QT-DL', 'name': '电力', 'parent_code': 'QT', 'level': 2},
            {'code': 'GC-GJ-HRB400', 'name': 'HRB400钢筋', 'parent_code': 'GC-GJ', 'level': 3},
            {'code': 'GC-GJ-HRB500', 'name': 'HRB500钢筋', 'parent_code': 'GC-GJ', 'level': 3},
            {'code': 'JC-NM-PO425', 'name': 'PO42.5水泥', 'parent_code': 'JC-NM', 'level': 3},
            {'code': 'JC-NM-PO525', 'name': 'PO52.5水泥', 'parent_code': 'JC-NM', 'level': 3},
        ]
        
        cat_map = {}
        for c in categories:
            parent = cat_map.get(c['parent_code']) if c['parent_code'] else None
            cat = Category(
                project_id=default_project.id,
                category_code=c['code'],
                name=c['name'],
                parent_id=parent.id if parent else 0,
                level=c['level'],
                source='company'
            )
            db.session.add(cat)
            db.session.flush()
            cat_map[c['code']] = cat
        
        print("创建物资主库...")
        materials = [
            {'code': 'HRB400-12', 'name': 'HRB400钢筋Φ12', 'spec': 'Φ12', 'unit': '吨', 'cat': 'GC-GJ-HRB400', 'price': 4850},
            {'code': 'HRB400-14', 'name': 'HRB400钢筋Φ14', 'spec': 'Φ14', 'unit': '吨', 'cat': 'GC-GJ-HRB400', 'price': 4820},
            {'code': 'HRB400-16', 'name': 'HRB400钢筋Φ16', 'spec': 'Φ16', 'unit': '吨', 'cat': 'GC-GJ-HRB400', 'price': 4780},
            {'code': 'HRB400-18', 'name': 'HRB400钢筋Φ18', 'spec': 'Φ18', 'unit': '吨', 'cat': 'GC-GJ-HRB400', 'price': 4760},
            {'code': 'HRB400-20', 'name': 'HRB400钢筋Φ20', 'spec': 'Φ20', 'unit': '吨', 'cat': 'GC-GJ-HRB400', 'price': 4750},
            {'code': 'HRB400-22', 'name': 'HRB400钢筋Φ22', 'spec': 'Φ22', 'unit': '吨', 'cat': 'GC-GJ-HRB400', 'price': 4730},
            {'code': 'HRB400-25', 'name': 'HRB400钢筋Φ25', 'spec': 'Φ25', 'unit': '吨', 'cat': 'GC-GJ-HRB400', 'price': 4720},
            {'code': 'HRB500-12', 'name': 'HRB500钢筋Φ12', 'spec': 'Φ12', 'unit': '吨', 'cat': 'GC-GJ-HRB500', 'price': 5200},
            {'code': 'HRB500-16', 'name': 'HRB500钢筋Φ16', 'spec': 'Φ16', 'unit': '吨', 'cat': 'GC-GJ-HRB500', 'price': 5150},
            {'code': 'HRB500-20', 'name': 'HRB500钢筋Φ20', 'spec': 'Φ20', 'unit': '吨', 'cat': 'GC-GJ-HRB500', 'price': 5120},
            {'code': 'GG-48', 'name': '钢管φ48×3.5', 'spec': 'φ48×3.5', 'unit': '吨', 'cat': 'GC-GG', 'price': 5200},
            {'code': 'GG-60', 'name': '钢管φ60×3.5', 'spec': 'φ60×3.5', 'unit': '吨', 'cat': 'GC-GG', 'price': 5100},
            {'code': 'GB-6', 'name': '钢板6mm', 'spec': '6mm', 'unit': '吨', 'cat': 'GC-GD', 'price': 4500},
            {'code': 'GB-8', 'name': '钢板8mm', 'spec': '8mm', 'unit': '吨', 'cat': 'GC-GD', 'price': 4450},
            {'code': 'PO425', 'name': 'PO42.5水泥', 'spec': '袋装50kg', 'unit': '吨', 'cat': 'JC-NM-PO425', 'price': 380},
            {'code': 'PO525', 'name': 'PO52.5水泥', 'spec': '袋装50kg', 'unit': '吨', 'cat': 'JC-NM-PO525', 'price': 420},
            {'code': 'SHASHI-ZS', 'name': '中砂', 'spec': '细度模数2.3-3.0', 'unit': 'm³', 'cat': 'JC-SG', 'price': 120},
            {'code': 'SHASHI-XS', 'name': '细砂', 'spec': '细度模数1.6-2.2', 'unit': 'm³', 'cat': 'JC-SG', 'price': 100},
            {'code': 'SHI-ZL', 'name': '碎石', 'spec': '5-20mm', 'unit': 'm³', 'cat': 'JC-SG', 'price': 110},
            {'code': 'SHI-DL', 'name': '砾石', 'spec': '20-40mm', 'unit': 'm³', 'cat': 'JC-SG', 'price': 105},
            {'code': 'TL-TL', 'name': '外墙涂料', 'spec': '弹性丙烯酸', 'unit': '桶', 'cat': 'JC-TC', 'price': 450},
            {'code': 'TL-NQ', 'name': '内墙涂料', 'spec': '环保乳胶漆', 'unit': '桶', 'cat': 'JC-TC', 'price': 380},
            {'code': 'SHT-C30', 'name': 'C30商砼', 'spec': '泵送', 'unit': 'm³', 'cat': 'QT-SHT', 'price': 420},
            {'code': 'SHT-C35', 'name': 'C35商砼', 'spec': '泵送', 'unit': 'm³', 'cat': 'QT-SHT', 'price': 450},
            {'code': 'SHT-C40', 'name': 'C40商砼', 'spec': '泵送', 'unit': 'm³', 'cat': 'QT-SHT', 'price': 480},
            {'code': 'WJ-DING', 'name': '膨胀螺栓', 'spec': 'M10×100', 'unit': '个', 'cat': 'QT-WJ', 'price': 2.5},
            {'code': 'WJ-MU', 'name': '木螺丝', 'spec': '4×30', 'unit': '盒', 'cat': 'QT-WJ', 'price': 8},
            {'code': 'WJ-M12', 'name': '螺栓螺母M12', 'spec': 'M12', 'unit': '套', 'cat': 'QT-WJ', 'price': 5},
            {'code': 'DL-DL', 'name': '电缆YJV-4×25', 'spec': 'YJV-4×25', 'unit': '米', 'cat': 'QT-DL', 'price': 85},
            {'code': 'DL-DL10', 'name': '电缆YJV-4×10', 'spec': 'YJV-4×10', 'unit': '米', 'cat': 'QT-DL', 'price': 45},
            {'code': 'DL-DG', 'name': '电线BV-2.5', 'spec': 'BV-2.5mm²', 'unit': '米', 'cat': 'QT-DL', 'price': 3.5},
            {'code': 'DL-DG4', 'name': '电线BV-4', 'spec': 'BV-4mm²', 'unit': '米', 'cat': 'QT-DL', 'price': 5},
            {'code': 'MUQI-MB', 'name': '木模板', 'spec': '18mm', 'unit': 'm²', 'cat': 'QT', 'price': 65},
            {'code': 'MUQI-FG', 'name': '方木', 'spec': '50×100', 'unit': 'm³', 'cat': 'QT', 'price': 1200},
            {'code': 'JIANLI', 'name': '脚手架', 'spec': '门式', 'unit': '套', 'cat': 'QT', 'price': 800},
            {'code': 'ANQUANWANG', 'name': '安全网', 'spec': '1.8×6m', 'unit': '张', 'cat': 'QT', 'price': 35},
            {'code': 'SHUIJING', 'name': '止水带', 'spec': '300×6', 'unit': '米', 'cat': 'QT', 'price': 25},
            {'code': 'FANGSHUI', 'name': '防水涂料', 'spec': 'JS聚合物', 'unit': '桶', 'cat': 'QT', 'price': 320},
            {'code': 'BANMU', 'name': '脚手板', 'spec': '4m', 'unit': '块', 'cat': 'QT', 'price': 50},
            {'code': 'GANGJIA', 'name': '钢支撑', 'spec': 'φ48', 'unit': '吨', 'cat': 'QT', 'price': 4800},
        ]
        
        mat_map = {}
        for m in materials:
            cat = cat_map.get(m['cat'])
            mat = Material(
                project_id=default_project.id,
                category_id=cat.id if cat else None,
                code=m['code'],
                name=m['name'],
                specification=m['spec'],
                unit=m['unit'],
                source='company'
            )
            db.session.add(mat)
            db.session.flush()
            mat_map[m['code']] = mat
        
        print("创建供应商主库...")
        suppliers = [
            {'code': 'GY001', 'name': '河南钢铁贸易有限公司', 'contact': '张三', 'phone': '13800138001', 'bank': '工商银行', 'account': '6222021234567890'},
            {'code': 'GY002', 'name': '郑州建材集团', 'contact': '李四', 'phone': '13800138002', 'bank': '建设银行', 'account': '6227001234567890'},
            {'code': 'GY003', 'name': '河南商砼有限公司', 'contact': '王五', 'phone': '13800138003', 'bank': '农业银行', 'account': '6228481234567890'},
            {'code': 'GY004', 'name': '郑州设备租赁公司', 'contact': '赵六', 'phone': '13800138004', 'bank': '中国银行', 'account': '6217851234567890'},
            {'code': 'GY005', 'name': '河南周转材租赁站', 'contact': '孙七', 'phone': '13800138005', 'bank': '交通银行', 'account': '6222621234567890'},
            {'code': 'GY006', 'name': '天津电缆厂', 'contact': '周八', 'phone': '13800138006', 'bank': '招商银行', 'account': '6225881234567890'},
            {'code': 'GY007', 'name': '山东木材加工厂', 'contact': '吴九', 'phone': '13800138007', 'bank': '浦发银行', 'account': '6225261234567890'},
            {'code': 'GY008', 'name': '江苏防水材料公司', 'contact': '郑十', 'phone': '13800138008', 'bank': '民生银行', 'account': '6226221234567890'},
            {'code': 'GY009', 'name': '河北砂石供应站', 'contact': '钱十一', 'phone': '13800138009', 'bank': '光大银行', 'account': '6226691234567890'},
            {'code': 'GY010', 'name': '广东涂料有限公司', 'contact': '刘十二', 'phone': '13800138010', 'bank': '兴业银行', 'account': '6229091234567890'},
        ]
        
        sup_map = {}
        for s in suppliers:
            sup = Supplier(
                project_id=default_project.id,
                code=s['code'],
                name=s['name'],
                contact_person=s['contact'],
                phone=s['phone'],
                bank_name=s['bank'],
                bank_account=s['account'],
                source='company',
                status='qualified'
            )
            db.session.add(sup)
            db.session.flush()
            sup_map[s['code']] = sup
        
        db.session.commit()
        print("公司级主数据创建完成")
        return {'categories': cat_map, 'materials': mat_map, 'suppliers': sup_map}

def create_project_data(project_code):
    """创建项目级常用数据和业务测试数据"""
    with app.app_context():
        project = Project.query.filter_by(code=project_code).first()
        if not project:
            print(f"项目 {project_code} 不存在")
            return
        
        print(f"为项目 {project.name} 创建数据...")
        
        project_materials = Material.query.filter_by(source='company').all()
        project_suppliers = Supplier.query.all()
        
        print(f"  创建项目常用物资...")
        if project.code == 'JCYY':
            common_mats = project_materials[:30]
        elif project.code == 'SBS':
            common_mats = project_materials[:15]
        else:
            common_mats = project_materials[:20]
        
        print(f"  创建项目常用供应商...")
        if project.code == 'JCYY':
            common_sups = project_suppliers[:8]
        elif project.code == 'SBS':
            common_sups = project_suppliers[:5]
        else:
            common_sups = project_suppliers[:6]
        
        print(f"  创建用料单位...")
        units = [
            {'code': f'{project.code}-BZ1', 'name': '施工一班', 'is_subcontractor': False},
            {'code': f'{project.code}-BZ2', 'name': '施工二班', 'is_subcontractor': False},
            {'code': f'{project.code}-FB1', 'name': '分包单位A', 'is_subcontractor': True},
            {'code': f'{project.code}-FB2', 'name': '分包单位B', 'is_subcontractor': True},
        ]
        
        unit_map = {}
        for u in units:
            existing = UsageUnit.query.filter_by(code=u['code']).first()
            if existing:
                unit = existing
            else:
                unit = UsageUnit(project_id=project.id, code=u['code'], name=u['name'], is_subcontractor=u['is_subcontractor'])
                db.session.add(unit)
                db.session.flush()
            unit_map[u['code']] = unit
            
            if not UnitTeam.query.filter_by(unit_id=unit.id, team_name='一组').first():
                team1 = UnitTeam(unit_id=unit.id, team_name='一组', picker_name='张三', phone='13800138101')
                db.session.add(team1)
            if not UnitTeam.query.filter_by(unit_id=unit.id, team_name='二组').first():
                team2 = UnitTeam(unit_id=unit.id, team_name='二组', picker_name='李四', phone='13800138102')
                db.session.add(team2)
        
        print(f"  创建工号...")
        work_numbers = [
            {'code': f'{project.code}-GH001', 'division': '土建', 'item': '1#楼基础工程'},
            {'code': f'{project.code}-GH002', 'division': '土建', 'item': '2#楼主体结构'},
            {'code': f'{project.code}-GH003', 'division': '土建', 'item': '地下车库'},
            {'code': f'{project.code}-GH004', 'division': '安装', 'item': '室外管网'},
            {'code': f'{project.code}-GH005', 'division': '装修', 'item': '装修工程'},
        ]
        
        wn_map = {}
        for wn in work_numbers:
            w = WorkNumber(project_id=project.id, code=wn['code'], division_name=wn['division'], item_name=wn['item'])
            db.session.add(w)
            db.session.flush()
            wn_map[wn['code']] = w
        
        print(f"  创建采购合同...")
        for i in range(3):
            code = f"HT-{project.code}-{i+1:03d}"
            supplier = random.choice(common_sups)
            contract = Contract(
                project_id=project.id,
                code=code,
                name=f"{supplier.name}采购合同",
                supplier_id=supplier.id,
                contract_type='采购合同',
                business_type='材料采购',
                procurement_method='议标',
                sign_date=random_date(datetime(2024, 1, 1), datetime(2024, 6, 30)),
                amount_with_tax=0,
                tax_rate=13,
                status='正常履约',
                approval_status='passed'
            )
            db.session.add(contract)
            db.session.flush()
            
            item_count = random.randint(5, 10)
            total_amount = 0
            for j in range(item_count):
                material = random.choice(common_mats)
                quantity = random.uniform(10, 500)
                unit_price = random.uniform(100, 10000)
                amount = quantity * unit_price
                total_amount += amount
                
                from app.utils import calc_unit_price_without_tax
                item = ContractItem(
                    contract_id=contract.id,
                    material_id=material.id,
                    quantity=quantity,
                    tax_rate=13,
                    unit_price_with_tax=unit_price,
                    unit_price_without_tax=calc_unit_price_without_tax(unit_price, 13),
                    amount_with_tax=amount
                )
                db.session.add(item)
            
            contract.amount_with_tax = total_amount
        
        print(f"  创建入库单...")
        stock_in_types = ['采购入库', '退库入库', '盘盈入库', '调拨入库']
        contracts = Contract.query.filter_by(project_id=project.id).all()
        
        for i in range(10):
            code = f"RK-{project.code}-{i+1:03d}"
            supplier = random.choice(common_sups)
            contract = random.choice(contracts) if contracts else None
            
            stock_in = StockIn(
                project_id=project.id,
                code=code,
                supplier_id=supplier.id,
                stock_in_type=random.choice(stock_in_types),
                stock_in_date=random_date(datetime(2024, 2, 1), datetime(2024, 10, 31)),
                approval_status='passed',
                quality_status='passed' if project.is_module_enabled('module_quality_check') else 'draft',
                contract_id=contract.id if contract else None
            )
            db.session.add(stock_in)
            db.session.flush()
            
            item_count = random.randint(3, 6)
            total_amount = 0
            total_quantity = 0
            for j in range(item_count):
                material = random.choice(common_mats)
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
        
        print(f"  创建出库单...")
        stock_out_types = ['工程领用', '调拨出库', '退库出库', '报废出库']
        for i in range(10):
            code = f"CK-{project.code}-{i+1:03d}"
            usage_unit = random.choice(list(unit_map.values()))
            work_number = random.choice(list(wn_map.values()))
            
            stock_out = StockOut(
                project_id=project.id,
                code=code,
                usage_unit_id=usage_unit.id,
                work_number_id=work_number.id,
                stock_out_type=random.choice(stock_out_types),
                stock_out_date=random_date(datetime(2024, 3, 1), datetime(2024, 11, 30)),
                approval_status='passed'
            )
            db.session.add(stock_out)
            db.session.flush()
            
            item_count = random.randint(3, 6)
            total_quantity = 0
            for j in range(item_count):
                material = random.choice(common_mats)
                quantity = random.uniform(1, 100)
                
                item = StockOutItem(
                    stock_out_id=stock_out.id,
                    material_id=material.id,
                    quantity=quantity
                )
                db.session.add(item)
                total_quantity += quantity
            
            stock_out.total_quantity = total_quantity
        
        print(f"  创建库存盘点...")
        check_no = f"PD-{project.code}-001"
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
        
        sample_materials = random.sample(common_mats, min(10, len(common_mats)))
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
        
        print(f"  创建对账单...")
        contracts = Contract.query.filter_by(project_id=project.id).all()
        for i in range(2):
            code = f"DZ-{project.code}-{i+1:03d}"
            supplier = random.choice(common_sups)
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
        
        print(f"  创建付款申请...")
        for i in range(2):
            code = f"SK-{project.code}-{i+1:03d}"
            supplier = random.choice(common_sups)
            contract = random.choice(contracts) if contracts else None
            if not contract:
                continue
            
            payment_app = PaymentApplication(
                project_id=project.id,
                application_code=code,
                apply_date=random_date(datetime(2024, 4, 1), datetime(2024, 10, 31)),
                applicant_name='张三',
                supplier_id=supplier.id,
                contract_id=contract.id,
                apply_amount=random.uniform(50000, 500000),
                payment_method='银行转账',
                status='approved',
                approval_status='passed'
            )
            db.session.add(payment_app)
        
        print(f"  创建发票...")
        for i in range(3):
            contract = random.choice(contracts) if contracts else None
            if not contract:
                continue
            
            invoice = Invoice(
                project_id=project.id,
                contract_id=contract.id,
                supplier_id=contract.supplier_id,
                invoice_code=f"FP{i+1:8d}",
                invoice_number=f"FP{i+1:8d}",
                invoice_date=random_date(datetime(2024, 3, 1), datetime(2024, 10, 31)),
                amount_with_tax=random.uniform(20000, 200000),
                tax_rate=13
            )
            db.session.add(invoice)
        
        print(f"  创建付款台账...")
        for i in range(2):
            contract = random.choice(contracts) if contracts else None
            if not contract:
                continue
            
            payment = Payment(
                project_id=project.id,
                contract_id=contract.id,
                supplier_id=contract.supplier_id,
                payment_code=f"FK-{project.code}-{i+1:03d}",
                payment_date=random_date(datetime(2024, 5, 1), datetime(2024, 11, 30)),
                amount=random.uniform(50000, 300000),
                method='银行转账',
                approval_status='passed'
            )
            db.session.add(payment)
        
        print(f"  创建采购申请...")
        for i in range(3):
            code = f"SQ-{project.code}-{i+1:03d}"
            status = random.choice(['draft', 'pending', 'passed'])
            req = PurchaseRequisition(
                pr_no=code,
                project_id=project.id,
                apply_date=random_date(datetime(2024, 3, 1), datetime(2024, 10, 31)),
                status=status
            )
            db.session.add(req)
            db.session.flush()
            
            item_count = random.randint(3, 6)
            for j in range(item_count):
                material = random.choice(common_mats)
                item = PurchaseRequisitionItem(
                    pr_id=req.id,
                    material_id=material.id,
                    material_name=material.name,
                    specification=material.specification,
                    unit=material.unit,
                    apply_qty=random.uniform(5, 100),
                    approve_qty=random.uniform(0, 100),
                    purpose='工程施工'
                )
                db.session.add(item)
        
        if project.is_module_enabled('module_equipment'):
            print(f"  创建设备...")
            equipments = [
                {'code': f'SB{project.code}01', 'name': '塔式起重机', 'spec': 'QTZ80', 'source': 'self', 'status': 'in_use', 'value': 800000},
                {'code': f'SB{project.code}02', 'name': '施工电梯', 'spec': 'SC200', 'source': 'self', 'status': 'in_use', 'value': 400000},
                {'code': f'SB{project.code}03', 'name': '挖掘机', 'spec': 'PC200', 'source': 'rent', 'status': 'in_use', 'value': 0},
                {'code': f'SB{project.code}04', 'name': '装载机', 'spec': 'ZL50', 'source': 'rent', 'status': 'repairing', 'value': 0},
                {'code': f'SB{project.code}05', 'name': '压路机', 'spec': 'YZ18', 'source': 'self', 'status': 'idle', 'value': 350000},
            ]
            for eq in equipments:
                equipment = Equipment(
                    project_id=project.id,
                    code=eq['code'],
                    name=eq['name'],
                    specification=eq['spec'],
                    source_type=eq['source'],
                    status=eq['status'],
                    purchase_date=datetime(2023, 1, 1),
                    original_value=eq['value'],
                    use_years=10,
                    supplier_id=common_sups[0].id if eq['source'] == 'rent' else None,
                    rent_unit_price=50000 if eq['source'] == 'rent' else 0
                )
                db.session.add(equipment)
                db.session.flush()
                
                if eq['source'] == 'self':
                    for j in range(random.randint(1, 2)):
                        maintenance = EquipmentMaintenance(
                            equipment_id=equipment.id,
                            maintain_date=random_date(datetime(2024, 1, 1), datetime(2024, 10, 31)),
                            maintain_type='保养',
                            content='定期保养',
                            cost=random.uniform(1000, 5000),
                            operator='李四'
                        )
                        db.session.add(maintenance)
                
                if eq['source'] == 'rent':
                    settle = EquipmentRentSettle(
                        settle_no=f"YD-{project.code}-{eq['code']}-001",
                        equipment_id=equipment.id,
                        supplier_id=common_sups[0].id,
                        settle_period='2024年7月',
                        settle_start_date=datetime(2024, 7, 1),
                        settle_end_date=datetime(2024, 7, 31),
                        unit_price=50000,
                        quantity=1,
                        rent_amount=50000,
                        status='confirmed'
                    )
                    db.session.add(settle)
        
        if project.is_module_enabled('module_turnover'):
            print(f"  创建周转材...")
            turnovers = [
                {'code': f'ZZ{project.code}01', 'name': '钢管', 'spec': 'φ48×3.5', 'unit': '吨', 'type': 'own', 'price': 5200},
                {'code': f'ZZ{project.code}02', 'name': '扣件', 'spec': '十字', 'unit': '个', 'type': 'own', 'price': 5},
                {'code': f'ZZ{project.code}03', 'name': '脚手板', 'spec': '4m', 'unit': '块', 'type': 'own', 'price': 50},
                {'code': f'ZZ{project.code}04', 'name': '工字钢', 'spec': 'I16', 'unit': '吨', 'type': 'own', 'price': 4800},
                {'code': f'ZZ{project.code}05', 'name': '安全网', 'spec': '1.8×6', 'unit': '张', 'type': 'own', 'price': 35},
            ]
            for t in turnovers:
                tm = TurnoverMaterial(
                    project_id=project.id,
                    code=t['code'],
                    name=t['name'],
                    specification=t['spec'],
                    unit=t['unit'],
                    material_type=t['type'],
                    rental_price=t['price'],
                    original_value=t['price'] * 100 if t['type'] == 'own' else 0
                )
                db.session.add(tm)
                db.session.flush()
                
                ti = TurnoverInventory(project_id=project.id, material_id=tm.id, quantity=100)
                db.session.add(ti)
                
                for j in range(3):
                    record = TurnoverRecord(
                        project_id=project.id,
                        material_id=tm.id,
                        team='施工一班',
                        qty=random.uniform(10, 50),
                        out_date=random_date(datetime(2024, 3, 1), datetime(2024, 10, 31)),
                        status='in_use'
                    )
                    db.session.add(record)
        
        print(f"  创建商砼小票...")
        for i in range(15):
            ticket = ConcreteTicket(
                project_id=project.id,
                ticket_no=f"ST-{project.code}-{i+1:04d}",
                supplier_id=common_sups[2].id if len(common_sups) > 2 else common_sups[0].id,
                strength_grade='C30',
                pour_part=random.choice(list(wn_map.keys())) + '浇筑',
                volume=random.uniform(5, 30),
                arrival_time=random_date(datetime(2024, 3, 1), datetime(2024, 11, 30)),
                vehicle_count=1,
                vehicle_no=f"豫A{i+1:4d}",
                driver_name='司机' + str(i+1)
            )
            db.session.add(ticket)
        
        db.session.commit()
        print(f"项目 {project.name} 数据创建完成")

def create_approval_flows():
    """创建审批流程（公司级默认+项目级简化）"""
    with app.app_context():
        print("创建审批流程...")
        
        project_a = Project.query.filter_by(code='JCYY').first()
        project_c = Project.query.filter_by(code='LYXM').first()
        
        stockin_company_flow = ApprovalFlow(
            flow_code='stockin_company',
            flow_name='入库审批-公司默认',
            biz_type='stockin',
            enabled=True,
            scope='company',
            is_default=True
        )
        db.session.add(stockin_company_flow)
        db.session.flush()
        
        ApprovalNode(flow_id=stockin_company_flow.id, node_order=1, node_name='项目物资员', approve_type='role', approve_role='material_staff')
        ApprovalNode(flow_id=stockin_company_flow.id, node_order=2, node_name='项目经理', approve_type='role', approve_role='material_manager')
        ApprovalNode(flow_id=stockin_company_flow.id, node_order=3, node_name='公司物资部长', approve_type='role', approve_role='material_admin')
        
        contract_company_flow = ApprovalFlow(
            flow_code='contract_company',
            flow_name='合同审批-公司默认',
            biz_type='contract',
            enabled=True,
            scope='company',
            is_default=True
        )
        db.session.add(contract_company_flow)
        db.session.flush()
        
        ApprovalNode(flow_id=contract_company_flow.id, node_order=1, node_name='项目物资员', approve_type='role', approve_role='material_staff')
        ApprovalNode(flow_id=contract_company_flow.id, node_order=2, node_name='项目经理', approve_type='role', approve_role='material_manager')
        ApprovalNode(flow_id=contract_company_flow.id, node_order=3, node_name='公司物资部长', approve_type='role', approve_role='material_admin')
        ApprovalNode(flow_id=contract_company_flow.id, node_order=4, node_name='财务主管', approve_type='role', approve_role='finance_user')
        
        if project_c:
            stockin_project_c_flow = ApprovalFlow(
                flow_code='stockin_project_c',
                flow_name='入库审批-洛阳项目简化',
                biz_type='stockin',
                enabled=True,
                scope='project',
                project_ids=json.dumps([project_c.id])
            )
            db.session.add(stockin_project_c_flow)
            db.session.flush()
            
            ApprovalNode(flow_id=stockin_project_c_flow.id, node_order=1, node_name='项目物资员', approve_type='role', approve_role='material_staff')
            ApprovalNode(flow_id=stockin_project_c_flow.id, node_order=2, node_name='项目经理', approve_type='role', approve_role='material_manager')
        
        db.session.commit()
        print("审批流程创建完成")

def create_inter_project_transfer():
    """创建项目间调拨"""
    with app.app_context():
        project_a = Project.query.filter_by(code='JCYY').first()
        project_c = Project.query.filter_by(code='LYXM').first()
        
        if project_a and project_c:
            print("创建项目间调拨...")
            materials_a = Material.query.filter_by(project_id=project_a.id).limit(3).all()
            
            transfer = MaterialTransfer(
                transfer_no='DB-2024001',
                from_project_id=project_a.id,
                to_project_id=project_c.id,
                transfer_date=datetime(2024, 8, 15),
                status='completed',
                applicant='张三',
                handler='李四'
            )
            db.session.add(transfer)
            db.session.flush()
            
            for mat in materials_a[:2]:
                item = MaterialTransferItem(
                    transfer_id=transfer.id,
                    material_id=mat.id,
                    material_name=mat.name,
                    specification=mat.specification,
                    unit=mat.unit,
                    transfer_qty=10,
                    out_qty=10,
                    in_qty=10
                )
                db.session.add(item)
            
            db.session.commit()
            print("项目间调拨创建完成")

def create_scrap_data():
    """创建报废数据"""
    with app.app_context():
        projects = Project.query.all()
        for project in projects:
            materials = Material.query.filter_by(project_id=project.id).limit(2).all()
            for idx, mat in enumerate(materials):
                scrap = MaterialScrap(
                    project_id=project.id,
                    code=f"BF-{project.code}-{idx+1:03d}",
                    scrap_date=datetime(2024, 9, 1),
                    reason='damaged',
                    remark='损坏无法使用',
                    applicant_name='张三',
                    approval_status='passed'
                )
                db.session.add(scrap)
                db.session.flush()
                
                item = MaterialScrapItem(
                    scrap_id=scrap.id,
                    material_id=mat.id,
                    quantity=5,
                    unit_price=100,
                    amount=500,
                    reason_detail='损坏无法使用'
                )
                db.session.add(item)
        
        db.session.commit()
        print("报废数据创建完成")

def main():
    """主函数"""
    with app.app_context():
        print("=" * 60)
        print("物资设备管理系统 - 全量测试数据初始化")
        print("=" * 60)
        
        print("\n1. 初始化系统配置和字典...")
        init_system_config()
        init_dict_data()
        
        print("\n2. 创建组织架构...")
        orgs = create_org_structure()
        
        print("\n3. 创建角色...")
        roles = create_roles()
        
        print("\n4. 创建测试项目...")
        projects = create_projects(orgs)
        
        print("\n5. 创建测试用户...")
        create_users(roles, orgs, projects)
        
        print("\n6. 创建公司级主数据...")
        main_data = create_company_materials(orgs['company'])
        
        print("\n7. 创建项目级数据...")
        create_project_data('JCYY')
        create_project_data('SBS')
        create_project_data('LYXM')
        
        print("\n8. 创建审批流程...")
        create_approval_flows()
        
        print("\n9. 创建项目间调拨...")
        create_inter_project_transfer()
        
        print("\n10. 创建报废数据...")
        create_scrap_data()
        
        print("\n" + "=" * 60)
        print("测试数据初始化完成！")
        print("=" * 60)
        print("\n测试账号清单:")
        print("  admin / 123456 - 超级管理员")
        print("  wuzibuzhang / 123456 - 公司物资部长")
        print("  caiwuzhuguan / 123456 - 财务主管")
        print("  jcy_xmjl / 123456 - 锦橙苑项目经理")
        print("  jcy_wzy / 123456 - 锦橙苑物资员")
        print("  jcy_luruyuan / 123456 - 锦橙苑录入员")
        print("  sbs_xmjl / 123456 - 孙八砦项目经理")
        print("  sbs_wzy / 123456 - 孙八砦物资员")
        print("  ly_xmjl / 123456 - 洛阳项目经理")
        print("  ly_wzy / 123456 - 洛阳物资员")
        print("  jianzhi_wzy / 123456 - 兼职物资员（锦橙苑+孙八砦）")

if __name__ == '__main__':
    main()