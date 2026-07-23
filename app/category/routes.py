from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required
from app.category import bp
from app import db
from app.models import Category, Material
from app.decorators import editor_required, log_audit


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
    """项目级物资分类页面：直接引用公司级统一分类（只读）。

    物资分类由公司统一管理（/master/category），所有项目共享同一套分类，
    项目级不再支持独立的增删改，需到公司级主数据管理中操作。
    """
    categories = Category.query.filter_by(source='company').order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()
    tree = _build_category_tree(categories)
    return render_template('category/index.html', tree=tree, categories=categories)


# ============ 以下写操作均重定向到公司级主数据管理 ============

@bp.route('/create', methods=['POST'])
@login_required
@editor_required
def create():
    flash('物资分类由公司统一管理，请在「主数据管理 - 物资分类」中操作。', 'info')
    return redirect(url_for('master.category_index'))


@bp.route('/batch_create', methods=['POST'])
@login_required
@editor_required
def batch_create():
    flash('物资分类由公司统一管理，请在「主数据管理 - 物资分类」中操作。', 'info')
    return redirect(url_for('master.category_index'))


@bp.route('/<int:id>/edit', methods=['POST'])
@login_required
@editor_required
def edit(id):
    flash('物资分类由公司统一管理，请在「主数据管理 - 物资分类」中操作。', 'info')
    return redirect(url_for('master.category_index'))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
def delete(id):
    flash('物资分类由公司统一管理，请在「主数据管理 - 物资分类」中操作。', 'info')
    return redirect(url_for('master.category_index'))


@bp.route('/api/tree')
@login_required
def api_tree():
    """返回公司级统一分类树JSON（所有项目共享）。"""
    categories = Category.query.filter_by(source='company').order_by(
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
    """返回指定父级的子分类列表JSON（公司级统一分类）。"""
    query = Category.query.filter_by(source='company')
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
