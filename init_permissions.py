#!/usr/bin/env python3
"""权限点初始化脚本 - 按权限点清单更新sys_menu表"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import SysMenu, SysRoleMenu, SysRole

OPERATIONS = ['view', 'create', 'edit', 'delete', 'import', 'export', 'approve', 'print']

PERMISSION_MAP = {
    'main.index': 'workbench:index',
    'approval.my_approvals': 'workbench:approval',
    'project.index': 'basic:project',
    'supplier.index': 'basic:supplier',
    'category.index': 'basic:category',
    'material.index': 'basic:material',
    'unit.index': 'basic:unit',
    'work_number.index': 'basic:work_number',
    'master.material_index': 'master:material',
    'master.supplier_index': 'master:supplier',
    'master.category_index': 'master:category',
    'purchase_requisition.index': 'contract:purchase_requisition',
    'contract.index': 'contract:list',
    'reconciliation.index': 'contract:reconciliation',
    'contract.invoices': 'contract:invoices',
    'payment_application.index': 'contract:payment_application',
    'contract.payments': 'contract:payments',
    'price_formula.index': 'contract:price_formula',
    'stock_in.index': 'stock:in',
    'stock_out.index': 'stock:out',
    'inventory.index': 'stock:query',
    'stock_check.index': 'stock:check',
    'material_transfer.index': 'stock:transfer',
    'batch.index': 'stock:batch',
    'batch.expiry_alerts': 'stock:expiry',
    'scrap.index': 'stock:scrap',
    'period_close.index': 'stock:period_close',
    'turnover_material.index': 'turnover:material',
    'turnover_material.record_index': 'turnover:record',
    'turnover_material.rental_bill': 'turnover:rental',
    'equipment.index': 'equipment:list',
    'equipment.rent_settle': 'equipment:rent',
    'equipment.depreciation': 'equipment:depreciation',
    'equipment.inspection_list': 'equipment:inspection_plan',
    'equipment.inspection_tasks': 'equipment:inspection_task',
    'equipment.inspection_records': 'equipment:inspection_record',
    'concrete.index': 'tools:concrete',
    'concrete.reconcile': 'tools:concrete_reconcile',
    'steel.index': 'tools:steel',
    'steel.specs': 'tools:steel_specs',
    'barcode.index': 'tools:barcode',
    'barcode.batch_print': 'tools:barcode_batch',
    'report.stock_in_report': 'report:stock_in',
    'report.stock_out_report': 'report:stock_out',
    'report.material_movement': 'report:movement',
    'report.receive_issue': 'report:receive_issue',
    'report.ledger': 'report:ledger',
    'report.work_number_cost': 'report:work_number_cost',
    'report.supplier_ledger': 'report:supplier_ledger',
    'subcontract.index': 'report:subcontract',
    'concrete.stats': 'report:concrete_stats',
    'report.advanced': 'report:advanced',
    'admin.users': 'system:user',
    'system.depts': 'system:dept',
    'system.roles': 'system:role',
    'system.menus': 'system:menu',
    'dict_mgr.index': 'system:dict',
    'approval.flows': 'system:approval_flow',
    'batch.category_config': 'system:batch_category',
    'admin.config_list': 'system:config',
    'admin.announcements': 'system:announcement',
    'admin.notify_config': 'system:notify',
    'ai.config': 'system:ai_config',
    'admin.attachments': 'system:attachment',
    'org_sync.index': 'system:org_sync',
    'admin.backup_index': 'system:backup',
    'admin.recycle_bin': 'system:recycle_bin',
    'admin.archive_index': 'system:archive',
    'admin.db_migrations': 'system:db_migration',
    'admin.audit_logs': 'system:audit_log',
    'admin.login_logs': 'system:login_log',
    'admin.online_users': 'system:online_user',
    'admin.error_logs': 'system:error_log',
    'ai.logs': 'system:ai_log',
    'help.index': 'help:guide',
    'mobile.offline_index': 'help:mobile',
}


def update_menu_permissions():
    """更新菜单权限标识"""
    app = create_app()
    with app.app_context():
        print("=" * 80)
        print("权限点初始化")
        print("=" * 80)

        updated_count = 0
        for menu in SysMenu.query.filter_by(menu_type='menu').all():
            if not menu.menu_code:
                continue

            base_permission = PERMISSION_MAP.get(menu.menu_code)
            if base_permission:
                view_permission = f"{base_permission}:view"
                if menu.permission != view_permission:
                    menu.permission = view_permission
                    updated_count += 1
                    print(f"  更新菜单[{menu.id}] {menu.menu_name}: {menu.permission}")

        db.session.commit()
        print(f"\n共更新 {updated_count} 个菜单权限标识")


def ensure_super_admin_permissions():
    """确保超级管理员拥有所有权限"""
    app = create_app()
    with app.app_context():
        print("\n" + "=" * 80)
        print("确保超级管理员权限")
        print("=" * 80)

        super_admin = SysRole.query.filter_by(role_code='super_admin').first()
        if not super_admin:
            print("✗ 未找到超级管理员角色")
            return

        existing = set((rm.menu_id, rm.operation) for rm in SysRoleMenu.query.filter_by(role_id=super_admin.id).all())
        all_menus = SysMenu.query.all()

        added_count = 0
        for menu in all_menus:
            for op in OPERATIONS:
                if (menu.id, op) not in existing:
                    rp = SysRoleMenu(role_id=super_admin.id, menu_id=menu.id, operation=op)
                    db.session.add(rp)
                    added_count += 1

        db.session.commit()
        print(f"超级管理员角色ID: {super_admin.id}")
        print(f"新增权限关联: {added_count} 条")

        total_perms = SysRoleMenu.query.filter_by(role_id=super_admin.id).count()
        print(f"超级管理员总权限数: {total_perms}")


def verify_permissions():
    """验证权限数据完整性"""
    app = create_app()
    with app.app_context():
        print("\n" + "=" * 80)
        print("权限数据验证")
        print("=" * 80)

        menus = SysMenu.query.filter_by(menu_type='menu').all()
        print(f"\n菜单总数: {len(menus)}")

        # 检查权限标识格式
        invalid_permissions = []
        for menu in menus:
            if menu.permission:
                parts = menu.permission.split(':')
                if len(parts) != 3 or parts[2] != 'view':
                    invalid_permissions.append(f"[{menu.id}] {menu.menu_name}: {menu.permission}")

        if invalid_permissions:
            print(f"\n✗ 权限标识格式不正确 ({len(invalid_permissions)}个):")
            for item in invalid_permissions[:10]:
                print(f"  {item}")
        else:
            print("\n✓ 所有权限标识格式正确")

        # 检查重复权限标识
        perms = {}
        for menu in menus:
            if menu.permission:
                if menu.permission in perms:
                    perms[menu.permission].append(menu.menu_name)
                else:
                    perms[menu.permission] = [menu.menu_name]

        duplicates = {k: v for k, v in perms.items() if len(v) > 1}
        if duplicates:
            print(f"\n✗ 存在重复权限标识 ({len(duplicates)}个):")
            for perm, names in duplicates.items():
                print(f"  {perm}: {', '.join(names)}")
        else:
            print("\n✓ 无重复权限标识")

        # 检查角色权限关联
        roles = SysRole.query.all()
        print(f"\n角色总数: {len(roles)}")

        for role in roles:
            count = SysRoleMenu.query.filter_by(role_id=role.id).count()
            print(f"  [{role.role_code}] {role.role_name}: {count} 条权限")

        # 检查超级管理员权限完整性
        super_admin = SysRole.query.filter_by(role_code='super_admin').first()
        if super_admin:
            total = SysRoleMenu.query.filter_by(role_id=super_admin.id).count()
            expected = len(SysMenu.query.all()) * len(OPERATIONS)
            if total >= expected:
                print(f"\n✓ 超级管理员权限完整 ({total}/{expected})")
            else:
                print(f"\n✗ 超级管理员权限不完整 ({total}/{expected})")


def main():
    update_menu_permissions()
    ensure_super_admin_permissions()
    verify_permissions()

    print("\n" + "=" * 80)
    print("权限点初始化完成")
    print("=" * 80)


if __name__ == '__main__':
    main()
