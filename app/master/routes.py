"""公司级主数据管理路由

提供公司级物资主库、供应商主库、物资分类的管理功能。
所有路由均要求公司管理员权限（@admin_required）。

数据筛选规则：source='company' 表示公司级主库数据（跨项目共享）。
项目级 project_id 字段仅为兼容 NOT NULL 约束，实际过滤以 source 为准。
"""
import os
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
from flask_login import login_required, current_user
from sqlalchemy import or_
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from app.master import master_bp
from app import db
from app.models import (
    Material, Supplier, Category, Project,
    ContractItem, StockInItem, StockOutItem, Inventory, Contract,
)
from app.decorators import admin_required, log_audit
from app.utils import (
    _code_gen_lock, _CODE_GEN_MAX_RETRY,
    log_operation, get_dict_items, ConfigCache,
)
from flask import current_app


def _ai_vision_enabled():
    """判断AI视觉识别是否启用"""
    try:
        return (ConfigCache.get('ai_enabled', 'false') == 'true'
                and ConfigCache.get('ai_vision_enabled', 'false') == 'true')
    except Exception:
        return False


# ============================================================
# 公司级物资主库
# ============================================================

def _gen_master_material_code():
    """生成公司级物资主库编码：WL + 6位流水号（如 WL000001）

    使用进程内锁防止并发冲突，参考 app.utils.gen_dept_code 实现。
    """
    from sqlalchemy import func

    with _code_gen_lock:
        max_code = db.session.query(func.max(Material.code)).filter(
            Material.code.like('WL%'),
            Material.source == 'company'
        ).scalar()
        if max_code:
            try:
                seq = int(max_code[2:]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
        for _ in range(_CODE_GEN_MAX_RETRY):
            candidate = f"WL{seq:06d}"
            exists = db.session.query(Material.id).filter_by(code=candidate).first()
            if not exists:
                return candidate
            seq += 1
        return f"WL{seq:07d}"


def _get_company_project_id():
    """获取公司级主库使用的 project_id（仅用于满足 NOT NULL 约束）

    优先使用当前 session 项目，其次取第一个项目。若均无则返回 None。
    """
    project_id = session.get('current_project_id')
    if project_id:
        return project_id
    first_project = Project.query.first()
    return first_project.id if first_project else None


@master_bp.route('/material')
@login_required
@admin_required
def material_index():
    """公司级物资主库列表页"""
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    category_id = request.args.get('category_id', 0, type=int)
    status = request.args.get('status', '', type=str)

    query = Material.query.filter_by(source='company')
    if keyword:
        query = query.filter(
            or_(Material.name.contains(keyword), Material.code.contains(keyword))
        )
    if category_id:
        query = query.filter_by(category_id=category_id)
    if status:
        query = query.filter_by(status=status)

    pagination = query.order_by(Material.code.asc()).paginate(
        page=page, per_page=10, error_out=False
    )

    # 公司级分类（用于筛选下拉）
    categories = Category.query.filter_by(source='company').order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()

    return render_template(
        'master/material_master.html',
        pagination=pagination,
        keyword=keyword,
        category_id=category_id,
        status=status,
        categories=categories,
    )


@master_bp.route('/material/create', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='master_material', operation='新增')
def material_create():
    """新增公司级物资主库"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('物资名称不能为空。', 'danger')
            return redirect(url_for('master.material_create'))

        category_id = request.form.get('category_id', type=int) or None
        if not category_id:
            flash('请选择物资分类。', 'danger')
            return redirect(url_for('master.material_create'))

        category = Category.query.get(category_id)
        if not category or category.level != 3:
            flash('物资分类必须为三级分类。', 'danger')
            return redirect(url_for('master.material_create'))

        project_id = _get_company_project_id()
        if not project_id:
            flash('系统未找到任何项目，无法创建物资。请先创建项目。', 'danger')
            return redirect(url_for('master.material_create'))

        code = _gen_master_material_code()
        if not code:
            flash('物资编码生成失败，请重试。', 'danger')
            return redirect(url_for('master.material_create'))

        material = Material(
            project_id=project_id,
            name=name,
            code=code,
            specification=request.form.get('specification', '').strip(),
            category_id=category_id,
            unit=request.form.get('unit', '').strip(),
            remark=request.form.get('remark', '').strip(),
            source='company',
            status='active',
            create_dept=current_user.dept_id,
        )
        db.session.add(material)
        db.session.commit()
        flash(f'公司级物资创建成功，编码：{code}', 'success')
        return redirect(url_for('master.material_index'))

    categories = Category.query.filter_by(source='company').order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()
    return render_template('master/material_form.html', material=None, categories=categories)


@master_bp.route('/material/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='master_material', operation='编辑')
def material_edit(id):
    """编辑公司级物资主库"""
    material = Material.query.get_or_404(id)
    # 仅允许编辑公司级主库物资
    if material.source != 'company':
        flash('该物资不在公司主库，无法通过此入口编辑。', 'danger')
        return redirect(url_for('master.material_index'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('物资名称不能为空。', 'danger')
            return redirect(url_for('master.material_edit', id=id))

        category_id = request.form.get('category_id', type=int) or None
        if not category_id:
            flash('请选择物资分类。', 'danger')
            return redirect(url_for('master.material_edit', id=id))

        category = Category.query.get(category_id)
        if not category or category.level != 3:
            flash('物资分类必须为三级分类。', 'danger')
            return redirect(url_for('master.material_edit', id=id))

        # 编码不变，仅更新其他字段
        material.name = name
        material.specification = request.form.get('specification', '').strip()
        material.category_id = category_id
        material.unit = request.form.get('unit', '').strip()
        material.remark = request.form.get('remark', '').strip()
        db.session.commit()
        flash('物资更新成功。', 'success')
        return redirect(url_for('master.material_index'))

    categories = Category.query.filter_by(source='company').order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()
    return render_template('master/material_form.html', material=material, categories=categories)


@master_bp.route('/material/<int:id>/toggle_status', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_material', operation='启停')
def material_toggle_status(id):
    """启用/停用公司级物资"""
    material = Material.query.get_or_404(id)
    if material.source != 'company':
        flash('该物资不在公司主库。', 'danger')
        return redirect(url_for('master.material_index'))

    material.status = 'inactive' if material.status == 'active' else 'active'
    db.session.commit()
    action = '停用' if material.status == 'inactive' else '启用'
    flash(f'物资已{action}。', 'success')
    return redirect(url_for('master.material_index'))


@master_bp.route('/material/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_material', operation='删除')
def material_delete(id):
    """删除公司级物资（删除前检查业务单据引用）"""
    material = Material.query.get_or_404(id)
    if material.source != 'company':
        flash('该物资不在公司主库。', 'danger')
        return redirect(url_for('master.material_index'))

    # 检查业务单据引用
    if ContractItem.query.filter_by(material_id=material.id).first():
        flash('该物资已关联合同明细，无法删除。', 'danger')
        return redirect(url_for('master.material_index'))
    if StockInItem.query.filter_by(material_id=material.id).first():
        flash('该物资已存在入库记录，无法删除。', 'danger')
        return redirect(url_for('master.material_index'))
    if StockOutItem.query.filter_by(material_id=material.id).first():
        flash('该物资已存在出库记录，无法删除。', 'danger')
        return redirect(url_for('master.material_index'))
    if Inventory.query.filter_by(material_id=material.id).first():
        flash('该物资已存在库存记录，无法删除。', 'danger')
        return redirect(url_for('master.material_index'))

    db.session.delete(material)
    db.session.commit()
    log_operation('删除', module='公司物资主库', description=f'删除公司级物资：{material.name}({material.code})')
    flash('物资删除成功。', 'success')
    return redirect(url_for('master.material_index'))


@master_bp.route('/material/api/search')
@login_required
def material_api_search():
    """API：搜索公司主库物资（JSON）

    供"从公司库添加"弹窗使用。返回 JSON：
        {items: [{id, code, name, specification, unit, category_name}], total, page}
    """
    q = request.args.get('q', '', type=str)
    category_id = request.args.get('category_id', 0, type=int)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    # 限制最大返回数量，避免一次性拉取过多
    per_page = min(max(per_page, 1), 100)

    query = Material.query.filter_by(source='company', status='active')
    if q:
        query = query.filter(
            or_(Material.name.contains(q), Material.code.contains(q))
        )
    if category_id:
        query = query.filter_by(category_id=category_id)

    pagination = query.order_by(Material.code.asc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    items = []
    for m in pagination.items:
        items.append({
            'id': m.id,
            'code': m.code or '',
            'name': m.name,
            'specification': m.specification or '',
            'unit': m.unit or '',
            'category_name': m.category.name if m.category else '',
        })

    return jsonify({
        'items': items,
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
    })


@master_bp.route('/material/export')
@login_required
@admin_required
@log_audit(module='master_material', operation='导出')
def material_export():
    """导出公司物资主库Excel"""
    keyword = request.args.get('keyword', '', type=str)
    category_id = request.args.get('category_id', 0, type=int)
    status = request.args.get('status', '', type=str)

    query = Material.query.filter_by(source='company')
    if keyword:
        query = query.filter(
            or_(Material.name.contains(keyword), Material.code.contains(keyword))
        )
    if category_id:
        query = query.filter_by(category_id=category_id)
    if status:
        query = query.filter_by(status=status)

    materials = query.order_by(Material.code.asc()).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '物资主库'

    headers = ['物资编码', '物资名称', '规格型号', '单位', '分类编码', '分类名称', '状态', '备注']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color='E8F0FE', end_color='E8F0FE', fill_type='solid')

    status_map = {'active': '启用', 'inactive': '停用'}

    for m in materials:
        ws.append([
            m.code or '',
            m.name or '',
            m.specification or '',
            m.unit or '',
            m.category.category_code if m.category else '',
            m.category.name if m.category else '',
            status_map.get(m.status, m.status or ''),
            m.remark or '',
        ])

    col_widths = [16, 24, 24, 10, 14, 18, 10, 30]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = f'物资主库_{datetime.now().strftime("%Y%m%d")}.xlsx'
    filepath = os.path.join(upload_dir, filename)
    wb.save(filepath)

    log_operation('导出', module='公司物资主库', description=f'导出物资数据 {len(materials)} 条')
    return send_from_directory(upload_dir, filename, as_attachment=True)


@master_bp.route('/material/template')
@login_required
@admin_required
def material_template():
    """下载公司物资主库导入模板"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '物资导入模板'

    headers = ['物资编码', '物资名称', '规格型号', '物资分类编码', '单位', '备注']
    ws.append(headers)

    red_fill = PatternFill(start_color='FFE6E6', end_color='FFE6E6', fill_type='solid')
    required_cols = [1, 3, 4]
    for col_idx in required_cols:
        ws.cell(row=1, column=col_idx).fill = red_fill
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')

    ws.append(['', '示例钢筋 HRB400', 'HRB400 Φ12', 'MC010601', '吨', '主体结构用'])

    ws2 = wb.create_sheet('填写说明')
    ws2.append(['字段', '是否必填', '说明'])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    ws2.append(['物资编码', '选填', '不填则系统自动生成；填写则使用指定编码，不能与现有编码重复'])
    ws2.append(['物资名称', '必填', '物资名称，同一分类下不可重复'])
    ws2.append(['规格型号', '选填', '如：HRB400 Φ12'])
    ws2.append(['物资分类编码', '必填', '三级分类编码，如 MC010601，需与系统分类一致'])
    ws2.append(['单位', '必填', '计量单位，如：吨、个、米、m³'])
    ws2.append(['备注', '选填', '其他说明信息'])

    ws2.column_dimensions['A'].width = 16
    ws2.column_dimensions['B'].width = 10
    ws2.column_dimensions['C'].width = 60

    col_widths = [16, 24, 24, 16, 10, 30]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = '公司物资主库导入模板.xlsx'
    filepath = os.path.join(upload_dir, filename)
    wb.save(filepath)
    return send_from_directory(upload_dir, filename, as_attachment=True)


@master_bp.route('/material/import', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_material', operation='批量导入')
def material_import():
    """批量导入公司物资主库

    编码规则：
    - 用户填写了编码：使用用户编码，检查重复
    - 用户未填写编码：系统自动生成（分类编码 + 3位流水号）
    """
    file = request.files.get('file')
    if not file:
        flash('请选择要导入的Excel文件。', 'danger')
        return redirect(url_for('master.material_index'))

    project_id = _get_company_project_id()
    if not project_id:
        flash('系统未找到任何项目，无法导入。请先创建项目。', 'danger')
        return redirect(url_for('master.material_index'))

    try:
        wb = openpyxl.load_workbook(file)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))

        success_count = 0
        errors = []

        for idx, row in enumerate(rows, start=2):
            if not row or all(v is None or str(v).strip() == '' for v in row):
                continue

            code = str(row[0]).strip() if len(row) > 0 and row[0] else ''
            name = str(row[1]).strip() if len(row) > 1 and row[1] else ''
            spec = str(row[2]).strip() if len(row) > 2 and row[2] else ''
            cat_code = str(row[3]).strip() if len(row) > 3 and row[3] else ''
            unit = str(row[4]).strip() if len(row) > 4 and row[4] else ''
            remark = str(row[5]).strip() if len(row) > 5 and row[5] else ''

            if not name:
                errors.append(f'第{idx}行：物资名称不能为空')
                continue
            if not unit:
                errors.append(f'第{idx}行：单位不能为空')
                continue
            if not cat_code:
                errors.append(f'第{idx}行：物资分类编码不能为空')
                continue

            category = Category.query.filter_by(
                source='company', category_code=cat_code
            ).first()
            if not category:
                errors.append(f'第{idx}行：物资分类编码"{cat_code}"不存在')
                continue
            if category.level != 3:
                errors.append(f'第{idx}行：物资分类"{cat_code}"不是三级分类')
                continue

            if code:
                existing_code = Material.query.filter_by(code=code).first()
                if existing_code:
                    errors.append(f'第{idx}行：物资编码"{code}"已存在')
                    continue
            else:
                from sqlalchemy import func
                with _code_gen_lock:
                    max_code = db.session.query(func.max(Material.code)).filter(
                        Material.code.like(category.category_code + '%'),
                        Material.source == 'company'
                    ).scalar()
                    seq = 1
                    if max_code and max_code.startswith(category.category_code):
                        try:
                            seq = int(max_code[len(category.category_code):]) + 1
                        except ValueError:
                            seq = 1
                    for _ in range(_CODE_GEN_MAX_RETRY):
                        candidate = f"{category.category_code}{seq:03d}"
                        exists = db.session.query(Material.id).filter_by(code=candidate).first()
                        if not exists:
                            code = candidate
                            break
                        seq += 1
                    if not code:
                        errors.append(f'第{idx}行：物资编码生成失败')
                        continue

            existing_name = Material.query.filter_by(
                source='company', name=name, category_id=category.id
            ).first()
            if existing_name:
                errors.append(f'第{idx}行：物资名称"{name}"在该分类下已存在')
                continue

            material = Material(
                project_id=project_id,
                name=name,
                code=code,
                specification=spec,
                category_id=category.id,
                unit=unit,
                remark=remark,
                source='company',
                status='active',
                create_dept=current_user.dept_id,
            )
            db.session.add(material)
            success_count += 1

        if success_count > 0:
            db.session.commit()

        if errors:
            msg = f'成功导入 {success_count} 条，失败 {len(errors)} 条：<br>' + '<br>'.join(errors[:10])
            if len(errors) > 10:
                msg += f'<br>...还有 {len(errors) - 10} 条错误未显示'
            flash(msg, 'warning')
        else:
            flash(f'成功导入 {success_count} 条物资数据。', 'success')

        log_operation('导入', module='公司物资主库', description=f'批量导入物资 {success_count} 条')

    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{str(e)}', 'danger')

    return redirect(url_for('master.material_index'))


# ============================================================
# 公司级供应商主库
# ============================================================

def _gen_master_supplier_code():
    """生成公司级供应商主库编码：GYS + 6位流水号（如 GYS000001）

    使用进程内锁防止并发冲突。
    """
    from sqlalchemy import func

    with _code_gen_lock:
        max_code = db.session.query(func.max(Supplier.code)).filter(
            Supplier.code.like('GYS%'),
            Supplier.source == 'company'
        ).scalar()
        if max_code:
            try:
                seq = int(max_code[3:]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
        for _ in range(_CODE_GEN_MAX_RETRY):
            candidate = f"GYS{seq:06d}"
            exists = db.session.query(Supplier.id).filter_by(code=candidate).first()
            if not exists:
                return candidate
            seq += 1
        return f"GYS{seq:07d}"


@master_bp.route('/supplier')
@login_required
@admin_required
def supplier_index():
    """公司级供应商主库列表页"""
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    status = request.args.get('status', '', type=str)

    query = Supplier.query.filter_by(source='company')
    if keyword:
        query = query.filter(
            or_(Supplier.name.contains(keyword), Supplier.code.contains(keyword))
        )
    if status:
        query = query.filter_by(status=status)

    pagination = query.order_by(Supplier.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False
    )

    return render_template(
        'master/supplier_master.html',
        pagination=pagination,
        keyword=keyword,
        status=status,
    )


@master_bp.route('/supplier/create', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='master_supplier', operation='新增')
def supplier_create():
    """新增公司级供应商主库"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('供应商名称不能为空。', 'danger')
            return redirect(url_for('master.supplier_create'))

        project_id = _get_company_project_id()
        if not project_id:
            flash('系统未找到任何项目，无法创建供应商。请先创建项目。', 'danger')
            return redirect(url_for('master.supplier_create'))

        code = _gen_master_supplier_code()

        supplier = Supplier(
            project_id=project_id,
            name=name,
            code=code,
            credit_code=request.form.get('credit_code', '').strip(),
            contact_person=request.form.get('contact_person', '').strip(),
            phone=request.form.get('phone', '').strip(),
            legal_person=request.form.get('legal_person', '').strip(),
            address=request.form.get('address', '').strip(),
            bank_name=request.form.get('bank_name', '').strip(),
            bank_account=request.form.get('bank_account', '').strip(),
            source='company',
            status='qualified',
            create_dept=current_user.dept_id,
        )
        db.session.add(supplier)
        db.session.commit()
        flash(f'公司级供应商创建成功，编码：{code}', 'success')
        return redirect(url_for('master.supplier_index'))

    return render_template('master/supplier_form.html',
                           supplier=None,
                           ai_vision_enabled=_ai_vision_enabled())


@master_bp.route('/supplier/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@log_audit(module='master_supplier', operation='编辑')
def supplier_edit(id):
    """编辑公司级供应商主库"""
    supplier = Supplier.query.get_or_404(id)
    if supplier.source != 'company':
        flash('该供应商不在公司主库，无法通过此入口编辑。', 'danger')
        return redirect(url_for('master.supplier_index'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('供应商名称不能为空。', 'danger')
            return redirect(url_for('master.supplier_edit', id=id))

        supplier.name = name
        supplier.credit_code = request.form.get('credit_code', '').strip()
        supplier.contact_person = request.form.get('contact_person', '').strip()
        supplier.phone = request.form.get('phone', '').strip()
        supplier.legal_person = request.form.get('legal_person', '').strip()
        supplier.address = request.form.get('address', '').strip()
        supplier.bank_name = request.form.get('bank_name', '').strip()
        supplier.bank_account = request.form.get('bank_account', '').strip()
        db.session.commit()
        flash('供应商更新成功。', 'success')
        return redirect(url_for('master.supplier_index'))

    return render_template('master/supplier_form.html',
                           supplier=supplier,
                           ai_vision_enabled=_ai_vision_enabled())


@master_bp.route('/supplier/<int:id>/status', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_supplier', operation='状态变更')
def supplier_status(id):
    """修改公司级供应商状态：qualified/unqualified/blacklist"""
    supplier = Supplier.query.get_or_404(id)
    if supplier.source != 'company':
        flash('该供应商不在公司主库。', 'danger')
        return redirect(url_for('master.supplier_index'))

    new_status = request.form.get('status', '').strip()
    if new_status not in ('qualified', 'unqualified', 'blacklist'):
        flash('无效的供应商状态。', 'danger')
        return redirect(url_for('master.supplier_index'))

    supplier.status = new_status
    db.session.commit()
    status_map = {
        'qualified': '合格',
        'unqualified': '不合格',
        'blacklist': '黑名单',
    }
    log_operation(
        '状态变更', module='公司供应商主库',
        description=f'供应商 {supplier.name} 状态变更为 {status_map.get(new_status, new_status)}'
    )
    flash(f'供应商状态已更新为「{status_map.get(new_status, new_status)}」。', 'success')
    return redirect(url_for('master.supplier_index'))


@master_bp.route('/supplier/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_supplier', operation='删除')
def supplier_delete(id):
    """删除公司级供应商（删除前检查业务单据引用）"""
    supplier = Supplier.query.get_or_404(id)
    if supplier.source != 'company':
        flash('该供应商不在公司主库。', 'danger')
        return redirect(url_for('master.supplier_index'))

    # 检查合同引用
    if Contract.query.filter_by(supplier_id=supplier.id).first():
        flash('该供应商已关联合同，无法删除。', 'danger')
        return redirect(url_for('master.supplier_index'))
    # 检查入库单引用
    from app.models import StockIn
    if StockIn.query.filter_by(supplier_id=supplier.id).first():
        flash('该供应商已存在入库记录，无法删除。', 'danger')
        return redirect(url_for('master.supplier_index'))

    db.session.delete(supplier)
    db.session.commit()
    log_operation('删除', module='公司供应商主库', description=f'删除公司级供应商：{supplier.name}')
    flash('供应商删除成功。', 'success')
    return redirect(url_for('master.supplier_index'))


@master_bp.route('/supplier/api/search')
@login_required
def supplier_api_search():
    """API：搜索公司主库供应商（JSON）

    供"从公司库添加"弹窗使用。返回 JSON：
        {items: [{id, code, name, contact_person, phone}], total, page}
    """
    q = request.args.get('q', '', type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 1), 100)

    query = Supplier.query.filter_by(source='company', status='qualified')
    if q:
        query = query.filter(
            or_(Supplier.name.contains(q), Supplier.code.contains(q))
        )

    pagination = query.order_by(Supplier.name.asc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    items = []
    for s in pagination.items:
        items.append({
            'id': s.id,
            'code': s.code or '',
            'name': s.name,
            'contact_person': s.contact_person or '',
            'phone': s.phone or '',
        })

    return jsonify({
        'items': items,
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
    })


@master_bp.route('/supplier/export')
@login_required
@admin_required
@log_audit(module='master_supplier', operation='导出')
def supplier_export():
    """导出公司供应商主库Excel"""
    keyword = request.args.get('keyword', '', type=str)
    status = request.args.get('status', '', type=str)

    query = Supplier.query.filter_by(source='company')
    if keyword:
        query = query.filter(
            or_(Supplier.name.contains(keyword), Supplier.code.contains(keyword))
        )
    if status:
        query = query.filter_by(status=status)

    suppliers = query.order_by(Supplier.code.asc()).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '供应商主库'

    headers = ['供应商编码', '供应商名称', '社会信用代码', '联系人', '联系电话', '地址', '状态', '备注']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color='E8F0FE', end_color='E8F0FE', fill_type='solid')

    status_map = {
        'qualified': '合格',
        'unqualified': '不合格',
        'blacklist': '黑名单',
    }

    for s in suppliers:
        ws.append([
            s.code or '',
            s.name or '',
            s.credit_code or '',
            s.contact_person or '',
            s.phone or '',
            s.address or '',
            status_map.get(s.status, s.status or ''),
            '',
        ])

    col_widths = [16, 28, 22, 12, 16, 32, 10, 30]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = f'供应商主库_{datetime.now().strftime("%Y%m%d")}.xlsx'
    filepath = os.path.join(upload_dir, filename)
    wb.save(filepath)

    log_operation('导出', module='公司供应商主库', description=f'导出供应商数据 {len(suppliers)} 条')
    return send_from_directory(upload_dir, filename, as_attachment=True)


@master_bp.route('/supplier/template')
@login_required
@admin_required
def supplier_template():
    """下载公司供应商主库导入模板"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '供应商导入模板'

    headers = ['供应商编码', '供应商名称', '社会信用代码', '联系人', '联系电话', '地址', '备注']
    ws.append(headers)

    red_fill = PatternFill(start_color='FFE6E6', end_color='FFE6E6', fill_type='solid')
    required_cols = [1]
    for col_idx in required_cols:
        ws.cell(row=1, column=col_idx).fill = red_fill
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')

    ws.append(['', '示例建材有限公司', '91110000MA01ABCD12', '张三', '13800138000', '北京市朝阳区示例路1号', '长期合作'])

    ws2 = wb.create_sheet('填写说明')
    ws2.append(['字段', '是否必填', '说明'])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    ws2.append(['供应商编码', '选填', '不填则系统自动生成；填写则使用指定编码，不能与现有编码重复'])
    ws2.append(['供应商名称', '必填', '供应商全称，不可重复'])
    ws2.append(['社会信用代码', '选填', '统一社会信用代码'])
    ws2.append(['联系人', '选填', '主要联系人姓名'])
    ws2.append(['联系电话', '选填', '手机号或座机号'])
    ws2.append(['地址', '选填', '供应商地址'])
    ws2.append(['备注', '选填', '其他说明信息'])

    ws2.column_dimensions['A'].width = 16
    ws2.column_dimensions['B'].width = 10
    ws2.column_dimensions['C'].width = 60

    col_widths = [16, 28, 22, 12, 16, 32, 30]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = '公司供应商主库导入模板.xlsx'
    filepath = os.path.join(upload_dir, filename)
    wb.save(filepath)
    return send_from_directory(upload_dir, filename, as_attachment=True)


@master_bp.route('/supplier/import', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_supplier', operation='批量导入')
def supplier_import():
    """批量导入公司供应商主库

    编码规则：
    - 用户填写了编码：使用用户编码，检查重复
    - 用户未填写编码：系统自动生成（SUP + 4位流水号）
    """
    file = request.files.get('file')
    if not file:
        flash('请选择要导入的Excel文件。', 'danger')
        return redirect(url_for('master.supplier_index'))

    project_id = _get_company_project_id()
    if not project_id:
        flash('系统未找到任何项目，无法导入。请先创建项目。', 'danger')
        return redirect(url_for('master.supplier_index'))

    try:
        wb = openpyxl.load_workbook(file)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))

        success_count = 0
        errors = []

        for idx, row in enumerate(rows, start=2):
            if not row or all(v is None or str(v).strip() == '' for v in row):
                continue

            code = str(row[0]).strip() if len(row) > 0 and row[0] else ''
            name = str(row[1]).strip() if len(row) > 1 and row[1] else ''
            credit_code = str(row[2]).strip() if len(row) > 2 and row[2] else ''
            contact_person = str(row[3]).strip() if len(row) > 3 and row[3] else ''
            phone = str(row[4]).strip() if len(row) > 4 and row[4] else ''
            address = str(row[5]).strip() if len(row) > 5 and row[5] else ''
            remark = str(row[6]).strip() if len(row) > 6 and row[6] else ''

            if not name:
                errors.append(f'第{idx}行：供应商名称不能为空')
                continue

            if code:
                existing_code = Supplier.query.filter_by(code=code).first()
                if existing_code:
                    errors.append(f'第{idx}行：供应商编码"{code}"已存在')
                    continue
            else:
                code = _gen_master_supplier_code()
                if not code:
                    errors.append(f'第{idx}行：供应商编码生成失败')
                    continue

            existing_name = Supplier.query.filter_by(
                source='company', name=name
            ).first()
            if existing_name:
                errors.append(f'第{idx}行：供应商名称"{name}"已存在')
                continue

            supplier = Supplier(
                project_id=project_id,
                name=name,
                code=code,
                credit_code=credit_code,
                contact_person=contact_person,
                phone=phone,
                address=address,
                source='company',
                status='qualified',
                create_dept=current_user.dept_id,
            )
            if remark:
                pass
            db.session.add(supplier)
            success_count += 1

        if success_count > 0:
            db.session.commit()

        if errors:
            msg = f'成功导入 {success_count} 条，失败 {len(errors)} 条：<br>' + '<br>'.join(errors[:10])
            if len(errors) > 10:
                msg += f'<br>...还有 {len(errors) - 10} 条错误未显示'
            flash(msg, 'warning')
        else:
            flash(f'成功导入 {success_count} 条供应商数据。', 'success')

        log_operation('导入', module='公司供应商主库', description=f'批量导入供应商 {success_count} 条')

    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{str(e)}', 'danger')

    return redirect(url_for('master.supplier_index'))


# ============================================================
# 公司级物资分类管理
# ============================================================

def _gen_master_category_code(parent_id):
    """生成公司级分类编码（公司统一分类）

    一级分类：MC + 2位序号（MC01）
    二级分类：父级编码 + 2位序号（MC0106）
    三级分类：父级编码 + 2位序号（MC010601）
    """
    if parent_id == 0:
        prefix = 'MC'
        existing = Category.query.filter(
            Category.source == 'company',
            Category.parent_id == 0
        ).all()
        seq = len(existing) + 1
        return f'{prefix}{seq:02d}'
    else:
        parent = Category.query.get(parent_id)
        if not parent:
            return None
        existing = Category.query.filter(
            Category.source == 'company',
            Category.parent_id == parent_id
        ).all()
        seq = len(existing) + 1
        return f'{parent.category_code}{seq:02d}'


def _build_master_category_tree(categories):
    """将扁平分类列表构建为树形结构，并统计物资数量"""
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


@master_bp.route('/category')
@login_required
@admin_required
def category_index():
    """公司级物资分类管理页（树形展示三级分类）"""
    categories = Category.query.filter_by(source='company').order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()
    tree = _build_master_category_tree(categories)
    return render_template('master/category_master.html', tree=tree, categories=categories)


@master_bp.route('/category/create', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_category', operation='新增')
def category_create():
    """新增公司级物资分类"""
    name = request.form.get('name', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int) or 0
    parent_id = request.form.get('parent_id', 0, type=int) or 0
    negative_stock_policy = request.form.get('negative_stock_policy', 'global')

    if not name:
        flash('分类名称不能为空。', 'danger')
        return redirect(url_for('master.category_index'))

    # 根据父级计算层级
    if parent_id == 0:
        level = 1
    else:
        parent = Category.query.get(parent_id)
        if not parent:
            flash('父级分类不存在。', 'danger')
            return redirect(url_for('master.category_index'))
        if parent.level >= 3:
            flash('最多支持三级分类，无法继续新增子分类。', 'danger')
            return redirect(url_for('master.category_index'))
        level = parent.level + 1

    # 公司级分类使用任一项目 ID 兜底 NOT NULL 约束
    project_id = _get_company_project_id()
    if not project_id:
        flash('系统未找到任何项目，无法创建分类。', 'danger')
        return redirect(url_for('master.category_index'))

    category_code = _gen_master_category_code(parent_id)
    if not category_code:
        flash('分类编码生成失败。', 'danger')
        return redirect(url_for('master.category_index'))

    category = Category(
        project_id=project_id,
        parent_id=parent_id,
        level=level,
        category_code=category_code,
        name=name,
        sort_order=sort_order,
        negative_stock_policy=negative_stock_policy,
        source='company',
    )
    db.session.add(category)
    db.session.commit()
    flash(f'分类创建成功，编码：{category_code}', 'success')
    return redirect(url_for('master.category_index'))


@master_bp.route('/category/batch_create', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_category', operation='批量新增')
def category_batch_create():
    """批量新增公司级物资分类"""
    names = request.form.get('names', '').strip()
    parent_id = request.form.get('parent_id', 0, type=int) or 0
    start_sort = request.form.get('start_sort', 1, type=int) or 1

    if not names:
        flash('请输入分类名称。', 'danger')
        return redirect(url_for('master.category_index'))

    if parent_id != 0:
        parent = Category.query.get(parent_id)
        if not parent:
            flash('父级分类不存在。', 'danger')
            return redirect(url_for('master.category_index'))
        if parent.level >= 3:
            flash('最多支持三级分类，无法继续新增子分类。', 'danger')
            return redirect(url_for('master.category_index'))
        if parent.source != 'company':
            flash('父级分类不在公司主库。', 'danger')
            return redirect(url_for('master.category_index'))

    lines = [n.strip() for n in names.split('\n') if n.strip()]
    if not lines:
        flash('请输入有效的分类名称。', 'danger')
        return redirect(url_for('master.category_index'))

    # 公司级分类使用任一项目 ID 兜底 NOT NULL 约束
    project_id = _get_company_project_id()
    if not project_id:
        flash('系统未找到任何项目，无法创建分类。', 'danger')
        return redirect(url_for('master.category_index'))

    count = 0
    sort_order = start_sort
    for name in lines:
        level = 1
        if parent_id != 0:
            level = parent.level + 1

        category_code = _gen_master_category_code(parent_id)
        if not category_code:
            continue

        category = Category(
            project_id=project_id,
            parent_id=parent_id,
            level=level,
            category_code=category_code,
            name=name,
            sort_order=sort_order,
            source='company',
        )
        db.session.add(category)
        count += 1
        sort_order += 1

    db.session.commit()
    flash(f'成功创建 {count} 个分类。', 'success')
    return redirect(url_for('master.category_index'))


@master_bp.route('/category/<int:id>/edit', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_category', operation='编辑')
def category_edit(id):
    """编辑公司级物资分类"""
    category = Category.query.get_or_404(id)
    if category.source != 'company':
        flash('该分类不在公司主库，无法通过此入口编辑。', 'danger')
        return redirect(url_for('master.category_index'))

    name = request.form.get('name', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int) or 0
    negative_stock_policy = request.form.get('negative_stock_policy', 'global')
    if not name:
        flash('分类名称不能为空。', 'danger')
        return redirect(url_for('master.category_index'))

    category.name = name
    category.sort_order = sort_order
    category.negative_stock_policy = negative_stock_policy
    db.session.commit()
    flash('分类更新成功。', 'success')
    return redirect(url_for('master.category_index'))


@master_bp.route('/category/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
@log_audit(module='master_category', operation='删除')
def category_delete(id):
    """删除公司级物资分类"""
    category = Category.query.get_or_404(id)
    if category.source != 'company':
        flash('该分类不在公司主库。', 'danger')
        return redirect(url_for('master.category_index'))

    if Category.query.filter_by(parent_id=category.id).first():
        flash('该分类下有子分类，无法删除。请先删除子分类。', 'danger')
        return redirect(url_for('master.category_index'))
    if Material.query.filter_by(category_id=category.id).first():
        flash('该分类下已有物资关联，无法删除。', 'danger')
        return redirect(url_for('master.category_index'))

    db.session.delete(category)
    db.session.commit()
    flash('分类删除成功。', 'success')
    return redirect(url_for('master.category_index'))


@master_bp.route('/category/api/tree')
@login_required
def category_api_tree():
    """返回公司级分类树 JSON（供物资表单三级联动使用）"""
    categories = Category.query.filter_by(source='company').order_by(
        Category.sort_order.asc(), Category.created_at.asc()
    ).all()
    tree = _build_master_category_tree(categories)

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
                'children': serialize(node['children']),
            })
        return result

    return jsonify(serialize(tree))


@master_bp.route('/category/api/children/<int:parent_id>')
@login_required
def category_api_children(parent_id):
    """返回指定父级的公司级子分类列表 JSON"""
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
        'sort_order': c.sort_order,
    } for c in categories])
