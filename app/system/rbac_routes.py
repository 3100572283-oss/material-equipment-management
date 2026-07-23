"""RBAC权限管理 - 路由层"""
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime

from app import db
from app.system import bp
from app.models import SysDept, SysRole, SysMenu, SysRoleMenu, SysRoleDept, User, Project
from app.decorators import admin_required, log_audit
from app.utils import gen_dept_code, gen_project_code, gen_role_code
import json as _json


TYPE_LABELS = {
    'catalog': '目录',
    'menu': '菜单',
    'button': '按钮',
}

DEPT_TYPE_LABELS = {
    'company': '公司',
    'branch': '分公司',
    'project': '项目部',
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
                if not endpoint:
                    continue
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
                    if endpoint and allowed_endpoints and endpoint not in allowed_endpoints:
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
                    sys_group_map[group_name].append({
                        'title': menu.menu_name,
                        'endpoint': endpoint,
                        'active': item_extra.get('active', endpoint or ''),
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
@admin_required
def depts():
    """组织架构管理页"""
    all_depts = SysDept.query.order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()
    all_projects = Project.query.filter_by(is_archived=False).all()
    return render_template('system/depts.html', depts=all_depts, all_projects=all_projects)


@bp.route('/depts/create', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='新增部门')
def create_dept():
    """新增部门"""
    if request.method == 'POST':
        dept_name = request.form.get('dept_name', '').strip()
        parent_id = request.form.get('parent_id', type=int, default=0)
        dept_type = request.form.get('dept_type', 'dept')
        project_id = request.form.get('project_id', type=int, default=0)
        leader = request.form.get('leader', '').strip()
        sort_input = request.form.get('sort', type=int, default=None)
        remark = request.form.get('remark', '').strip()

        # 项目相关字段
        project_name = request.form.get('project_name', '').strip()
        project_address = request.form.get('project_address', '').strip()
        project_manager = request.form.get('project_manager', '').strip()
        project_phone = request.form.get('project_phone', '').strip()
        start_date = request.form.get('start_date', '').strip() or None
        planned_end_date = request.form.get('planned_end_date', '').strip() or None
        project_status = request.form.get('project_status', 'active')
        building_area = request.form.get('building_area', type=float, default=0)
        contract_amount = request.form.get('contract_amount', type=float, default=0)
        project_type = request.form.get('project_type', '').strip()

        if not dept_name:
            flash('部门名称不能为空', 'error')
            return redirect(url_for('system.create_dept'))

        # 自动生成部门编码
        dept_code = gen_dept_code()

        if dept_type == 'project':
            # 项目部类型：同步创建项目
            if not project_name:
                project_name = dept_name

            # 自动生成项目编码
            project_code = gen_project_code()

            new_project = Project(
                name=project_name,
                code=project_code,
                address=project_address or None,
                manager=project_manager or None,
                contact_phone=project_phone or None,
                start_date=datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None,
                planned_end_date=datetime.strptime(planned_end_date, '%Y-%m-%d').date() if planned_end_date else None,
                status=project_status,
                building_area=building_area,
                contract_amount=contract_amount,
                project_type=project_type or None,
            )
            db.session.add(new_project)
            db.session.flush()
            project_id = new_project.id

        if sort_input is None:
            max_sort = db.session.query(db.func.max(SysDept.sort)).filter_by(
                parent_id=parent_id if parent_id else 0).scalar() or 0
            sort_input = max_sort + 1

        dept = SysDept(
            dept_code=dept_code,
            dept_name=dept_name,
            parent_id=parent_id if parent_id else 0,
            dept_type=dept_type,
            project_id=project_id if project_id and dept_type == 'project' else None,
            leader=leader or None,
            sort=sort_input,
            remark=remark or None
        )
        db.session.add(dept)
        db.session.commit()
        flash('部门创建成功', 'success')
        return redirect(url_for('system.depts'))

    all_depts = SysDept.query.order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()
    all_projects = Project.query.filter_by(is_archived=False).all()

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
        while current.parent_id != 0:
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
    parent_id_param = request.args.get('parent_id', type=int, default=0)
    max_sort = db.session.query(db.func.max(SysDept.sort)).filter_by(
        parent_id=parent_id_param if parent_id_param else 0).scalar() or 0
    default_sort = max_sort + 1

    parent_dept = dept_map.get(parent_id_param) if parent_id_param else None

    return render_template('system/dept_form.html', dept=None, all_depts=all_depts, all_projects=all_projects,
                           default_sort=default_sort, parent_id=parent_id_param, parent_dept=parent_dept,
                           DEPT_TYPE_LABELS=SysDept._DEPT_TYPE_MAP if hasattr(SysDept, '_DEPT_TYPE_MAP') else {})


@bp.route('/depts/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='编辑部门')
def edit_dept(id):
    """编辑部门"""
    dept = SysDept.query.get_or_404(id)
    if request.method == 'POST':
        dept_name = request.form.get('dept_name', '').strip()
        parent_id = request.form.get('parent_id', type=int, default=0)
        dept_type = request.form.get('dept_type', 'dept')
        project_id = request.form.get('project_id', type=int, default=0)
        leader = request.form.get('leader', '').strip()
        sort = request.form.get('sort', type=int, default=dept.sort)
        remark = request.form.get('remark', '').strip()

        # 项目相关字段
        project_name = request.form.get('project_name', '').strip()
        project_address = request.form.get('project_address', '').strip()
        project_manager = request.form.get('project_manager', '').strip()
        project_phone = request.form.get('project_phone', '').strip()
        start_date = request.form.get('start_date', '').strip() or None
        planned_end_date = request.form.get('planned_end_date', '').strip() or None
        project_status = request.form.get('project_status', 'active')
        building_area = request.form.get('building_area', type=float, default=0)
        contract_amount = request.form.get('contract_amount', type=float, default=0)
        project_type = request.form.get('project_type', '').strip()

        if not dept_name:
            flash('部门名称不能为空', 'error')
            return redirect(url_for('system.edit_dept', id=id))

        # 不能将部门设为自己的子部门
        if parent_id == id:
            flash('不能将上级部门设为自己', 'error')
            return redirect(url_for('system.edit_dept', id=id))

        dept.dept_name = dept_name
        dept.parent_id = parent_id if parent_id else 0
        dept.dept_type = dept_type
        dept.leader = leader or None
        dept.sort = sort
        dept.remark = remark or None

        if dept_type == 'project':
            # 项目部类型：同步更新或创建项目
            if dept.project_id and Project.query.get(dept.project_id):
                # 更新已有项目
                project = Project.query.get(dept.project_id)
                project.name = project_name if project_name else dept_name
                project.address = project_address or None
                project.manager = project_manager or None
                project.contact_phone = project_phone or None
                project.start_date = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
                project.planned_end_date = datetime.strptime(planned_end_date, '%Y-%m-%d').date() if planned_end_date else None
                project.status = project_status
                project.building_area = building_area
                project.contract_amount = contract_amount
                project.project_type = project_type or None
                project_id = project.id
            else:
                # 创建新项目
                project_code = gen_project_code()
                new_project = Project(
                    name=project_name if project_name else dept_name,
                    code=project_code,
                    address=project_address or None,
                    manager=project_manager or None,
                    contact_phone=project_phone or None,
                    start_date=datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None,
                    planned_end_date=datetime.strptime(planned_end_date, '%Y-%m-%d').date() if planned_end_date else None,
                    status=project_status,
                    building_area=building_area,
                    contract_amount=contract_amount,
                    project_type=project_type or None,
                )
                db.session.add(new_project)
                db.session.flush()
                project_id = new_project.id
            dept.project_id = project_id
        else:
            # 非项目部类型清空项目关联
            dept.project_id = None

        db.session.commit()
        flash('部门已更新', 'success')
        return redirect(url_for('system.depts'))

    all_depts = SysDept.query.order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()
    all_projects = Project.query.filter_by(is_archived=False).all()

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
        while current.parent_id != 0:
            depth += 1
            if current.parent_id in dept_map:
                current = dept_map[current.parent_id]
            else:
                break
        return depth

    for d in all_depts:
        d._depth = get_dept_depth(d)
        d._path = get_dept_path(d)

    return render_template('system/dept_form.html', dept=dept, all_depts=all_depts, all_projects=all_projects,
                           parent_id=dept.parent_id, parent_dept=dept_map.get(dept.parent_id) if dept.parent_id else None,
                           DEPT_TYPE_LABELS=SysDept._DEPT_TYPE_MAP if hasattr(SysDept, '_DEPT_TYPE_MAP') else {})


@bp.route('/depts/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='删除部门')
def delete_dept(id):
    """删除部门"""
    dept = SysDept.query.get_or_404(id)

    if dept.children.count() > 0:
        flash('该部门有子部门，请先转移子部门后再删除', 'error')
        return redirect(url_for('system.depts'))

    if dept.users.count() > 0:
        flash('该部门下有用户，请先转移用户后再删除', 'error')
        return redirect(url_for('system.depts'))

    # 项目部类型：归档对应项目而非物理删除
    if dept.dept_type == 'project' and dept.project_id:
        project = Project.query.get(dept.project_id)
        if project:
            project.is_archived = True

    parent_id = dept.parent_id
    db.session.delete(dept)
    db.session.commit()
    _reorder_depts(parent_id)
    flash('部门已删除', 'success')
    return redirect(url_for('system.depts'))


@bp.route('/depts/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='启用/禁用部门')
def toggle_dept(id):
    """启用/禁用部门"""
    dept = SysDept.query.get_or_404(id)
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
    """角色菜单权限配置页"""
    role = SysRole.query.get_or_404(id)
    
    menus = SysMenu.query.filter(SysMenu.parent_id == 0).order_by(SysMenu.sort).all()
    
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
    
    def build_menu_tree(items, parent_id=0, level=0):
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
    
    return render_template('system/role_permissions.html',
                           role=role, menu_tree=menu_tree, 
                           checked_ids=checked_menu_ids, checked_set=checked_set,
                           menu_op_map=menu_op_map,
                           operations=all_operations, op_labels=op_labels)


@bp.route('/roles/<int:id>/permissions/save', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='保存角色权限')
def save_role_permissions(id):
    """保存角色菜单权限"""
    menu_ops_raw = request.form.getlist('menu_ops')
    
    unique_ops = set()
    for item in menu_ops_raw:
        if ':' in item:
            parts = item.split(':')
            if len(parts) == 2:
                try:
                    menu_id = int(parts[0])
                    operation = parts[1]
                    unique_ops.add((menu_id, operation))
                except ValueError:
                    pass
    
    SysRoleMenu.query.filter_by(role_id=id).delete()
    
    for menu_id, operation in unique_ops:
        rm = SysRoleMenu(role_id=id, menu_id=menu_id, operation=operation)
        db.session.add(rm)
    
    db.session.commit()
    flash('权限配置已保存', 'success')
    return redirect(url_for('system.role_permissions', id=id))


# ============== 数据权限配置 ==============

@bp.route('/roles/<int:id>/data_scope')
@login_required
@admin_required
def role_data_scope(id):
    """角色数据权限配置页"""
    role = SysRole.query.get_or_404(id)
    depts = SysDept.query.order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()
    selected_depts = [rd.dept_id for rd in SysRoleDept.query.filter_by(role_id=id).all()]
    return render_template('system/role_data_scope.html',
                           role=role, depts=depts, selected_depts=selected_depts)


@bp.route('/roles/<int:id>/data_scope/save', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='保存数据权限')
def save_role_data_scope(id):
    """保存角色数据权限"""
    role = SysRole.query.get_or_404(id)
    data_scope = request.form.get('data_scope', 'all')
    dept_ids = request.form.getlist('dept_ids')
    
    SysRoleDept.query.filter_by(role_id=id).delete()
    
    if data_scope == 'custom':
        for dept_id in dept_ids:
            try:
                rd = SysRoleDept(role_id=id, dept_id=int(dept_id))
                db.session.add(rd)
            except ValueError:
                pass
    
    role.data_scope = data_scope
    db.session.commit()
    flash('数据权限已保存', 'success')
    return redirect(url_for('system.role_data_scope', id=id))


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
                parent_id=parent_id if parent_id else 0).scalar() or 0
            sort_input = max_sort + 1

        menu = SysMenu(
            menu_name=menu_name,
            menu_code=menu_code or None,
            parent_id=parent_id if parent_id else 0,
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
    parent_id_param = request.args.get('parent_id', type=int, default=0)
    max_sort = db.session.query(db.func.max(SysMenu.sort)).filter_by(
        parent_id=parent_id_param).scalar() or 0
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
        menu.parent_id = parent_id if parent_id else 0
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
    """获取部门树形数据"""
    depts = SysDept.query.filter_by(status=True).order_by(SysDept.sort.asc(), SysDept.created_at.asc()).all()
    
    def build_tree(parent_id):
        children = []
        for d in depts:
            if d.parent_id == parent_id:
                node = {
                    'id': d.id,
                    'label': d.dept_name,
                    'code': d.dept_code,
                    'children': build_tree(d.id)
                }
                children.append(node)
        return children
    
    return jsonify(build_tree(0))


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