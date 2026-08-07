#!/usr/bin/env python
# scripts/register_integration_menu.py
"""幂等注册「外部对接」菜单（顶级目录 + 2 个子菜单）。

严格遵守本项目菜单三条铁律：
1) 顶级目录 parent_id 必须为 NULL（写 0 会成为孤儿节点，侧边栏不显示）；
2) 子菜单 menu_code 必须等于 Flask 端点名（get_menu_groups 用 url_for(menu_code)，
   端点不存在会导致整个侧边栏渲染崩溃）；
3) 按 menu_code 幂等 upsert，可重复执行。

执行：
    ./venv/bin/python scripts/register_integration_menu.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import SysMenu  # noqa: E402

CATALOG = {
    'menu_code': 'integration_catalog',
    'menu_name': '外部对接',
    'menu_type': 'catalog',
    'icon': 'bi-plug',
    'sort': 117,
    'module_key': 'integration',
    'permission': 'integration:datasource:view',
    'path': None,
}

CHILDREN = [
    {
        'menu_code': 'integration.datasource_list',   # == Flask 端点名
        'menu_name': '外部数据源配置',
        'path': '/integration/datasource/',
        'icon': 'bi-key',
        'sort': 1,
        'permission': 'integration:datasource:view',
    },
    {
        'menu_code': 'integration.template_list',     # == Flask 端点名
        'menu_name': '导出模板管理',
        'path': '/integration/template/',
        'icon': 'bi-file-earmark-spreadsheet',
        'sort': 2,
        'permission': 'integration:template:view',
    },
]


def upsert(code, **fields):
    m = SysMenu.query.filter_by(menu_code=code).first()
    created = False
    if not m:
        m = SysMenu(menu_code=code)
        db.session.add(m)
        created = True
    for k, v in fields.items():
        setattr(m, k, v)
    db.session.flush()
    return m, created


def main():
    app = create_app()
    with app.app_context():
        # 1) 顶级目录：parent_id 显式为 None
        cat, cat_new = upsert(
            CATALOG['menu_code'],
            parent_id=None,
            menu_name=CATALOG['menu_name'],
            menu_type=CATALOG['menu_type'],
            path=CATALOG['path'],
            icon=CATALOG['icon'],
            sort=CATALOG['sort'],
            status=True,
            permission=CATALOG['permission'],
            module_key=CATALOG['module_key'],
            remark='外部数据源与导出模板配置中心')
        print('%s 目录 id=%s %s' % ('新建' if cat_new else '更新', cat.id, cat.menu_name))

        # 2) 子菜单：menu_code 必须是真实端点名
        endpoints = set(app.view_functions.keys())
        for c in CHILDREN:
            if c['menu_code'] not in endpoints:
                print('!! 端点不存在，跳过（否则会崩溃整个侧边栏）：%s' % c['menu_code'])
                continue
            m, new = upsert(
                c['menu_code'],
                parent_id=cat.id,
                menu_name=c['menu_name'],
                menu_type='menu',
                path=c['path'],
                icon=c['icon'],
                sort=c['sort'],
                status=True,
                permission=c['permission'],
                module_key=CATALOG['module_key'])
            print('  %s 菜单 id=%s %s -> %s' %
                  ('新建' if new else '更新', m.id, m.menu_name, m.menu_code))

        db.session.commit()

        # 3) 同步到 auth_core 影子菜单目录（阶段A 已建）
        try:
            from app.auth_core.adapter import sync_menu_from_legacy
            n = sync_menu_from_legacy()
            print('auth_core_menu 同步：%s' % n)
        except Exception as e:
            print('auth_core_menu 同步跳过：%s' % e)

        # 4) 初始化内置数据源/模板
        from app.integration.models import ensure_builtin_integration_data
        changed = ensure_builtin_integration_data()
        print('内置数据源/模板初始化：%s' % ('已写入' if changed else '已存在，无变更'))

        # 5) 补授权限（新权限点 → 角色）
        try:
            from app.auth_core.adapter import grant_default_role_permissions, sync_permission_names
            from app.auth_core.init_data import PERM_SEED
            from app.auth_core.models import AuthPermission
            added = 0
            for module, resource, action, key in PERM_SEED:
                if not AuthPermission.query.filter_by(perm_key=key).first():
                    db.session.add(AuthPermission(
                        module=module, resource=resource, action=action, perm_key=key))
                    added += 1
            if added:
                db.session.commit()
            g = grant_default_role_permissions()
            sync_permission_names()
            print('权限点新增 %s 个，角色授权新增 %s 条' % (added, g))
        except Exception as e:
            db.session.rollback()
            print('权限补齐失败：%s' % e)


if __name__ == '__main__':
    main()
