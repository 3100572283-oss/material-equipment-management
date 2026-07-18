"""RBAC权限管理 - 路由层"""
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from app import db
from app.system import bp
from app.models import SysDept, SysRole, SysMenu, SysRoleMenu, SysRoleDept, User
from app.decorators import admin_required, log_audit


TYPE_LABELS = {
    'catalog': '目录',
    'menu': '菜单',
    'button': '按钮',
}


@bp.context_processor
def inject_type_labels():
    return {'TYPE_LABELS': TYPE_LABELS}


# ============== 部门管理 ==============

@bp.route('/depts')
@login_required
@admin_required
def depts():
    """部门列表页"""
    depts_list = SysDept.query.order_by(SysDept.sort).all()
    return render_template('system/depts.html', depts=depts_list)


@bp.route('/depts/create', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='新增部门')
def create_dept():
    """新增部门"""
    if request.method == 'POST':
        dept_name = request.form.get('dept_name', '').strip()
        dept_code = request.form.get('dept_code', '').strip()
        parent_id = request.form.get('parent_id', type=int, default=0)
        sort = request.form.get('sort', type=int, default=0)
        remark = request.form.get('remark', '').strip()

        if not dept_name or not dept_code:
            flash('部门名称和编码不能为空', 'error')
            return redirect(url_for('system.create_dept'))

        if SysDept.query.filter_by(dept_code=dept_code).first():
            flash('部门编码已存在', 'error')
            return redirect(url_for('system.create_dept'))

        dept = SysDept(
            dept_code=dept_code,
            dept_name=dept_name,
            parent_id=parent_id if parent_id else 0,
            sort=sort,
            remark=remark or None
        )
        db.session.add(dept)
        db.session.commit()
        flash('部门创建成功', 'success')
        return redirect(url_for('system.depts'))

    all_depts = SysDept.query.order_by(SysDept.sort).all()
    return render_template('system/dept_form.html', dept=None, all_depts=all_depts)


@bp.route('/depts/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='编辑部门')
def edit_dept(id):
    """编辑部门"""
    dept = SysDept.query.get_or_404(id)
    if request.method == 'POST':
        dept_name = request.form.get('dept_name', '').strip()
        dept_code = request.form.get('dept_code', '').strip()
        parent_id = request.form.get('parent_id', type=int, default=0)
        sort = request.form.get('sort', type=int, default=0)
        remark = request.form.get('remark', '').strip()

        if not dept_name or not dept_code:
            flash('部门名称和编码不能为空', 'error')
            return redirect(url_for('system.edit_dept', id=id))

        if SysDept.query.filter(SysDept.dept_code == dept_code, SysDept.id != id).first():
            flash('部门编码已存在', 'error')
            return redirect(url_for('system.edit_dept', id=id))

        dept.dept_name = dept_name
        dept.dept_code = dept_code
        dept.parent_id = parent_id if parent_id else 0
        dept.sort = sort
        dept.remark = remark or None
        db.session.commit()
        flash('部门已更新', 'success')
        return redirect(url_for('system.depts'))

    all_depts = SysDept.query.order_by(SysDept.sort).all()
    return render_template('system/dept_form.html', dept=dept, all_depts=all_depts)


@bp.route('/depts/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='删除部门')
def delete_dept(id):
    """删除部门"""
    dept = SysDept.query.get_or_404(id)
    
    if dept.children.count() > 0:
        flash('该部门有子部门，无法删除', 'error')
        return redirect(url_for('system.depts'))
    
    if dept.users.count() > 0:
        flash('该部门下有用户，无法删除', 'error')
        return redirect(url_for('system.depts'))

    db.session.delete(dept)
    db.session.commit()
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
        role_code = request.form.get('role_code', '').strip()
        role_name = request.form.get('role_name', '').strip()
        data_scope = request.form.get('data_scope', 'all')
        sort = request.form.get('sort', type=int, default=0)
        remark = request.form.get('remark', '').strip()

        if not role_code or not role_name:
            flash('角色编码和名称不能为空', 'error')
            return redirect(url_for('system.create_role'))

        if SysRole.query.filter_by(role_code=role_code).first():
            flash('角色编码已存在', 'error')
            return redirect(url_for('system.create_role'))

        role = SysRole(
            role_code=role_code,
            role_name=role_name,
            data_scope=data_scope,
            sort=sort,
            remark=remark or None
        )
        db.session.add(role)
        db.session.commit()
        flash('角色创建成功', 'success')
        return redirect(url_for('system.roles'))

    return render_template('system/role_form.html', role=None)


@bp.route('/roles/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='编辑角色')
def edit_role(id):
    """编辑角色"""
    role = SysRole.query.get_or_404(id)
    if request.method == 'POST':
        role_code = request.form.get('role_code', '').strip()
        role_name = request.form.get('role_name', '').strip()
        data_scope = request.form.get('data_scope', 'all')
        sort = request.form.get('sort', type=int, default=0)
        remark = request.form.get('remark', '').strip()

        if not role_code or not role_name:
            flash('角色编码和名称不能为空', 'error')
            return redirect(url_for('system.edit_role', id=id))

        if SysRole.query.filter(SysRole.role_code == role_code, SysRole.id != id).first():
            flash('角色编码已存在', 'error')
            return redirect(url_for('system.edit_role', id=id))

        role.role_code = role_code
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
    
    checked_menu_ids = [rm.menu_id for rm in SysRoleMenu.query.filter_by(role_id=id).all()]
    
    return render_template('system/role_permissions.html',
                           role=role, menus=menus, checked_ids=checked_menu_ids)


@bp.route('/roles/<int:id>/permissions/save', methods=['POST'])
@login_required
@admin_required
@log_audit(module='rbac', operation='保存角色权限')
def save_role_permissions(id):
    """保存角色菜单权限"""
    menu_ids = request.form.getlist('menu_ids')
    
    SysRoleMenu.query.filter_by(role_id=id).delete()
    
    for menu_id in menu_ids:
        try:
            rm = SysRoleMenu(role_id=id, menu_id=int(menu_id))
            db.session.add(rm)
        except ValueError:
            pass
    
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
    depts = SysDept.query.order_by(SysDept.sort).all()
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
        sort = request.form.get('sort', type=int, default=0)
        permission = request.form.get('permission', '').strip()
        remark = request.form.get('remark', '').strip()

        if not menu_name:
            flash('菜单名称不能为空', 'error')
            return redirect(url_for('system.create_menu'))

        if menu_code and SysMenu.query.filter_by(menu_code=menu_code).first():
            flash('菜单编码已存在', 'error')
            return redirect(url_for('system.create_menu'))

        menu = SysMenu(
            menu_name=menu_name,
            menu_code=menu_code or None,
            parent_id=parent_id if parent_id else 0,
            menu_type=menu_type,
            path=path or None,
            icon=icon or None,
            sort=sort,
            permission=permission or None,
            remark=remark or None
        )
        db.session.add(menu)
        db.session.commit()
        flash('菜单创建成功', 'success')
        return redirect(url_for('system.menus'))

    all_menus = SysMenu.query.order_by(SysMenu.sort).all()
    return render_template('system/menu_form.html', menu=None, all_menus=all_menus)


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
    db.session.delete(menu)
    db.session.commit()
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
    depts = SysDept.query.filter_by(status=True).order_by(SysDept.sort).all()
    
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
        role_code = request.form.get('role_code', '').strip()
        role_name = request.form.get('role_name', '').strip()
        if not role_code or not role_name:
            flash('角色编码和名称不能为空', 'error')
            return redirect(url_for('system.copy_role', id=id))
        if SysRole.query.filter_by(role_code=role_code).first():
            flash('角色编码已存在', 'error')
            return redirect(url_for('system.copy_role', id=id))

        new_role = SysRole(
            role_code=role_code,
            role_name=role_name,
            data_scope=src_role.data_scope,
            sort=src_role.sort,
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

    return render_template('system/role_copy.html', src_role=src_role)