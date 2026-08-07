# app/auth_core/menu_tree.py
"""阶段 A 影子菜单树：从 auth_core_menu 重建菜单树，算法与 permission_service.get_user_menu_tree 一致。

当前活路径仍读 sys_menu；本模块仅供 parity 验证与后续切流使用，不接入 base.html / 任何活路径。
可见性：AuthGateway 授予的 perm_key 集合 ∩ auth_core_menu.permission（同命名空间）。
"""
from app.auth_core.models import AuthMenu, AuthUser
from app.auth_core.gateway import AuthGateway


def _auth_core_menu_ids_via_auth_core(user):
    """从 auth_core 角色→权限(SSOT) 派生可见菜单 id 集合（读 auth_core_menu）。"""
    keys = AuthGateway.get_permissions(user.id)
    if not keys:
        return set()
    menus = AuthMenu.query.filter(
        AuthMenu.permission.in_(keys),
        AuthMenu.menu_type == 'menu'
    ).all()
    return set(m.id for m in menus)


def get_user_menu_tree_via_auth_core(user):
    """影子实现：镜像 permission_service.get_user_menu_tree，目录源为 auth_core_menu。"""
    from app.models import SysModule

    enabled_modules = {}
    for m in SysModule.query.all():
        enabled_modules[m.module_key] = bool(m.status)

    allowed_menu_ids = set()
    if user.is_admin():
        allowed_menu_ids = set(m.id for m in AuthMenu.query.all())
    else:
        if isinstance(user, AuthUser):
            allowed_menu_ids = _auth_core_menu_ids_via_auth_core(user)
        else:
            allowed_menu_ids = set()

    def build_tree(parent_id=None):
        children = []
        menus = AuthMenu.query.filter_by(
            parent_id=parent_id,
            status=True
        ).order_by(AuthMenu.sort).all()

        for menu in menus:
            if menu.module_key and not enabled_modules.get(menu.module_key, False):
                continue

            if menu.menu_type == 'catalog':
                sub_children = build_tree(menu.id)
                if sub_children:
                    children.append({
                        'id': menu.id,
                        'name': menu.menu_name,
                        'code': menu.menu_code,
                        'type': menu.menu_type,
                        'icon': menu.icon or '',
                        'path': menu.path or '',
                        'component': menu.component or '',
                        'permission': menu.permission or '',
                        'moduleKey': menu.module_key,
                        'children': sub_children
                    })
            elif menu.menu_type == 'menu':
                if user.is_admin() or menu.id in allowed_menu_ids:
                    children.append({
                        'id': menu.id,
                        'name': menu.menu_name,
                        'code': menu.menu_code,
                        'type': menu.menu_type,
                        'icon': menu.icon or '',
                        'path': menu.path or '',
                        'component': menu.component or '',
                        'permission': menu.permission or '',
                        'moduleKey': menu.module_key,
                        'children': build_tree(menu.id)
                    })
            elif menu.menu_type == 'button':
                pass

        return children

    return build_tree()
