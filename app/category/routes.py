from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required
from app.category import bp
from app import db
from app.models import Category, Material
from app.decorators import editor_required, log_audit


def _gen_category_code(project_id, parent_id):
    """根据项目ID和父级ID生成分类编码。

    一级分类：MC + 2位序号（MC01）
    二级分类：父级编码 + 2位序号（MC0106）
    三级分类：父级编码 + 2位序号（MC010601）
    """
    if parent_id == 0:
        prefix = 'MC'
        existing = Category.query.filter(
            Category.project_id == project_id,
            Category.parent_id == 0
        ).all()
        seq = len(existing) + 1
        return f'{prefix}{seq:02d}'
    else:
        parent = Category.query.get(parent_id)
        if not parent:
            return None
        existing = Category.query.filter(
            Category.project_id == project_id,
            Category.parent_id == parent_id
        ).all()
        seq = len(existing) + 1
        return f'{parent.category_code}{seq:02d}'


def _build_category_tree(categories):
    """将扁平分类列表构建为树形结构，同时计算物资数量。"""
    nodes = {c.id: {'category': c, 'children': [], 'material_count': 0} for c in categories}
    roots = []
    
    for c in categories:
        node = nodes[c.id]
        if c.level == 3:
            node['material_count'] = Material.query.filter_by(category_id=c.id).count()
        else:
            node['material_count'] = len([child for child in categories if child.parent_id == c.id])
        
        if c.parent_id and c.parent_id in nodes:
            nodes[c.parent_id]['children'].append(node)
        else:
            roots.append(node)
    return roots


@bp.route('/')
@login_required
def index():
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    categories = Category.query.filter_by(project_id=project_id).order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()
    tree = _build_category_tree(categories)
    return render_template('category/index.html', tree=tree, categories=categories)


@bp.route('/create', methods=['POST'])
@login_required
@editor_required
@log_audit(module='category', operation='新增')
def create():
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    name = request.form.get('name', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int) or 0
    parent_id = request.form.get('parent_id', 0, type=int) or 0
    negative_stock_policy = request.form.get('negative_stock_policy', 'global')

    if not name:
        flash('分类名称不能为空。', 'danger')
        return redirect(url_for('category.index'))

    # 根据父级计算层级
    if parent_id == 0:
        level = 1
    else:
        parent = Category.query.get(parent_id)
        if not parent:
            flash('父级分类不存在。', 'danger')
            return redirect(url_for('category.index'))
        if parent.level >= 3:
            flash('最多支持三级分类，无法继续新增子分类。', 'danger')
            return redirect(url_for('category.index'))
        level = parent.level + 1

    category_code = _gen_category_code(project_id, parent_id)

    category = Category(
        project_id=project_id,
        parent_id=parent_id,
        level=level,
        category_code=category_code,
        name=name,
        sort_order=sort_order,
        negative_stock_policy=negative_stock_policy
    )
    db.session.add(category)
    db.session.commit()
    flash(f'分类创建成功，编码：{category_code}', 'success')
    return redirect(url_for('category.index'))


@bp.route('/batch_create', methods=['POST'])
@login_required
@editor_required
@log_audit(module='category', operation='批量新增')
def batch_create():
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    names = request.form.get('names', '').strip()
    parent_id = request.form.get('parent_id', 0, type=int) or 0
    start_sort = request.form.get('start_sort', 1, type=int) or 1

    if not names:
        flash('请输入分类名称。', 'danger')
        return redirect(url_for('category.index'))

    if parent_id != 0:
        parent = Category.query.get(parent_id)
        if not parent:
            flash('父级分类不存在。', 'danger')
            return redirect(url_for('category.index'))
        if parent.level >= 3:
            flash('最多支持三级分类，无法继续新增子分类。', 'danger')
            return redirect(url_for('category.index'))

    lines = [n.strip() for n in names.split('\n') if n.strip()]
    if not lines:
        flash('请输入有效的分类名称。', 'danger')
        return redirect(url_for('category.index'))

    count = 0
    sort_order = start_sort
    for name in lines:
        level = 1
        if parent_id != 0:
            level = parent.level + 1

        category_code = _gen_category_code(project_id, parent_id)

        category = Category(
            project_id=project_id,
            parent_id=parent_id,
            level=level,
            category_code=category_code,
            name=name,
            sort_order=sort_order
        )
        db.session.add(category)
        count += 1
        sort_order += 1

    db.session.commit()
    flash(f'成功创建 {count} 个分类。', 'success')
    return redirect(url_for('category.index'))


@bp.route('/<int:id>/edit', methods=['POST'])
@login_required
@editor_required
@log_audit(module='category', operation='编辑')
def edit(id):
    category = Category.query.get_or_404(id)
    name = request.form.get('name', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int) or 0
    negative_stock_policy = request.form.get('negative_stock_policy', 'global')
    if not name:
        flash('分类名称不能为空。', 'danger')
        return redirect(url_for('category.index'))
    category.name = name
    category.sort_order = sort_order
    category.negative_stock_policy = negative_stock_policy
    db.session.commit()
    flash('分类更新成功。', 'success')
    return redirect(url_for('category.index'))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='category', operation='删除')
def delete(id):
    category = Category.query.get_or_404(id)
    # 检查是否有子分类
    if Category.query.filter_by(parent_id=category.id).first():
        flash('该分类下有子分类，无法删除。请先删除子分类。', 'danger')
        return redirect(url_for('category.index'))
    # 检查是否有关联物资
    if Material.query.filter_by(category_id=category.id).first():
        flash('该分类下已有物资关联，无法删除。', 'danger')
        return redirect(url_for('category.index'))
    db.session.delete(category)
    db.session.commit()
    flash('分类删除成功。', 'success')
    return redirect(url_for('category.index'))


@bp.route('/api/tree')
@login_required
def api_tree():
    """返回当前项目的分类树JSON。"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])

    categories = Category.query.filter_by(project_id=project_id).order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()
    tree = _build_category_tree(categories)

    def serialize(nodes):
        result = []
        for node in nodes:
            c = node['category']
            result.append({
                'id': c.id,
                'name': c.name,
                'category_code': c.category_code,
                'level': c.level,
                'parent_id': c.parent_id,
                'sort_order': c.sort_order,
                'children': serialize(node['children'])
            })
        return result

    return jsonify(serialize(tree))


@bp.route('/api/children/<int:parent_id>')
@login_required
def api_children(parent_id):
    """返回指定父级的子分类列表JSON。"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])

    query = Category.query.filter_by(project_id=project_id)
    if parent_id == 0:
        query = query.filter_by(parent_id=0)
    else:
        query = query.filter_by(parent_id=parent_id)

    categories = query.order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()

    return jsonify([{
        'id': c.id,
        'name': c.name,
        'category_code': c.category_code,
        'level': c.level,
        'parent_id': c.parent_id,
        'sort_order': c.sort_order
    } for c in categories])
