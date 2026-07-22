from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from flask import session

from app.price_formula import bp
from app import db
from app.models import PriceFormula, Supplier, Category
from app.decorators import editor_required, log_audit


@bp.route('/')
@login_required
def index():
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    formulas = PriceFormula.query.filter_by(project_id=project_id).order_by(PriceFormula.created_at.desc()).all()
    return render_template('price_formula/index.html', formulas=formulas)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='price_formula', operation='新增')
def create():
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        float_type_map = {'不下浮': 'none', '比例': 'ratio', '金额': 'amount'}
        float_type = float_type_map.get(request.form.get('discount_type', '不下浮'), 'none')
        float_value = request.form.get('discount_value', type=float) or 0
        
        formula = PriceFormula(
            project_id=project_id,
            formula_name=request.form.get('formula_name', '').strip(),
            supplier_id=request.form.get('supplier_id', type=int) or None,
            material_category_id=request.form.get('material_category_id', type=int) or None,
            base_price_type=request.form.get('base_price_type', '手动输入'),
            float_type=float_type,
            float_value=float_value,
            service_fee_rate=request.form.get('service_fee_rate', type=float) or 0,
            service_fee_fixed=request.form.get('service_fee_fixed', type=float) or 0,
            capital_fee_rate=request.form.get('capital_fee_rate', type=float) or 0,
            capital_fee_days=request.form.get('capital_fee_days', type=int) or None,
            tax_rate=request.form.get('tax_rate', type=float) or 13,
            tax_included=request.form.get('tax_included') == '1',
            status=request.form.get('status', '启用'),
            remark=request.form.get('remark', '').strip() or None
        )
        db.session.add(formula)
        db.session.commit()
        flash('价格方案创建成功。', 'success')
        return redirect(url_for('price_formula.index'))

    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    categories = Category.query.filter_by(project_id=project_id, level=1).order_by(Category.sort_order).all()
    return render_template('price_formula/form.html', formula=None, suppliers=suppliers, categories=categories)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='price_formula', operation='编辑')
def edit(id):
    formula = PriceFormula.query.get_or_404(id)
    if request.method == 'POST':
        float_type_map = {'不下浮': 'none', '比例': 'ratio', '金额': 'amount'}
        float_type = float_type_map.get(request.form.get('discount_type', '不下浮'), 'none')
        
        formula.formula_name = request.form.get('formula_name', '').strip()
        formula.supplier_id = request.form.get('supplier_id', type=int) or None
        formula.material_category_id = request.form.get('material_category_id', type=int) or None
        formula.base_price_type = request.form.get('base_price_type', '手动输入')
        formula.float_type = float_type
        formula.float_value = request.form.get('discount_value', type=float) or 0
        formula.service_fee_rate = request.form.get('service_fee_rate', type=float) or 0
        formula.service_fee_fixed = request.form.get('service_fee_fixed', type=float) or 0
        formula.capital_fee_rate = request.form.get('capital_fee_rate', type=float) or 0
        formula.capital_fee_days = request.form.get('capital_fee_days', type=int) or None
        formula.tax_rate = request.form.get('tax_rate', type=float) or 13
        formula.tax_included = request.form.get('tax_included') == '1'
        formula.status = request.form.get('status', '启用')
        formula.remark = request.form.get('remark', '').strip() or None
        db.session.commit()
        flash('价格方案已更新。', 'success')
        return redirect(url_for('price_formula.index'))

    project_id = session.get('current_project_id')
    suppliers = Supplier.query.filter_by(project_id=project_id).order_by(Supplier.name).all()
    categories = Category.query.filter_by(project_id=project_id, level=1).order_by(Category.sort_order).all()
    return render_template('price_formula/form.html', formula=formula, suppliers=suppliers, categories=categories)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='price_formula', operation='删除')
def delete(id):
    formula = PriceFormula.query.get_or_404(id)
    db.session.delete(formula)
    db.session.commit()
    flash('价格方案已删除。', 'success')
    return redirect(url_for('price_formula.index'))


@bp.route('/api/by_supplier/<int:supplier_id>')
@login_required
def api_by_supplier(supplier_id):
    """获取指定供应商的价格方案列表"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])
    formulas = PriceFormula.query.filter_by(
        project_id=project_id, supplier_id=supplier_id, status='启用'
    ).all()
    # 包含通用方案（supplier_id 为空）
    general = PriceFormula.query.filter_by(
        project_id=project_id, supplier_id=None, status='启用'
    ).all()
    formulas.extend(general)
    return jsonify([{
        'id': f.id,
        'formula_name': f.formula_name,
        'base_price_type': f.base_price_type,
        'float_type': f.float_type,
        'float_value': float(f.float_value or 0),
        'service_fee_rate': float(f.service_fee_rate or 0),
        'service_fee_fixed': float(f.service_fee_fixed or 0),
        'capital_fee_rate': float(f.capital_fee_rate or 0),
        'capital_fee_days': f.capital_fee_days,
        'tax_rate': float(f.tax_rate or 13),
        'tax_included': f.tax_included,
    } for f in formulas])


@bp.route('/api/<int:id>/toggle_status', methods=['POST'])
@login_required
@editor_required
@log_audit(module='price_formula', operation='切换状态')
def toggle_status(id):
    formula = PriceFormula.query.get_or_404(id)
    formula.status = '禁用' if formula.status == '启用' else '启用'
    db.session.commit()
    return jsonify({'success': True, 'status': formula.status})
