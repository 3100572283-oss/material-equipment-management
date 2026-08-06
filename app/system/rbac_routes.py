"""RBAC权限管理 - 路由层"""
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime

from app import db
from app.system import bp
from app.models import SysDept, SysRole, SysMenu, SysRoleMenu, SysRoleDept, SysRoleDataScope, User, Project
from app.decorators import admin_required, permission_required, log_audit
from app.utils import gen_dept_code, gen_role_code
import json as _json


TYPE_LABELS = {
    'catalog': '目录',
    'menu': '菜单',
    'button': '按钮',
}

DEPT_TYPE_LABELS = {
    'company': '公司',
    'branch': '分公司',
    'dept': '部门',
    'team': '班组',
}


def get_sys_menu_data():
    """获取系统管理左侧菜单数据（按5个分组）"""
    sys_group_map = {
        '用户与权限': [],
        '业务配置': [],
        '系统工具': [],
        '数据管理': [],
        '日志审计': [],
    }
    
    section_map = {
        '用户管理': '用户与权限',
        '组织架构': '用户与权限',
        '项目管理': '用户与权限',
        '角色管理': '用户与权限',
        '菜单管理': '用户与权限',
        '基础信息管理': '业务配置',
        '审批流程管理': '业务配置',
        '批次分类配置': '业务配置',
        '系统配置': '业务配置',
        '公告管理': '系统工具',
        '通知配置': '系统工具',
        'AI配置': '系统工具',
        '附件管理': '系统工具',
        '组织架构同步': '系统工具',
        '数据备份': '数据管理',
        '数据回收站': '数据管理',
        '项目归档': '数据管理',
        '期末结账': '数据管理',
        '数据库迁移': '数据管理',
        '操作日志': '日志审计',
        '登录日志': '日志审计',
        '在线用户': '日志审计',
        '错误日志': '日志审计',
        '单据编号检查': '日志审计',
        'AI调用日志': '日志审计',
        '同步日志': '日志审计',
    }
    
    icon_map = {
        '用户管理': 'bi-person',
        '组织架构': 'bi-diagram-3',
        '项目管理': 'bi-folder',
        '角色管理': 'bi-shield-lock',
        '菜单管理': 'bi-list-nested',
        '基础信息管理': 'bi-book',
        '审批流程管理': 'bi-arrow-left-right',
        '批次分类配置': 'bi-tags',
        '系统配置': 'bi-sliders',
        '公告管理': 'bi-megaphone',
        '通知配置': 'bi-bell',
        'AI配置': 'bi-robot',
        '附件管理': 'bi-paperclip',
        '组织架构同步': 'bi-arrow-repeat',
        '数据备份': 'bi-cloud-arrow-down',
        '数据回收站': 'bi-trash',
        '项目归档': 'bi-archive',
        '期末结账': 'bi-calendar-check',
        '数据库迁移': 'bi-database',
        '操作日志': 'bi-journal-text',
        '登录日志': 'bi-box-arrow-in-right',
        '在线用户': 'bi-people',
        '错误日志': 'bi-bug',
        '单据编号检查': 'bi-check-circle',
        'AI调用日志': 'bi-robot',
        '同步日志': 'bi-arrow-repeat',
    }
    
    from flask import session, current_app
    
    project_module_config = {}
    allowed_endpoints = set()
    try:
        if not current_user.is_admin():
            role_menus = db.session.query(SysMenu.menu_code).join(
                SysRoleMenu, SysRoleMenu.menu_id == SysMenu.id
            ).filter(
                SysRoleMenu.role_id == current_user.role_id,
                SysMenu.menu_code.isnot(None)
            ).all()
            allowed_endpoints = {row[0] for row in role_menus if row[0]}
        project_id = session.get('current_project_id')
        if project_id:
            project = Project.query.get(project_id)
            if project:
                project_module_config = project.get_module_config()
    except Exception:
        pass
    
    # 尝试从数据库读取，失败则回退到配置文件
    try:
        sys_catalog = SysMenu.query.filter_by(menu_code='system').first()
        if sys_catalog:
            menu_items = SysMenu.query.filter_by(
                parent_id=sys_catalog.id, menu_type='menu', status=True
            ).order_by(SysMenu.sort).all()
            for menu in menu_items:
                endpoint = menu.menu_code
                # 如果配置了path且指向其他蓝图，从path推导endpoint
                # 例如 path='/project/' 对应 endpoint 'project.index'
                if menu.path and menu.path.startswith('/') and not menu.path.startswith('/system/'):
                    path_parts = [p for p in menu.path.strip('/').split('/') if p]
                    if path_parts:
                        bp_name = path_parts[0]
                        endpoint = f'{bp_name}.index'
                if not endpoint:
                    continue
                from flask import current_app
                try:
                    current_app.url_map.iter_rules()
                    if endpoint not in current_app.view_functions:
                        continue
                except Exception:
                    pass
                if menu.permission and menu.permission.startswith('module_'):
                    item_module = menu.permission
                    if item_module in project_module_config:
                        if not project_module_config[item_module]:
                            continue
                    else:
                        from app.utils import get_config
                        enabled = get_config(item_module, 'true')
                        if str(enabled).lower() != 'true':
                            continue
                if not current_user.is_admin():
                    # 用menu_code判断权限（因为跨蓝图菜单的endpoint与menu_code不同）
                    perm_key = menu.menu_code
                    if perm_key and allowed_endpoints and perm_key not in allowed_endpoints:
                        continue
                item_extra = {}
                if menu.remark:
                    try:
                        item_extra = _json.loads(menu.remark)
                    except Exception:
                        pass
                group_name = section_map.get(menu.menu_name, '业务配置')
                if group_name in sys_group_map:
                    menu_icon = menu.icon
                    if not menu_icon or menu_icon == 'bi-circle':
                        menu_icon = icon_map.get(menu.menu_name, 'bi-circle')
                    # active默认值：跨蓝图菜单用 blueprint_name. 作为前缀匹配
                    default_active = endpoint
                    if menu.path and menu.path.startswith('/') and not menu.path.startswith('/system/'):
                        path_parts = [p for p in menu.path.strip('/').split('/') if p]
                        if path_parts:
                            default_active = f'{path_parts[0]}.'
                    sys_group_map[group_name].append({
                        'title': menu.menu_name,
                        'endpoint': endpoint,
                        'active': item_extra.get('active', default_active),
                        'icon': menu_icon,
                    })
    except Exception:
        try:
            with open(current_app.root_path + '/static/config/menu_config.json', 'r', encoding='utf-8') as f:
                menu_cfg = _json.load(f)
            for group in menu_cfg.get('groups', []):
                if group.get('id') != 'system':
                    continue
                for item in group.get('items', []):
                    item_module = item.get('module')
                    if item_module:
                        if item_module in project_module_config:
                            if not project_module_config[item_module]:
                                continue
                        else:
                            from app.utils import get_config
                            enabled = get_config(item_module, 'true')
                            if str(enabled).lower() != 'true':
                                continue
                    if not current_user.is_admin():
                        ep = item.get('endpoint')
                        if ep and allowed_endpoints and ep not in allowed_endpoints:
                            continue
                    ep = item.get('endpoint', '')
                    if ep:
                        from flask import current_app as _ca
                        try:
                            if ep not in _ca.view_functions:
                                continue
                        except Exception:
                            pass
                    group_name = section_map.get(item.get('title', ''), '业务配置')
                    if group_name in sys_group_map:
                        sys_group_map[group_name].append({
                            'title': item.get('title', ''),
                            'endpoint': item.get('endpoint', ''),
                            'active': item.get('active', item.get('endpoint', '')),
                            'icon': item.get('icon', 'bi-circle'),
                        })
        except Exception:
            pass
    
    # 构建结果列表（按顺序）
    group_order = ['用户与权限', '业务配置', '系统工具', '数据管理', '日志审计']
    result = []
    for gname in group_order:
        items = sys_group_map.get(gname, [])
        if items:
            result.append({
                'title': gname,
                'items': items,
            })
    return result


def _reorder_depts(parent_id):
    """删除部门后重新整理同级排序号"""
    depts = SysDept.query.filter_by(parent_id=parent_id).order_by(
        SysDept.sort, SysDept.id).all()
    for index, dept in enumerate(depts, 1):
        if dept.sort != index:
            dept.sort = index
    db.session.commit()


def _get_allowed_dept_ids():
    """获取当前用户有权限访问的部门ID集合（委托给统一权限服务）

    返回 None 表示拥有全部部门权限，无需过滤
    """
    from app.services.permission_service import permission_service
    return permission_service.get_allowed_dept_ids(current_user)


def _reorder_roles():
    """删除角色后重新整理排序号"""
    roles = SysRole.query.order_by(SysRole.sort, SysRole.id).all()
    for index, role in enumerate(roles, 1):
        if role.sort != index:
            role.sort = index
    db.session.commit()


def _reorder_menus(parent_id):
    """删除菜单后重新整理同级排序号"""
    menus = SysMenu.query.filter_by(parent_id=parent_id).order_by(
        SysMenu.sort, SysMenu.id).all()
    for index, menu in enumerate(menus, 1):
        if menu.sort != index:
            menu.sort = index
    db.session.commit()


def get_first_sys_menu():
    """获取当前用户有权限的第一个系统菜单项"""
    groups = get_sys_menu_data()
    for g in groups:
        if g['items']:
            return g['items'][0]
    return None


def get_sys_menu_title(endpoint):
    """根据endpoint获取菜单标题"""
    groups = get_sys_menu_data()
    for g in groups:
        for item in g['items']:
            active = item.get('active', '')
            if active.endswith('.'):
                if endpoint and endpoint.startswith(active):
                    return item['title']
            else:
                if endpoint == active:
                    return item['title']
    return '系统管理'


@bp.context_processor
def inject_type_labels():
    return {'TYPE_LABELS': TYPE_LABELS, 'DEPT_TYPE_LABELS': DEPT_TYPE_LABELS}


@bp.route('/')
@login_required
@admin_required
def index():
    """系统管理首页 - 跳转到第一个有权限的菜单项"""
    first = get_first_sys_menu()
    if first:
        return redirect(url_for(first['endpoint']))
    flash('您没有系统管理权限', 'error')
    return redirect(url_for('main.index'))


# ============== 部门管理（组织架构树） ==============

@bp.route('/depts')
@login_required
@permission_required('system:dept:list')
def depts():
    """组织架构管理页
    
    数据权限过滤：只显示当前用户有权限访问的部门
    """
    all_depts = SysDept.query.order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()

    # 按数据权限过滤部门
    allowed_dept_ids = _get_allowed_dept_ids()
    if allowed_dept_ids is not None:
        depts_filtered = [d for d in all_depts if d.id in allowed_dept_ids]
    else:
        depts_filtered = all_depts

    return render_template('system/depts.html', depts=depts_filtered)


@bp.route('/depts/create', methods=['GET', 'POST'])
@login_required
@permission_required('system:dept:add')
@log_audit(module='rbac', operation='新增部门')
def create_dept():
    """新增部门
    
    数据权限校验：非管理员只能在自己权限范围内新增部门（父部门必须在权限内）
    """
    if request.method == 'POST':
        dept_name = request.form.get('dept_name', '').strip()
        parent_id = request.form.get('parent_id', type=int, default=0)
        dept_type = request.form.get('dept_type', 'dept')
        leader = request.form.get('leader', '').strip()
        sort_input = request.form.get('sort', type=int, default=None)
        remark = request.form.get('remark', '').strip()

        if not dept_name:
            flash('部门名称不能为空', 'error')
            return redirect(url_for('system.create_dept'))

        # 数据权限校验：非管理员只能在有权限的部门下新增
        allowed_dept_ids = _get_allowed_dept_ids()
        if allowed_dept_ids is not None and parent_id is not None:
            if parent_id not in allowed_dept_ids:
                flash('无权在此部门下新增子部门', 'error')
                return redirect(url_for('system.depts'))

        # 自动生成部门编码
        dept_code = gen_dept_code()

        if sort_input is None:
            max_sort = db.session.query(db.func.max(SysDept.sort)).filter_by(
                parent_id=parent_id if parent_id else None).scalar() or 0
            sort_input = max_sort + 1

        dept = SysDept(
            dept_code=dept_code,
            dept_name=dept_name,
            parent_id=(parent_id if parent_id else None),
            dept_type=dept_type,
            leader=leader or None,
            sort=sort_input,
            remark=remark or None
        )
        db.session.add(dept)
        db.session.commit()
        flash('部门创建成功', 'success')
        return redirect(url_for('system.depts'))

    # 查询所有行政部门并按数据权限过滤（只显示有权限的部门作为可选父部门）
    all_depts = SysDept.query.filter(
        SysDept.dept_type.in_(['company', 'branch', 'dept', 'team'])
    ).order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()
    allowed_dept_ids = _get_allowed_dept_ids()
    if allowed_dept_ids is not None:
        all_depts = [d for d in all_depts if d.id in allowed_dept_ids]

    dept_map = {d.id: d for d in all_depts}

    def get_dept_path(d):
        path = []
        current = d
        while current:
            path.insert(0, current.dept_name)
            if current.parent_id and current.parent_id in dept_map:
                current = dept_map[current.parent_id]
            else:
                current = None
        return ' / '.join(path)

    def get_dept_depth(d):
        depth = 0
        current = d
        while current.parent_id is not None:
            depth += 1
            if current.parent_id in dept_map:
                current = dept_map[current.parent_id]
            else:
                break
        return depth

    for d in all_depts:
        d._depth = get_dept_depth(d)
        d._path = get_dept_path(d)

    default_sort = 1
    parent_id_param = request.args.get('parent_id', type=int)
    max_sort = db.session.query(db.func.max(SysDept.sort)).filter_by(
        parent_id=parent_id_param if parent_id_param else None).scalar() or 0
    default_sort = max_sort + 1

    parent_dept = dept_map.get(parent_id_param) if parent_id_param else None

    return render_template('system/dept_form.html', dept=None, all_depts=all_depts,
                           default_sort=default_sort, parent_id=parent_id_param, parent_dept=parent_dept,
                           DEPT_TYPE_LABELS=DEPT_TYPE_LABELS)


@bp.route('/depts/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@permission_required('system:dept:edit')
@log_audit(module='rbac', operation='编辑部门')
def edit_dept(id):
    """编辑部门
    
    数据权限校验：只能编辑权限范围内的部门
    """
    dept = SysDept.query.get_or_404(id)

    # 数据权限校验：只能编辑有权限的部门
    allowed_dept_ids = _get_allowed_dept_ids()
    if allowed_dept_ids is not None and dept.id not in allowed_dept_ids:
        flash('无权编辑此部门', 'error')
        return redirect(url_for('system.depts'))

    if request.method == 'POST':
        dept_name = request.form.get('dept_name', '').strip()
        parent_id = request.form.get('parent_id', type=int, default=0)
        dept_type = request.form.get('dept_type', 'dept')
        leader = request.form.get('leader', '').strip()
        sort = request.form.get('sort', type=int, default=dept.sort)
        remark = request.form.get('remark', '').strip()

        if not dept_name:
            flash('部门名称不能为空', 'error')
            return redirect(url_for('system.edit_dept', id=id))

        # 不能将部门设为自己的子部门
        if parent_id == id:
            flash('不能将上级部门设为自己', 'error')
            return redirect(url_for('system.edit_dept', id=id))

        # 数据权限校验：非管理员修改父部门时，新父部门必须在权限范围内
        if allowed_dept_ids is not None and parent_id is not None and parent_id != dept.parent_id:
            if parent_id not in allowed_dept_ids:
                flash('无权将部门移动到该父部门下', 'error')
                return redirect(url_for('system.edit_dept', id=id))

        dept.dept_name = dept_name
        dept.parent_id = parent_id if parent_id else None
        dept.dept_type = dept_type
        dept.leader = leader or None
        dept.sort = sort
        dept.remark = remark or None

        db.session.commit()
        flash('部门已更新', 'success')
        return redirect(url_for('system.depts'))

    # 查询所有行政部门并按数据权限过滤（只显示有权限的部门作为可选父部门）
    all_depts = SysDept.query.filter(
        SysDept.dept_type.in_(['company', 'branch', 'dept', 'team'])
    ).order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()
    allowed_dept_ids = _get_allowed_dept_ids()
    if allowed_dept_ids is not None:
        all_depts = [d for d in all_depts if d.id in allowed_dept_ids]

    dept_map = {d.id: d for d in all_depts}

    def get_dept_path(d):
        path = []
        current = d
        while current:
            path.insert(0, current.dept_name)
            if current.parent_id and current.parent_id in dept_map:
                current = dept_map[current.parent_id]
            else:
                current = None
        return ' / '.join(path)

    def get_dept_depth(d):
        depth = 0
        current = d
        while current.parent_id is not None:
            depth += 1
            if current.parent_id in dept_map:
                current = dept_map[current.parent_id]
            else:
                break
        return depth

    for d in all_depts:
        d._depth = get_dept_depth(d)
        d._path = get_dept_path(d)

    return render_template('system/dept_form.html', dept=dept, all_depts=all_depts,
                           parent_id=dept.parent_id, parent_dept=dept_map.get(dept.parent_id) if dept.parent_id else None,
                           DEPT_TYPE_LABELS=DEPT_TYPE_LABELS)


@bp.route('/depts/<int:id>/delete', methods=['POST'])
@login_required
@permission_required('system:dept:delete')
@log_audit(module='rbac', operation='删除部门')
def delete_dept(id):
    """删除部门
    
    数据权限校验：只能删除权限范围内的部门
    """
    dept = SysDept.query.get_or_404(id)

    # 数据权限校验：只能删除有权限的部门
    allowed_dept_ids = _get_allowed_dept_ids()
    if allowed_dept_ids is not None and dept.id not in allowed_dept_ids:
        flash('无权删除此部门', 'error')
        return redirect(url_for('system.depts'))

    if dept.children.count() > 0:
        flash('该部门有子部门，请先转移子部门后再删除', 'error')
        return redirect(url_for('system.depts'))

    if dept.users.count() > 0:
        flash('该部门下有用户，请先转移用户后再删除', 'error')
        return redirect(url_for('system.depts'))

    parent_id = dept.parent_id
    db.session.delete(dept)
    db.session.commit()
    _reorder_depts(parent_id)
    flash('部门已删除', 'success')
    return redirect(url_for('system.depts'))


@bp.route('/depts/<int:id>/toggle', methods=['POST'])
@login_required
@permission_required('system:dept:edit')
@log_audit(module='rbac', operation='启用/禁用部门')
def toggle_dept(id):
    """启用/禁用部门
    
    数据权限校验：只能启停权限范围内的部门
    """
    dept = SysDept.query.get_or_404(id)

    # 数据权限校验：只能启停有权限的部门
    allowed_dept_ids = _get_allowed_dept_ids()
    if allowed_dept_ids is not None and dept.id not in allowed_dept_ids:
        flash('无权操作此部门', 'error')
        return redirect(url_for('system.depts'))

    dept.status = not dept.status
    db.session.commit()
    flash(f'部门已{"启用" if dept.status else "禁用"}', 'success')
    return redirect(url_for('system.depts'))


# ============== 角色管理 ==============

@bp.route('/roles')
@login_required
@admin_required
def roles():
    """角色列表页"""
    roles_list = SysRole.query.order_by(SysRole.sort).all()
    return render_template('system/roles.html', roles=roles_list)


@bp.route('/roles/create', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='新增角色')
def create_role():
    """新增角色"""
    if request.method == 'POST':
        role_name = request.form.get('role_name', '').strip()
        data_scope = request.form.get('data_scope', 'all')
        sort_input = request.form.get('sort', type=int, default=None)
        remark = request.form.get('remark', '').strip()

        if not role_name:
            flash('角色名称不能为空', 'error')
            return redirect(url_for('system.create_role'))

        role_code = gen_role_code()

        if sort_input is None:
            max_sort = db.session.query(db.func.max(SysRole.sort)).scalar() or 0
            sort_input = max_sort + 1

        role = SysRole(
            role_code=role_code,
            role_name=role_name,
            data_scope=data_scope,
            sort=sort_input,
            remark=remark or None
        )
        db.session.add(role)
        db.session.commit()
        flash('角色创建成功', 'success')
        return redirect(url_for('system.roles'))

    max_sort = db.session.query(db.func.max(SysRole.sort)).scalar() or 0
    default_sort = max_sort + 1
    default_role_code = gen_role_code()
    return render_template('system/role_form.html', role=None, default_sort=default_sort, default_role_code=default_role_code)


@bp.route('/roles/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='编辑角色')
def edit_role(id):
    """编辑角色"""
    role = SysRole.query.get_or_404(id)
    if request.method == 'POST':
        role_name = request.form.get('role_name', '').strip()
        data_scope = request.form.get('data_scope', 'all')
        sort = request.form.get('sort', type=int, default=0)
        remark = request.form.get('remark', '').strip()

        if not role_name:
            flash('角色名称不能为空', 'error')
            return redirect(url_for('system.edit_role', id=id))

        role.role_name = role_name
        role.data_scope = data_scope
        role.sort = sort
        role.remark = remark or None
        db.session.commit()
        flash('角色已更新', 'success')
        return redirect(url_for('system.roles'))

    return render_template('system/role_form.html', role=role)


@bp.route('/roles/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='删除角色')
def delete_role(id):
    """删除角色"""
    role = SysRole.query.get_or_404(id)
    
    if role.role_code == 'super_admin':
        flash('超级管理员角色不可删除', 'error')
        return redirect(url_for('system.roles'))
    
    if role.users.count() > 0:
        flash('该角色下有用户，无法删除', 'error')
        return redirect(url_for('system.roles'))

    SysRoleMenu.query.filter_by(role_id=id).delete()
    SysRoleDept.query.filter_by(role_id=id).delete()
    db.session.delete(role)
    db.session.commit()
    _reorder_roles()
    flash('角色已删除', 'success')
    return redirect(url_for('system.roles'))


@bp.route('/roles/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='启用/禁用角色')
def toggle_role(id):
    """启用/禁用角色"""
    role = SysRole.query.get_or_404(id)
    if role.role_code == 'super_admin':
        flash('超级管理员角色不可禁用', 'error')
        return redirect(url_for('system.roles'))
    role.status = not role.status
    db.session.commit()
    flash(f'角色已{"启用" if role.status else "禁用"}', 'success')
    return redirect(url_for('system.roles'))


# ============== 菜单权限配置 ==============

@bp.route('/roles/<int:id>/permissions')
@login_required
@admin_required
def role_permissions(id):
    """角色菜单权限配置页（统一权限中心：功能权限+数据权限）"""
    role = SysRole.query.get_or_404(id)

    menus = SysMenu.query.filter(SysMenu.parent_id is None).order_by(SysMenu.sort).all()

    role_permissions = SysRoleMenu.query.filter_by(role_id=id).all()
    menu_op_map = {}
    for rp in role_permissions:
        if rp.menu_id not in menu_op_map:
            menu_op_map[rp.menu_id] = set()
        menu_op_map[rp.menu_id].add(rp.operation)

    checked_menu_ids = list(menu_op_map.keys())
    checked_set = set(checked_menu_ids)

    all_operations = ['view', 'create', 'edit', 'delete', 'import', 'export', 'approve', 'print']
    op_labels = {
        'view': '查看',
        'create': '新增',
        'edit': '编辑',
        'delete': '删除',
        'import': '导入',
        'export': '导出',
        'approve': '审批',
        'print': '打印',
    }

    def build_menu_tree(items, parent_id=None, level=0):
        tree = []
        for item in items:
            if item.parent_id == parent_id:
                ops = menu_op_map.get(item.id, set())
                node = {
                    'menu': item,
                    'level': level,
                    'children': build_menu_tree(items, item.id, level + 1),
                    'has_children': len(item.children.all()) > 0,
                    'checked': item.id in checked_set,
                    'operations': ops,
                }
                tree.append(node)
        return tree

    all_menu_items = SysMenu.query.order_by(SysMenu.sort).all()
    menu_tree = build_menu_tree(all_menu_items)

    # 数据权限配置（从 sys_role_data_scope 读取，兼容旧字段）
    data_scope_cfg = SysRoleDataScope.query.filter_by(role_id=id).first()
    if data_scope_cfg:
        current_data_scope = data_scope_cfg.data_scope or 'all'
        try:
            custom_dept_ids = [int(x) for x in _json.loads(data_scope_cfg.custom_depts)] if data_scope_cfg.custom_depts else []
        except Exception:
            custom_dept_ids = [int(x.strip()) for x in data_scope_cfg.custom_depts.split(',') if x.strip().isdigit()] if data_scope_cfg.custom_depts else []
    else:
        current_data_scope = role.data_scope or 'all'
        custom_dept_ids = [rd.dept_id for rd in SysRoleDept.query.filter_by(role_id=id).all()]

    all_depts = SysDept.query.filter(
        SysDept.dept_type.in_(['company', 'branch', 'dept', 'team'])
    ).order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()

    # 数据范围枚举
    data_scope_options = [
        ('all', '全部数据', '不限制，可访问所有数据'),
        ('dept_and_sub', '本部门及下级部门数据', '当前用户所在部门及其所有子部门的数据'),
        ('dept', '本部门数据', '仅当前用户所在部门的数据'),
        ('self', '仅本人数据', '只显示当前用户自己创建的数据'),
        ('custom', '自定义数据权限', '手动选择可访问的部门'),
    ]

    return render_template('system/role_permissions.html',
                           role=role, menu_tree=menu_tree,
                           checked_ids=checked_menu_ids, checked_set=checked_set,
                           menu_op_map=menu_op_map,
                           operations=all_operations, op_labels=op_labels,
                           data_scope_options=data_scope_options,
                           current_data_scope=current_data_scope,
                           all_depts=all_depts,
                           custom_dept_ids=custom_dept_ids)


@bp.route('/roles/<int:id>/permissions/save', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='保存角色权限')
def save_role_permissions(id):
    """保存角色权限 - 统一入口：功能权限+数据权限

    接收前端提交的数据：
    - menu_ops: 多个 menu_id:operation 项
    - data_scope: 数据范围 (all/dept_and_sub/dept/self/custom)
    - custom_depts: 自定义部门ID列表 (data_scope=custom时使用)

    自动处理父子菜单联动：勾选子菜单权限时，自动同步授予所有上级父菜单的查看权限。
    保存后立即回查验证，确保数据一致性。
    """
    from app.models import SysModule
    from app.services.permission_service import permission_service

    role = SysRole.query.get_or_404(id)

    menu_ops_raw = request.form.getlist('menu_ops')

    # 解析提交的权限数据
    unique_ops = set()
    for item in menu_ops_raw:
        if ':' in item:
            parts = item.split(':')
            if len(parts) == 2:
                try:
                    menu_id = int(parts[0])
                    operation = parts[1]
                    if operation in {'view', 'create', 'edit', 'delete', 'import', 'export', 'approve', 'print'}:
                        unique_ops.add((menu_id, operation))
                except ValueError:
                    pass

    # 自动补全父菜单查看权限（父子菜单联动）
    final_ops = permission_service.apply_parent_menu_permissions(id, unique_ops)

    # 数据权限
    data_scope = request.form.get('data_scope', 'all')
    if data_scope not in {'all', 'dept_and_sub', 'dept', 'self', 'custom'}:
        data_scope = 'all'
    custom_dept_ids_raw = request.form.getlist('custom_depts')

    try:
        # 删除旧功能权限
        SysRoleMenu.query.filter_by(role_id=id).delete()

        # 保存新功能权限（包含自动补全的父菜单权限）
        for menu_id, operation in final_ops:
            rm = SysRoleMenu(role_id=id, menu_id=menu_id, operation=operation)
            db.session.add(rm)

        # 保存数据权限到 sys_role_data_scope
        custom_depts_json = _json.dumps([int(x) for x in custom_dept_ids_raw if str(x).isdigit()]) if custom_dept_ids_raw else None
        scope_cfg = SysRoleDataScope.query.filter_by(role_id=id).first()
        if scope_cfg:
            scope_cfg.data_scope = data_scope
            scope_cfg.custom_depts = custom_depts_json
        else:
            scope_cfg = SysRoleDataScope(role_id=id, data_scope=data_scope, custom_depts=custom_depts_json)
            db.session.add(scope_cfg)

        # 同步旧字段（兼容）
        role.data_scope = data_scope
        # 自定义部门
        SysRoleDept.query.filter_by(role_id=id).delete()
        if data_scope == 'custom' and custom_dept_ids_raw:
            for did in custom_dept_ids_raw:
                try:
                    db.session.add(SysRoleDept(role_id=id, dept_id=int(did)))
                except ValueError:
                    pass

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'权限保存失败：{e}', 'danger')
        return redirect(url_for('system.role_permissions', id=id))

    # ===== 回查验证：确保保存的数据与最终权限集合一致 =====
    saved = set((rm.menu_id, rm.operation) for rm in SysRoleMenu.query.filter_by(role_id=id).all())

    if saved != final_ops:
        missing = final_ops - saved
        extra = saved - final_ops
        error_msg = []
        if missing:
            error_msg.append(f"丢失权限: {missing}")
        if extra:
            error_msg.append(f"多余权限: {extra}")
        error_text = "; ".join(error_msg)
        flash(f'权限保存验证失败: {error_text}', 'danger')
        return redirect(url_for('system.role_permissions', id=id))

    # 回查数据权限
    saved_scope = SysRoleDataScope.query.filter_by(role_id=id).first()
    if not saved_scope or saved_scope.data_scope != data_scope:
        flash(f'数据权限保存验证失败', 'danger')
        return redirect(url_for('system.role_permissions', id=id))

    added_parent_count = len(final_ops) - len(unique_ops)
    flash(f'权限配置已保存（功能权限 {len(final_ops)} 项，其中自动补全父菜单 {added_parent_count} 项，数据权限：{dict([(d[0], d[1]) for d in [("all", "全部"), ("dept_and_sub", "本部门及下级"), ("dept", "本部门"), ("self", "仅本人"), ("custom", "自定义")]]).get(data_scope, data_scope)}）', 'success')
    return redirect(url_for('system.role_permissions', id=id))


# ============== 数据权限配置（已合并到角色权限配置页） ==============

@bp.route('/roles/<int:id>/data_scope')
@login_required
@admin_required
def role_data_scope(id):
    """数据权限配置 - 已合并到统一权限配置页

    重定向到角色权限配置页（功能权限+数据权限统一管理）
    """
    flash('数据权限已合并到统一权限配置页', 'info')
    return redirect(url_for('system.role_permissions', id=id))


@bp.route('/roles/<int:id>/data_scope/save', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='保存数据权限（兼容路由）')
def save_role_data_scope(id):
    """保存角色数据权限（兼容旧路由，自动重定向到统一保存入口）"""
    return save_role_permissions(id)


# ============== 菜单管理 ==============

@bp.route('/menus')
@login_required
@admin_required
def menus():
    """菜单列表页"""
    menus_list = SysMenu.query.order_by(SysMenu.sort).all()
    return render_template('system/menus.html', menus=menus_list)


@bp.route('/menus/create', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='新增菜单')
def create_menu():
    """新增菜单"""
    if request.method == 'POST':
        menu_name = request.form.get('menu_name', '').strip()
        menu_code = request.form.get('menu_code', '').strip()
        parent_id = request.form.get('parent_id', type=int, default=0)
        menu_type = request.form.get('menu_type', 'menu')
        path = request.form.get('path', '').strip()
        icon = request.form.get('icon', '').strip()
        sort_input = request.form.get('sort', type=int, default=None)
        permission = request.form.get('permission', '').strip()
        remark = request.form.get('remark', '').strip()

        if not menu_name:
            flash('菜单名称不能为空', 'error')
            return redirect(url_for('system.create_menu'))

        if menu_code and SysMenu.query.filter_by(menu_code=menu_code).first():
            flash('菜单编码已存在', 'error')
            return redirect(url_for('system.create_menu'))

        if sort_input is None:
            max_sort = db.session.query(db.func.max(SysMenu.sort)).filter_by(
                parent_id=parent_id if parent_id else None).scalar() or 0
            sort_input = max_sort + 1

        menu = SysMenu(
            menu_name=menu_name,
            menu_code=menu_code or None,
            parent_id=(parent_id if parent_id else None),
            menu_type=menu_type,
            path=path or None,
            icon=icon or None,
            sort=sort_input,
            permission=permission or None,
            remark=remark or None
        )
        db.session.add(menu)
        db.session.commit()
        flash('菜单创建成功', 'success')
        return redirect(url_for('system.menus'))

    all_menus = SysMenu.query.order_by(SysMenu.sort).all()
    parent_id_param = request.args.get('parent_id', type=int)
    max_sort = db.session.query(db.func.max(SysMenu.sort)).filter_by(
        parent_id=(parent_id_param if parent_id_param else None)).scalar() or 0
    default_sort = max_sort + 1
    return render_template('system/menu_form.html', menu=None, all_menus=all_menus, default_sort=default_sort)


@bp.route('/menus/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='编辑菜单')
def edit_menu(id):
    """编辑菜单"""
    menu = SysMenu.query.get_or_404(id)
    if request.method == 'POST':
        menu_name = request.form.get('menu_name', '').strip()
        menu_code = request.form.get('menu_code', '').strip()
        parent_id = request.form.get('parent_id', type=int, default=0)
        menu_type = request.form.get('menu_type', 'menu')
        path = request.form.get('path', '').strip()
        icon = request.form.get('icon', '').strip()
        sort = request.form.get('sort', type=int, default=0)
        permission = request.form.get('permission', '').strip()
        remark = request.form.get('remark', '').strip()

        if not menu_name:
            flash('菜单名称不能为空', 'error')
            return redirect(url_for('system.edit_menu', id=id))

        if menu_code and SysMenu.query.filter(SysMenu.menu_code == menu_code, SysMenu.id != id).first():
            flash('菜单编码已存在', 'error')
            return redirect(url_for('system.edit_menu', id=id))

        menu.menu_name = menu_name
        menu.menu_code = menu_code or None
        menu.parent_id = parent_id if parent_id else None
        menu.menu_type = menu_type
        menu.path = path or None
        menu.icon = icon or None
        menu.sort = sort
        menu.permission = permission or None
        menu.remark = remark or None
        db.session.commit()
        flash('菜单已更新', 'success')
        return redirect(url_for('system.menus'))

    all_menus = SysMenu.query.order_by(SysMenu.sort).all()
    return render_template('system/menu_form.html', menu=menu, all_menus=all_menus)


@bp.route('/menus/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='删除菜单')
def delete_menu(id):
    """删除菜单"""
    menu = SysMenu.query.get_or_404(id)
    
    if menu.children.count() > 0:
        flash('该菜单有子菜单，无法删除', 'error')
        return redirect(url_for('system.menus'))

    SysRoleMenu.query.filter_by(menu_id=id).delete()
    parent_id = menu.parent_id
    db.session.delete(menu)
    db.session.commit()
    _reorder_menus(parent_id)
    flash('菜单已删除', 'success')
    return redirect(url_for('system.menus'))


@bp.route('/menus/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='启用/禁用菜单')
def toggle_menu(id):
    """启用/禁用菜单"""
    menu = SysMenu.query.get_or_404(id)
    menu.status = not menu.status
    db.session.commit()
    flash(f'菜单已{"启用" if menu.status else "禁用"}', 'success')
    return redirect(url_for('system.menus'))


# ============== API接口 ==============

@bp.route('/api/depts/tree')
@login_required
def api_dept_tree():
    """获取部门树形数据（按数据权限过滤）

    数据权限过滤规则：
    - all 或 admin：返回全部部门树
    - dept_and_sub：返回当前用户部门及其所有子部门
    - dept：只返回当前用户所在部门
    - self：只返回当前用户所在部门
    - custom：返回自定义部门ID列表对应的部门及其子部门树

    参数：
    - admin_only: 1=只返回行政部门（排除项目部），默认0=全部
    """
    admin_only = request.args.get('admin_only', '0') == '1'

    # 查询所有部门（包含禁用状态），保留原有的排序逻辑
    query = SysDept.query
    if admin_only:
        # 只返回行政部门类型：company/branch/dept/team
        query = query.filter(SysDept.dept_type.in_(['company', 'branch', 'dept', 'team']))
    all_depts = query.order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()

    # ========== 数据权限过滤：计算有权限的部门ID集合 ==========
    allowed_dept_ids = None  # None 表示全部权限

    # 管理员或全部数据权限：直接返回全量
    if current_user.is_admin() or current_user.get_data_scope() == 'all':
        allowed_dept_ids = None  # 表示不过滤
    else:
        data_scope = current_user.get_data_scope()
        user_dept_id = current_user.dept_id

        # 初始化有权限的部门ID集合
        allowed_dept_ids = set()

        if data_scope == 'dept_and_sub' and user_dept_id:
            # 本部门及下级：包含当前用户部门及其所有子部门
            user_dept = SysDept.query.get(user_dept_id)
            if user_dept:
                # 递归获取所有子部门ID（包含自身）
                allowed_dept_ids = set(user_dept.get_children_recursive())

        elif data_scope == 'dept' and user_dept_id:
            # 本部门：只包含当前用户所在部门
            allowed_dept_ids = {user_dept_id}

        elif data_scope == 'self' and user_dept_id:
            # 仅本人：部门树层面只显示自己所在部门
            allowed_dept_ids = {user_dept_id}

        elif data_scope == 'custom':
            # 自定义数据权限：从 SysRoleDataScope.custom_depts 读取自定义部门ID列表
            if current_user.role_obj:
                scope_cfg = SysRoleDataScope.query.filter_by(role_id=current_user.role_obj.id).first()
                if scope_cfg and scope_cfg.custom_depts:
                    import json as _json
                    try:
                        custom_dept_ids = [int(x) for x in _json.loads(scope_cfg.custom_depts) if str(x).isdigit()]
                    except Exception:
                        # 兜底：逗号分隔格式
                        custom_dept_ids = [int(x.strip()) for x in scope_cfg.custom_depts.split(',') if x.strip().isdigit()]

                    # 自定义部门需要包含其所有子部门，确保树结构完整
                    for dept_id in custom_dept_ids:
                        dept = SysDept.query.get(dept_id)
                        if dept:
                            allowed_dept_ids.update(dept.get_children_recursive())

    # 过滤出有权限的部门列表
    if allowed_dept_ids is not None:
        depts = [d for d in all_depts if d.id in allowed_dept_ids]
    else:
        depts = all_depts
    # ============================================================

    # 构建部门树（只包含有权限的部门节点）
    # 找根节点：父节点不在当前权限列表中的部门（即顶层可见部门）
    dept_ids_set = {d.id for d in depts}

    def build_tree(parent_id):
        children = []
        for d in depts:
            if d.parent_id == parent_id:
                node = {
                    'id': d.id,
                    'label': d.dept_name,
                    'name': d.dept_name,
                    'code': d.dept_code,
                    'dept_type': d.dept_type,
                    'leader': d.leader or '',
                    'status': d.status,
                    'sort': d.sort,
                    'children': build_tree(d.id)
                }
                children.append(node)
        return children

    # 如果是全量权限，从parent_id=0开始；否则从顶层可见部门开始
    if allowed_dept_ids is None:
        tree = build_tree(0)
    else:
        # 找到所有根节点（父部门不在可见范围内的部门）
        tree = []
        for d in depts:
            if d.parent_id not in dept_ids_set:
                node = {
                    'id': d.id,
                    'label': d.dept_name,
                    'name': d.dept_name,
                    'code': d.dept_code,
                    'dept_type': d.dept_type,
                    'leader': d.leader or '',
                    'status': d.status,
                    'sort': d.sort,
                    'children': build_tree(d.id)
                }
                tree.append(node)

    return jsonify(tree)


@bp.route('/api/roles')
@login_required
def api_roles():
    """获取角色列表"""
    roles = SysRole.query.filter_by(status=True).order_by(SysRole.sort).all()
    return jsonify([{'id': r.id, 'label': r.role_name, 'code': r.role_code} for r in roles])


@bp.route('/api/menus/tree')
@login_required
def api_menu_tree():
    """获取菜单树形数据"""
    menus = SysMenu.query.order_by(SysMenu.sort).all()
    
    def build_tree(parent_id):
        children = []
        for m in menus:
            if m.parent_id == parent_id:
                node = {
                    'id': m.id,
                    'label': m.menu_name,
                    'type': m.menu_type,
                    'permission': m.permission,
                    'children': build_tree(m.id)
                }
                children.append(node)
        return children
    
    return jsonify(build_tree(0))


@bp.route('/api/user/has_permission/<permission>')
@login_required
def api_has_permission(permission):
    """检查用户是否有指定权限"""
    if current_user.is_admin():
        return jsonify({'has_permission': True})
    
    if not current_user.role_id:
        return jsonify({'has_permission': False})
    
    menu = SysMenu.query.filter_by(permission=permission).first()
    if not menu:
        return jsonify({'has_permission': False})
    
    exists = SysRoleMenu.query.filter_by(role_id=current_user.role_id, menu_id=menu.id).first()
    return jsonify({'has_permission': exists is not None})


@bp.route('/roles/<int:id>/copy', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='系统管理', operation='复制角色')
def copy_role(id):
    src_role = SysRole.query.get_or_404(id)
    if request.method == 'POST':
        role_name = request.form.get('role_name', '').strip()
        if not role_name:
            flash('角色名称不能为空', 'error')
            return redirect(url_for('system.copy_role', id=id))

        role_code = gen_role_code()
        max_sort = db.session.query(db.func.max(SysRole.sort)).scalar() or 0
        new_role = SysRole(
            role_code=role_code,
            role_name=role_name,
            data_scope=src_role.data_scope,
            sort=max_sort + 1,
            remark=src_role.remark
        )
        db.session.add(new_role)
        db.session.flush()

        src_menus = SysRoleMenu.query.filter_by(role_id=src_role.id).all()
        for sm in src_menus:
            db.session.add(SysRoleMenu(role_id=new_role.id, menu_id=sm.menu_id))

        if src_role.data_scope == 'custom':
            src_depts = SysRoleDept.query.filter_by(role_id=src_role.id).all()
            for sd in src_depts:
                db.session.add(SysRoleDept(role_id=new_role.id, dept_id=sd.dept_id))

        db.session.commit()
        flash('角色复制成功', 'success')
        return redirect(url_for('system.roles'))

    default_role_code = gen_role_code()
    return render_template('system/role_copy.html', src_role=src_role, default_role_code=default_role_code)


# ============== 动态权限接口 ==============

@bp.route('/api/user/menus')
@login_required
def api_user_menus():
    """动态菜单接口 - 返回当前用户有权限的菜单树"""
    from app.models import SysModule

    # 获取启用的模块列表
    enabled_modules = set()
    for m in SysModule.query.filter_by(status=True).all():
        enabled_modules.add(m.module_key)

    # 获取用户有权限的菜单
    allowed_menu_ids = set()
    if current_user.is_admin():
        allowed_menu_ids = set(m.id for m in SysMenu.query.all())
    else:
        rms = SysRoleMenu.query.filter_by(
            role_id=current_user.role_id,
            operation='view'
        ).all()
        allowed_menu_ids = set(rm.menu_id for rm in rms)

    # 构建菜单树
    def build_tree(parent_id=None):
        items = []
        menus = SysMenu.query.filter_by(
            parent_id=parent_id,
            status=True
        ).order_by(SysMenu.sort).all()

        for menu in menus:
            # 检查模块是否启用
            if menu.module_key and menu.module_key not in enabled_modules:
                continue

            # 目录：只要有子菜单有权限就显示
            if menu.menu_type == 'catalog':
                children = build_tree(menu.id)
                if children:
                    items.append({
                        'id': menu.id,
                        'name': menu.menu_name,
                        'code': menu.menu_code,
                        'icon': menu.icon or '',
                        'path': menu.path or '',
                        'type': menu.menu_type,
                        'children': children,
                    })
            # 菜单：需要view权限
            elif menu.menu_type == 'menu':
                if current_user.is_admin() or menu.id in allowed_menu_ids:
                    items.append({
                        'id': menu.id,
                        'name': menu.menu_name,
                        'code': menu.menu_code,
                        'icon': menu.icon or '',
                        'path': menu.path or '',
                        'type': menu.menu_type,
                        'permission': menu.permission,
                    })
        return items

    menu_tree = build_tree()
    return jsonify({
        'code': 200,
        'data': menu_tree,
        'message': 'success'
    })


@bp.route('/api/user/permissions')
@login_required
def api_user_permissions():
    """返回当前用户的所有权限标识列表"""
    permissions = []

    if current_user.is_admin():
        # 管理员拥有所有权限
        menus = SysMenu.query.filter(SysMenu.permission.isnot(None)).all()
        permissions = [m.permission for m in menus]
    else:
        # 查询角色的所有权限
        rms = SysRoleMenu.query.filter_by(role_id=current_user.role_id).all()
        menu_ids = set(rm.menu_id for rm in rms)
        menus = SysMenu.query.filter(SysMenu.id.in_(menu_ids)).all()
        menu_map = {m.id: m for m in menus}

        for rm in rms:
            menu = menu_map.get(rm.menu_id)
            if menu and menu.permission:
                # 替换操作类型
                parts = menu.permission.rsplit(':', 1)
                if len(parts) == 2:
                    perm = f"{parts[0]}:{rm.operation}"
                    permissions.append(perm)

    return jsonify({
        'code': 200,
        'data': list(set(permissions)),
        'message': 'success'
    })


def permission_required(permission):
    """后端权限校验装饰器

    用法: @permission_required('stock:in:create')
    无权限返回403
    """
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({'code': 401, 'message': '未登录'}), 401
            if current_user.is_admin():
                return f(*args, **kwargs)

            # 解析权限标识
            parts = permission.rsplit(':', 1)
            if len(parts) != 2:
                return jsonify({'code': 403, 'message': '权限格式错误'}), 403

            perm_base, operation = parts

            # 查找对应的菜单
            menus = SysMenu.query.filter(
                SysMenu.permission.like(f'{perm_base}%')
            ).all()

            if not menus:
                return jsonify({'code': 403, 'message': '权限未配置'}), 403

            menu_ids = [m.id for m in menus]
            has_perm = SysRoleMenu.query.filter(
                SysRoleMenu.role_id == current_user.role_id,
                SysRoleMenu.menu_id.in_(menu_ids),
                SysRoleMenu.operation == operation
            ).first()

            if not has_perm:
                return jsonify({'code': 403, 'message': '无权限访问'}), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ============== 模块管理 ==============

@bp.route('/modules')
@login_required
@admin_required
def modules():
    """系统级模块管理页面"""
    from app.models import SysModule
    modules = SysModule.query.order_by(SysModule.sort).all()
    return render_template('system/modules.html', modules=modules)


@bp.route('/modules/save', methods=['POST'])
@login_required
@admin_required
@log_audit(module='system', operation='保存模块配置')
def save_modules():
    """保存系统级模块开关配置"""
    from app.models import SysModule

    modules = SysModule.query.order_by(SysModule.sort).all()
    for module in modules:
        # 必需模块不可关闭
        if module.is_required:
            module.status = True
            continue

        field_name = f'module_{module.module_key}'
        enabled = request.form.get(field_name, 'off') == 'on'
        module.status = enabled

    db.session.commit()
    flash('模块配置已保存', 'success')
    return redirect(url_for('system.modules'))