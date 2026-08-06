import os
import uuid
import json
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, current_app, jsonify, session
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.supplier import bp

# P2: 文件上传扩展名校验
def _validate_upload_file(file):
    """校验上传文件扩展名，不通过则abort 400"""
    if file and file.filename:
        from app.utils import validate_file_extension
        from flask import abort
        ok, err = validate_file_extension(file.filename)
        if not ok:
            abort(400, err)
    return file

from app import db
from app.models import Supplier, SupplierEvaluation, StockIn, Contract, Inventory, ProjectSupplier
from app.decorators import editor_required, log_audit
from app.utils import (
    log_operation, export_to_excel, get_config,
    get_project_suppliers,
    add_supplier_to_project,
    remove_supplier_from_project,
)


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@bp.route('/')
@login_required
def index():
    project_id = session.get('current_project_id')
    # 必须选择项目（常用表为项目级视图）
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    level = request.args.get('level', '', type=str)

    # 通过 ProjectSupplier 关联表获取项目常用供应商（包含公司主库 + 项目级临时供应商）
    query = get_project_suppliers(project_id, common_only=True)
    if keyword:
        query = query.filter(Supplier.name.contains(keyword) | Supplier.code.contains(keyword))

    if level:
        subquery = db.session.query(
            SupplierEvaluation.supplier_id,
            SupplierEvaluation.level
        ).order_by(SupplierEvaluation.evaluate_date.desc()).group_by(SupplierEvaluation.supplier_id).subquery()
        query = query.join(subquery, Supplier.id == subquery.c.supplier_id).filter(subquery.c.level == level)

    pagination = query.order_by(Supplier.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False
    )

    # 标记每条供应商是否已加入项目常用
    linked_ids = set(
        ps.supplier_id for ps in ProjectSupplier.query.filter_by(project_id=project_id).all()
    )
    # P2: 批量查询最新评价，避免 N+1 查询
    from sqlalchemy import func as _func
    _supplier_ids = [s.id for s in pagination.items]
    _latest_evals = {}
    if _supplier_ids:
        _sub = db.session.query(
            SupplierEvaluation.supplier_id,
            _func.max(SupplierEvaluation.id).label('max_id')
        ).filter(
            SupplierEvaluation.supplier_id.in_(_supplier_ids)
        ).group_by(SupplierEvaluation.supplier_id).subquery()
        _evals = db.session.query(SupplierEvaluation).join(
            _sub, SupplierEvaluation.id == _sub.c.max_id
        ).all()
        _latest_evals = {e.supplier_id: e for e in _evals}
    for s in pagination.items:
        s.latest_evaluation = _latest_evals.get(s.id)
        s._is_project_common = s.id in linked_ids

    return render_template('supplier/index.html', pagination=pagination, keyword=keyword, level=level,
                           linked_supplier_ids=sorted(linked_ids))


@bp.route('/export')
@login_required
def export():
    """导出供应商列表Excel（导出本项目常用供应商）"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('supplier.index'))

    # 与列表页保持一致：导出本项目常用供应商（包含公司主库 + 项目级）
    suppliers = get_project_suppliers(project_id, common_only=True).order_by(Supplier.created_at.desc()).all()
    
    headers = ['序号', '供应商编码', '供应商名称', '统一社会信用代码', '联系人', '联系电话', 
               '法定代表人', '地址', '开户银行', '银行账号']
    rows = []
    for idx, s in enumerate(suppliers, 1):
        rows.append([
            idx, s.code or '', s.name, s.credit_code or '', s.contact_person or '',
            s.phone or '', s.legal_person or '', s.address or '', s.bank_name or '', s.bank_account or ''
        ])
    
    from datetime import datetime
    filename = f'供应商列表_{datetime.now().strftime("%Y%m%d")}.xlsx'
    data = export_to_excel(headers, rows, '供应商列表', filename)
    
    from flask import Response
    return Response(data, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})


@bp.route('/create', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='新增')
def create():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        file = request.files.get('license_image')
        _validate_upload_file(file)
        license_path = None
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'licenses')
            os.makedirs(upload_dir, exist_ok=True)
            license_path = os.path.join('uploads', 'licenses', filename)
            file.save(os.path.join(upload_dir, filename))

        supplier = Supplier(
            project_id=project_id,
            name=request.form.get('name', '').strip(),
            code=request.form.get('code', '').strip(),
            credit_code=request.form.get('credit_code', '').strip(),
            contact_person=request.form.get('contact_person', '').strip(),
            phone=request.form.get('phone', '').strip(),
            legal_person=request.form.get('legal_person', '').strip(),
            address=request.form.get('address', '').strip(),
            bank_name=request.form.get('bank_name', '').strip(),
            bank_account=request.form.get('bank_account', '').strip(),
            license_image=license_path,
            # 主数据统一改造：项目级新增供应商标记为 project，自动加入项目常用
            source='project',
            status='qualified',
        )
        db.session.add(supplier)
        db.session.commit()
        # 项目级供应商创建后自动加入项目常用表
        add_supplier_to_project(supplier.id, project_id)
        flash('供应商创建成功。', 'success')
        return redirect(url_for('supplier.index'))
    return render_template('supplier/form.html')


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='编辑')
def edit(id):
    supplier = Supplier.query.get_or_404(id)
    if request.method == 'POST':
        file = request.files.get('license_image')
        _validate_upload_file(file)
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'licenses')
            os.makedirs(upload_dir, exist_ok=True)
            supplier.license_image = os.path.join('uploads', 'licenses', filename)
            file.save(os.path.join(upload_dir, filename))

        supplier.name = request.form.get('name', '').strip()
        supplier.code = request.form.get('code', '').strip()
        supplier.credit_code = request.form.get('credit_code', '').strip()
        supplier.contact_person = request.form.get('contact_person', '').strip()
        supplier.phone = request.form.get('phone', '').strip()
        supplier.legal_person = request.form.get('legal_person', '').strip()
        supplier.address = request.form.get('address', '').strip()
        supplier.bank_name = request.form.get('bank_name', '').strip()
        supplier.bank_account = request.form.get('bank_account', '').strip()
        db.session.commit()
        flash('供应商更新成功。', 'success')
        return redirect(url_for('supplier.index'))
    return render_template('supplier/form.html', supplier=supplier)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='删除')
def delete(id):
    supplier = Supplier.query.get_or_404(id)
    from app.models import Contract, StockIn, PurchaseOrder
    if Contract.query.filter_by(supplier_id=supplier.id).first():
        flash('该供应商已关联合同，无法删除。', 'danger')
        return redirect(url_for('supplier.index'))
    if StockIn.query.filter_by(supplier_id=supplier.id).first():
        flash('该供应商已存在入库记录，无法删除。', 'danger')
        return redirect(url_for('supplier.index'))
    if PurchaseOrder.query.filter_by(supplier_id=supplier.id).first():
        flash('该供应商已关联采购订单，无法删除。', 'danger')
        return redirect(url_for('supplier.index'))
    db.session.delete(supplier)
    db.session.commit()
    log_operation('删除', module='供应商管理', description=f'删除供应商：{supplier.name}')
    flash('供应商删除成功。', 'success')
    return redirect(url_for('supplier.index'))


@bp.route('/<int:supplier_id>/remove_from_project', methods=['POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='移除常用')
def remove_from_project(supplier_id):
    """从项目常用供应商移除（仅删除 ProjectSupplier 关联，不影响主库数据）"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('supplier.index'))

    supplier = Supplier.query.get(supplier_id)
    if not supplier:
        flash('供应商不存在。', 'danger')
        return redirect(url_for('supplier.index'))

    # 项目级供应商（source='project'）不允许仅移除常用，否则会变成游离数据
    if supplier.source == 'project':
        flash('项目级临时供应商无法仅移除常用，请直接删除。', 'warning')
        return redirect(url_for('supplier.index'))

    remove_supplier_from_project(supplier_id, project_id)
    flash(f'已将供应商「{supplier.name}」从本项目常用移除。', 'success')
    return redirect(url_for('supplier.index'))


@bp.route('/api/add_to_project', methods=['POST'])
@login_required
@editor_required
def api_add_to_project():
    """批量将公司库供应商加入项目常用（AJAX 接口）

    请求 JSON: {"supplier_ids": [1, 2, 3]}
    返回 JSON: {"success": true, "added_count": N}
    """
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': '请先选择项目'}), 400

    payload = request.get_json(silent=True) or {}
    supplier_ids = payload.get('supplier_ids', []) or []
    if not isinstance(supplier_ids, list) or not supplier_ids:
        return jsonify({'success': False, 'message': 'supplier_ids 不能为空'}), 400

    added = 0
    for sid in supplier_ids:
        try:
            sid_int = int(sid)
        except (TypeError, ValueError):
            continue
        # 仅允许添加公司级供应商
        s = Supplier.query.get(sid_int)
        if not s or s.source != 'company':
            continue
        add_supplier_to_project(sid_int, project_id)
        added += 1

    return jsonify({'success': True, 'added_count': added})


@bp.route('/api/project_suppliers')
@login_required
def api_project_suppliers():
    """API：返回当前项目常用供应商列表（JSON）

    供业务单据（入库/合同等）的统一供应商选择弹窗使用。
    支持关键字搜索、分页。

    返回 JSON:
        {items: [{id, code, name, credit_code, contact_person, phone}], total, page, pages}
    """
    from sqlalchemy import or_

    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'items': [], 'total': 0, 'page': 1, 'pages': 0})

    q = request.args.get('q', '', type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    # 限制最大返回数量，避免一次性拉取过多
    per_page = min(max(per_page, 1), 100)

    query = get_project_suppliers(project_id, common_only=True)
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
            'credit_code': s.credit_code or '',
            'contact_person': s.contact_person or '',
            'phone': s.phone or '',
        })

    return jsonify({
        'items': items,
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
    })


@bp.route('/api/ocr_license', methods=['POST'])
@login_required
@editor_required
def ocr_license():
    """AI识别营业执照"""
    file = request.files.get('image')
    _validate_upload_file(file)
    if not file or not allowed_file(file.filename):
        return jsonify({'success': False, 'message': '请上传有效的图片文件'})

    # 客户端文件大小校验(限制4MB)
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 4 * 1024 * 1024:
        return jsonify({'success': False, 'message': '图片大小不能超过4MB,请压缩后重试'})

    # 保存图片
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f'{uuid.uuid4().hex}.{ext}'
    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'licenses')
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, filename)
    file.save(file_path)

    # 调用OCR识别(带异常捕获)
    from app.ocr_service import extract_business_license, is_ocr_available
    from flask import current_app

    if not is_ocr_available():
        return jsonify({
            'success': False,
            'message': 'AI识别功能未启用,请安装 paddleocr 库或手动录入信息',
            'image_path': os.path.join('uploads', 'licenses', filename)
        })

    try:
        result = extract_business_license(file_path)
    except Exception as e:
        current_app.logger.exception('OCR 营业执照识别异常')
        return jsonify({
            'success': False,
            'message': '识别服务异常,请稍后重试或手动录入信息'
        })

    if result['success']:
        return jsonify({
            'success': True,
            'message': result['message'],
            'data': result['data'],
            'raw_texts': result.get('raw_texts', []),
            'image_path': os.path.join('uploads', 'licenses', filename)
        })
    else:
        # 识别失败不回传 image_path,避免泄露服务器路径
        return jsonify({
            'success': False,
            'message': result['message']
        })


@bp.route('/batch_delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='批量删除')
def batch_delete():
    """批量删除供应商"""
    ids = request.form.getlist('ids', type=int)
    if not ids:
        flash('请选择要删除的供应商', 'warning')
        return redirect(url_for('supplier.index'))
    
    from app.models import Contract
    deleted = 0
    failed = 0
    for sid in ids:
        supplier = Supplier.query.get(sid)
        if supplier:
            if Contract.query.filter_by(supplier_id=supplier.id).first():
                failed += 1
            else:
                db.session.delete(supplier)
                deleted += 1
    
    db.session.commit()
    
    if deleted > 0:
        log_operation('删除', module='供应商管理', description=f'批量删除 {deleted} 个供应商')
    
    if failed > 0:
        flash(f'成功删除 {deleted} 个供应商，{failed} 个因关联合同无法删除', 'info')
    else:
        flash(f'成功删除 {deleted} 个供应商', 'success')
    
    return redirect(url_for('supplier.index'))


@bp.route('/download_template')
@login_required
def download_template():
    """下载供应商导入模板"""
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '供应商导入模板'
    headers = ['供应商名称*', '编码', '统一社会信用代码', '法定代表人', '联系人', '电话', '地址', '开户银行', '银行账号', '备注']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.append(['示例供应商有限公司', 'GYS-001', '91110000MA12345678', '张三', '李四', '13800138000', '北京市朝阳区xx路xx号', '中国银行北京分行', '1234567890', '优质供应商'])
    
    template_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(template_dir, exist_ok=True)
    template_path = os.path.join(template_dir, '供应商导入模板.xlsx')
    wb.save(template_path)
    from flask import send_from_directory
    return send_from_directory(template_dir, '供应商导入模板.xlsx', as_attachment=True)


@bp.route('/import', methods=['POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='批量导入')
def import_excel():
    """批量导入供应商"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目', 'warning')
        return redirect(url_for('main.index'))
    
    file = request.files.get('file')
    _validate_upload_file(file)
    if not file:
        flash('请选择文件', 'danger')
        return redirect(url_for('supplier.index'))
    
    import openpyxl
    
    try:
        wb = openpyxl.load_workbook(file)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        
        success_count = 0
        errors = []
        
        for idx, row in enumerate(rows, start=2):
            if not row or not row[0]:
                continue
            
            name = str(row[0]).strip() if row[0] else ''
            if not name:
                errors.append(f'第{idx}行：供应商名称不能为空')
                continue
            
            # 检查是否已存在
            existing = Supplier.query.filter_by(project_id=project_id, name=name).first()
            if existing:
                errors.append(f'第{idx}行：供应商"{name}"已存在')
                continue
            
            code = str(row[1]).strip() if row[1] else ''
            credit_code = str(row[2]).strip() if row[2] else ''
            legal_person = str(row[3]).strip() if row[3] else ''
            contact_person = str(row[4]).strip() if row[4] else ''
            phone = str(row[5]).strip() if row[5] else ''
            address = str(row[6]).strip() if row[6] else ''
            bank_name = str(row[7]).strip() if row[7] else ''
            bank_account = str(row[8]).strip() if row[8] else ''
            remark = str(row[9]).strip() if len(row) > 9 and row[9] else ''
            
            supplier = Supplier(
                project_id=project_id,
                name=name,
                code=code,
                credit_code=credit_code,
                legal_person=legal_person,
                contact_person=contact_person,
                phone=phone,
                address=address,
                bank_name=bank_name,
                bank_account=bank_account,
                remark=remark,
                # 主数据统一改造：项目级导入供应商标记为 project，自动加入项目常用
                source='project',
                status='qualified',
            )
            db.session.add(supplier)
            db.session.flush()
            # 加入项目常用表（与新增逻辑保持一致）
            existing_link = ProjectSupplier.query.filter_by(
                project_id=project_id, supplier_id=supplier.id
            ).first()
            if not existing_link:
                db.session.add(ProjectSupplier(
                    project_id=project_id,
                    supplier_id=supplier.id,
                    is_common=True,
                    sort=0,
                ))
            success_count += 1
        
        if success_count > 0:
            db.session.commit()
        
        if errors:
            flash(f'成功导入 {success_count} 条，{len(errors)} 条失败：\n' + '\n'.join(errors[:5]), 'warning')
        else:
            flash(f'成功导入 {success_count} 条供应商数据', 'success')
        
        log_operation('导入', module='供应商管理', description=f'批量导入供应商 {success_count} 条')
        
    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{str(e)}', 'danger')
    
    return redirect(url_for('supplier.index'))


@bp.route('/<int:id>/detail')
@login_required
def detail(id):
    supplier = Supplier.query.get_or_404(id)
    
    evaluations = SupplierEvaluation.query.filter_by(supplier_id=id).order_by(SupplierEvaluation.evaluate_date.desc()).all()
    
    from sqlalchemy import func
    stats = {
        'total_amount': 0,
        'delivery_count': 0,
        'return_count': 0,
        'on_time_rate': 0,
        'avg_price': 0,
    }
    
    stock_ins = StockIn.query.filter_by(supplier_id=id).all()
    if stock_ins:
        total_amount = sum(float(si.total_amount or 0) for si in stock_ins)
        delivery_count = len(stock_ins)
        return_count = sum(1 for si in stock_ins if si.stock_in_type == '退货入库')
        
        # 无计划到货日期字段，默认按全部准时计算（或后续补充该字段）
        on_time_rate = 100.0
        
        total_qty = sum(float(item.quantity or 0) for si in stock_ins for item in si.items)
        avg_price = round(total_amount / total_qty, 2) if total_qty > 0 else 0
        
        stats = {
            'total_amount': round(total_amount, 2),
            'delivery_count': delivery_count,
            'return_count': return_count,
            'on_time_rate': on_time_rate,
            'avg_price': avg_price,
        }
    
    from datetime import date
    today = date.today()
    warning_date = today + timedelta(days=30)
    return render_template('supplier/detail.html', supplier=supplier, evaluations=evaluations, stats=stats, today=today, warning_date=warning_date)


@bp.route('/<int:id>/evaluation/add', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='新增评价')
def add_evaluation(id):
    supplier = Supplier.query.get_or_404(id)
    if request.method == 'POST':
        weights_raw = get_config('evaluation_weights', '{"quality": 40, "price": 20, "delivery": 25, "service": 15}')
        weights = json.loads(weights_raw) if isinstance(weights_raw, str) else weights_raw

        quality_score = int(request.form.get('quality_score', 0))
        price_score = int(request.form.get('price_score', 0))
        delivery_score = int(request.form.get('delivery_score', 0))
        service_score = int(request.form.get('service_score', 0))

        total_score = round(
            quality_score * weights['quality'] / 100 +
            price_score * weights['price'] / 100 +
            delivery_score * weights['delivery'] / 100 +
            service_score * weights['service'] / 100,
            2
        )

        level = 'D'
        thresholds_raw = get_config('evaluation_thresholds', '{"A": 90, "B": 80, "C": 60}')
        thresholds = json.loads(thresholds_raw) if isinstance(thresholds_raw, str) else thresholds_raw
        if total_score >= thresholds['A']:
            level = 'A'
        elif total_score >= thresholds['B']:
            level = 'B'
        elif total_score >= thresholds['C']:
            level = 'C'
        
        eval_obj = SupplierEvaluation(
            supplier_id=id,
            evaluate_period=request.form.get('evaluate_period', ''),
            evaluate_date=datetime.strptime(request.form.get('evaluate_date', datetime.now().strftime('%Y-%m-%d')), '%Y-%m-%d').date(),
            evaluator=request.form.get('evaluator', '') or (current_user.name or current_user.username),
            quality_score=quality_score,
            price_score=price_score,
            delivery_score=delivery_score,
            service_score=service_score,
            total_score=total_score,
            level=level,
            comment=request.form.get('comment', '')
        )
        db.session.add(eval_obj)
        db.session.commit()
        flash('评价添加成功', 'success')
        return redirect(url_for('supplier.detail', id=id))
    
    periods = []
    now = datetime.now()
    for year in range(now.year - 2, now.year + 1):
        for quarter in ['Q1', 'Q2', 'Q3', 'Q4']:
            periods.append(f'{year}年{quarter}')
        periods.append(f'{year}年度')
    
    weights_raw = get_config('evaluation_weights', '{"quality": 40, "price": 20, "delivery": 25, "service": 15}')
    weights = json.loads(weights_raw) if isinstance(weights_raw, str) else weights_raw
    return render_template('supplier/evaluation_form.html', supplier=supplier, periods=periods, weights=weights, today_str=now.strftime('%Y-%m-%d'))


@bp.route('/evaluation/<int:eid>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='编辑评价')
def edit_evaluation(eid):
    eval_obj = SupplierEvaluation.query.get_or_404(eid)
    if request.method == 'POST':
        weights_raw = get_config('evaluation_weights', '{"quality": 40, "price": 20, "delivery": 25, "service": 15}')
        weights = json.loads(weights_raw) if isinstance(weights_raw, str) else weights_raw

        eval_obj.quality_score = int(request.form.get('quality_score', 0))
        eval_obj.price_score = int(request.form.get('price_score', 0))
        eval_obj.delivery_score = int(request.form.get('delivery_score', 0))
        eval_obj.service_score = int(request.form.get('service_score', 0))

        eval_obj.total_score = round(
            eval_obj.quality_score * weights['quality'] / 100 +
            eval_obj.price_score * weights['price'] / 100 +
            eval_obj.delivery_score * weights['delivery'] / 100 +
            eval_obj.service_score * weights['service'] / 100,
            2
        )

        thresholds_raw = get_config('evaluation_thresholds', '{"A": 90, "B": 80, "C": 60}')
        thresholds = json.loads(thresholds_raw) if isinstance(thresholds_raw, str) else thresholds_raw
        if eval_obj.total_score >= thresholds['A']:
            eval_obj.level = 'A'
        elif eval_obj.total_score >= thresholds['B']:
            eval_obj.level = 'B'
        elif eval_obj.total_score >= thresholds['C']:
            eval_obj.level = 'C'
        else:
            eval_obj.level = 'D'
        
        eval_obj.evaluate_period = request.form.get('evaluate_period', '')
        eval_obj.comment = request.form.get('comment', '')
        db.session.commit()
        flash('评价更新成功', 'success')
        return redirect(url_for('supplier.detail', id=eval_obj.supplier_id))
    
    periods = []
    now = datetime.now()
    for year in range(now.year - 2, now.year + 1):
        for quarter in ['Q1', 'Q2', 'Q3', 'Q4']:
            periods.append(f'{year}年{quarter}')
        periods.append(f'{year}年度')
    
    weights_raw = get_config('evaluation_weights', '{"quality": 40, "price": 20, "delivery": 25, "service": 15}')
    weights = json.loads(weights_raw) if isinstance(weights_raw, str) else weights_raw
    return render_template('supplier/evaluation_form.html', evaluation=eval_obj, periods=periods, weights=weights, today_str=now.strftime('%Y-%m-%d'))


@bp.route('/evaluation/<int:eid>/delete', methods=['POST'])
@login_required
@editor_required
@log_audit(module='supplier', operation='删除评价')
def delete_evaluation(eid):
    eval_obj = SupplierEvaluation.query.get_or_404(eid)
    supplier_id = eval_obj.supplier_id
    db.session.delete(eval_obj)
    db.session.commit()
    flash('评价删除成功', 'success')
    return redirect(url_for('supplier.detail', id=supplier_id))


@bp.route('/api/get_expiring_suppliers')
@login_required
def api_expiring_suppliers():
    from datetime import datetime, timedelta
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify([])
    
    today = datetime.now().date()
    warning_date = today + timedelta(days=30)
    
    suppliers = Supplier.query.filter_by(project_id=project_id).all()
    result = []
    for s in suppliers:
        status = None
        expire_type = None
        expire_date = None
        
        if s.license_expire_date:
            if s.license_expire_date < today:
                status = 'expired'
                expire_type = '营业执照'
                expire_date = s.license_expire_date
            elif s.license_expire_date <= warning_date:
                if status != 'expired':
                    status = 'warning'
                    expire_type = '营业执照'
                    expire_date = s.license_expire_date
        
        if s.certificate_expire_date:
            if s.certificate_expire_date < today:
                status = 'expired'
                expire_type = '资质证书'
                expire_date = s.certificate_expire_date
            elif s.certificate_expire_date <= warning_date and status != 'expired':
                status = 'warning'
                expire_type = '资质证书'
                expire_date = s.certificate_expire_date
        
        if status:
            result.append({
                'id': s.id,
                'name': s.name,
                'expire_type': expire_type,
                'expire_date': expire_date.strftime('%Y-%m-%d') if expire_date else None,
                'status': status
            })
    
    return jsonify(result)


@bp.route('/ledger')
@login_required
def ledger():
    """供应商往来台账 - 汇总视图"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    from sqlalchemy import func
    from app.models import Payment, Reconciliation, Invoice

    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)
    keyword = request.args.get('keyword', '', type=str)

    query = Supplier.query.filter_by(project_id=project_id)
    if keyword:
        query = query.filter(Supplier.name.contains(keyword) | Supplier.code.contains(keyword))

    suppliers = query.order_by(Supplier.name).all()

    ledger_data = []
    for s in suppliers:
        contract_amount = db.session.query(func.coalesce(func.sum(Contract.amount_with_tax), 0)).filter(
            Contract.supplier_id == s.id,
            Contract.approval_status == 'passed'
        ).scalar() or 0

        stock_in_query = StockIn.query.filter_by(
            supplier_id=s.id, approval_status='passed'
        )
        if date_from:
            try:
                stock_in_query = stock_in_query.filter(StockIn.stock_in_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
            except Exception:
                pass
        if date_to:
            try:
                stock_in_query = stock_in_query.filter(StockIn.stock_in_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
            except Exception:
                pass
        stock_in_amount = db.session.query(func.coalesce(func.sum(StockIn.total_amount), 0)).filter(
            StockIn.id.in_([si.id for si in stock_in_query.all()])
        ).scalar() or 0 if stock_in_query.count() > 0 else 0

        recon_query = Reconciliation.query.filter_by(
            supplier_id=s.id, status='已确认'
        )
        if date_from:
            try:
                recon_query = recon_query.filter(Reconciliation.end_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
            except Exception:
                pass
        if date_to:
            try:
                recon_query = recon_query.filter(Reconciliation.start_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
            except Exception:
                pass
        recon_amount = 0
        recon_list = recon_query.all()
        if recon_list:
            recon_amount = sum(float(r.total_amount or 0) for r in recon_list)

        invoice_amount = 0
        invoices = Invoice.query.filter_by(supplier_id=s.id).all()
        if invoices:
            invoice_amount = sum(float(inv.amount_with_tax or 0) for inv in invoices)

        payment_query = Payment.query.filter_by(
            supplier_id=s.id, approval_status='passed'
        )
        if date_from:
            try:
                payment_query = payment_query.filter(Payment.payment_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
            except Exception:
                pass
        if date_to:
            try:
                payment_query = payment_query.filter(Payment.payment_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
            except Exception:
                pass
        payment_amount = 0
        payments = payment_query.all()
        if payments:
            payment_amount = sum(float(p.amount or 0) for p in payments)

        owed_amount = float(recon_amount) - float(payment_amount)

        ledger_data.append({
            'id': s.id,
            'name': s.name,
            'code': s.code or '',
            'contract_amount': round(float(contract_amount), 2),
            'stock_in_amount': round(float(stock_in_amount), 2),
            'recon_amount': round(float(recon_amount), 2),
            'invoice_amount': round(float(invoice_amount), 2),
            'payment_amount': round(float(payment_amount), 2),
            'owed_amount': round(owed_amount, 2),
        })

    return render_template('supplier/ledger.html',
                           ledger_data=ledger_data,
                           date_from=date_from, date_to=date_to, keyword=keyword)


@bp.route('/<int:id>/ledger')
@login_required
def supplier_ledger_detail(id):
    """供应商往来台账 - 明细视图"""
    supplier = Supplier.query.get_or_404(id)
    project_id = supplier.project_id

    from app.models import Payment, Reconciliation, Invoice

    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)

    records = []

    stock_in_query = StockIn.query.filter_by(
        supplier_id=id, approval_status='passed'
    )
    if date_from:
        try:
            stock_in_query = stock_in_query.filter(StockIn.stock_in_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            stock_in_query = stock_in_query.filter(StockIn.stock_in_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass
    for si in stock_in_query.all():
        records.append({
            'date': si.stock_in_date,
            'type': '入库',
            'type_code': 'stock_in',
            'doc_no': si.code,
            'description': si.stock_in_type,
            'debit': float(si.total_amount or 0),
            'credit': 0,
            'balance': 0,
            'ref_id': si.id,
        })

    recon_query = Reconciliation.query.filter_by(
        supplier_id=id, status='已确认'
    )
    if date_from:
        try:
            recon_query = recon_query.filter(Reconciliation.end_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            recon_query = recon_query.filter(Reconciliation.start_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass
    for r in recon_query.all():
        records.append({
            'date': r.end_date,
            'type': '对账',
            'type_code': 'reconciliation',
            'doc_no': r.code,
            'description': f'对账确认（{r.start_date}~{r.end_date}）',
            'debit': 0,
            'credit': 0,
            'balance': 0,
            'ref_id': r.id,
        })

    invoices = Invoice.query.filter_by(supplier_id=id).all()
    for inv in invoices:
        if date_from:
            try:
                if inv.invoice_date and inv.invoice_date < datetime.strptime(date_from, '%Y-%m-%d').date():
                    continue
            except Exception:
                pass
        if date_to:
            try:
                if inv.invoice_date and inv.invoice_date > datetime.strptime(date_to, '%Y-%m-%d').date():
                    continue
            except Exception:
                pass
        records.append({
            'date': inv.invoice_date,
            'type': '开票',
            'type_code': 'invoice',
            'doc_no': inv.invoice_number or '',
            'description': inv.remark or '发票',
            'debit': 0,
            'credit': 0,
            'balance': 0,
            'ref_id': inv.id,
        })

    payment_query = Payment.query.filter_by(
        supplier_id=id, approval_status='passed'
    )
    if date_from:
        try:
            payment_query = payment_query.filter(Payment.payment_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            payment_query = payment_query.filter(Payment.payment_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass
    for p in payment_query.all():
        records.append({
            'date': p.payment_date,
            'type': '付款',
            'type_code': 'payment',
            'doc_no': p.payment_code,
            'description': p.method or '付款',
            'debit': 0,
            'credit': float(p.amount or 0),
            'balance': 0,
            'ref_id': p.id,
        })

    records.sort(key=lambda x: x['date'] or date.min)

    balance = 0
    for r in records:
        balance += r['debit'] - r['credit']
        r['balance'] = round(balance, 2)

    total_debit = round(sum(r['debit'] for r in records), 2)
    total_credit = round(sum(r['credit'] for r in records), 2)

    summary = {
        'contract_amount': 0,
        'stock_in_amount': 0,
        'recon_amount': 0,
        'invoice_amount': 0,
        'payment_amount': 0,
        'owed_amount': 0,
    }
    from sqlalchemy import func
    contract_amount = db.session.query(func.coalesce(func.sum(Contract.amount_with_tax), 0)).filter(
        Contract.supplier_id == id,
        Contract.approval_status == 'passed'
    ).scalar() or 0
    summary['contract_amount'] = round(float(contract_amount), 2)
    summary['stock_in_amount'] = round(sum(float(si.total_amount or 0) for si in stock_in_query.all()), 2)
    summary['recon_amount'] = round(sum(float(r.total_amount or 0) for r in recon_query.all()), 2)
    summary['invoice_amount'] = round(sum(float(inv.amount_with_tax or 0) for inv in invoices), 2)
    summary['payment_amount'] = round(sum(float(p.amount or 0) for p in payment_query.all()), 2)
    summary['owed_amount'] = round(summary['recon_amount'] - summary['payment_amount'], 2)

    return render_template('supplier/ledger_detail.html',
                           supplier=supplier,
                           records=records,
                           summary=summary,
                           total_debit=total_debit,
                           total_credit=total_credit,
                           date_from=date_from, date_to=date_to)


@bp.route('/<int:id>/ledger/export')
@login_required
def supplier_ledger_export(id):
    """导出供应商往来台账明细"""
    supplier = Supplier.query.get_or_404(id)

    from app.models import Payment, Reconciliation, Invoice
    from flask import Response

    date_from = request.args.get('date_from', '', type=str)
    date_to = request.args.get('date_to', '', type=str)

    records = []

    stock_in_query = StockIn.query.filter_by(
        supplier_id=id, approval_status='passed'
    )
    if date_from:
        try:
            stock_in_query = stock_in_query.filter(StockIn.stock_in_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            stock_in_query = stock_in_query.filter(StockIn.stock_in_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass
    for si in stock_in_query.all():
        records.append({
            'date': str(si.stock_in_date or ''),
            'type': '入库',
            'doc_no': si.code,
            'description': si.stock_in_type,
            'debit': float(si.total_amount or 0),
            'credit': 0,
            'balance': 0,
        })

    recon_query = Reconciliation.query.filter_by(
        supplier_id=id, status='已确认'
    )
    if date_from:
        try:
            recon_query = recon_query.filter(Reconciliation.end_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            recon_query = recon_query.filter(Reconciliation.start_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass
    for r in recon_query.all():
        records.append({
            'date': str(r.end_date or ''),
            'type': '对账',
            'doc_no': r.code,
            'description': f'对账确认（{r.start_date}~{r.end_date}）',
            'debit': 0,
            'credit': 0,
            'balance': 0,
        })

    invoices = Invoice.query.filter_by(supplier_id=id).all()
    for inv in invoices:
        if date_from:
            try:
                if inv.invoice_date and inv.invoice_date < datetime.strptime(date_from, '%Y-%m-%d').date():
                    continue
            except Exception:
                pass
        if date_to:
            try:
                if inv.invoice_date and inv.invoice_date > datetime.strptime(date_to, '%Y-%m-%d').date():
                    continue
            except Exception:
                pass
        records.append({
            'date': str(inv.invoice_date or ''),
            'type': '开票',
            'doc_no': inv.invoice_number or '',
            'description': inv.remark or '发票',
            'debit': 0,
            'credit': 0,
            'balance': 0,
        })

    payment_query = Payment.query.filter_by(
        supplier_id=id, approval_status='passed'
    )
    if date_from:
        try:
            payment_query = payment_query.filter(Payment.payment_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except Exception:
            pass
    if date_to:
        try:
            payment_query = payment_query.filter(Payment.payment_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except Exception:
            pass
    for p in payment_query.all():
        records.append({
            'date': str(p.payment_date or ''),
            'type': '付款',
            'doc_no': p.payment_code,
            'description': p.method or '付款',
            'debit': 0,
            'credit': float(p.amount or 0),
            'balance': 0,
        })

    records.sort(key=lambda x: x['date'])

    balance = 0
    for r in records:
        balance += r['debit'] - r['credit']
        r['balance'] = round(balance, 2)

    headers = ['序号', '日期', '类型', '单据号', '摘要', '借方金额', '贷方金额', '余额']
    rows = []
    for idx, r in enumerate(records, 1):
        rows.append([
            idx, r['date'], r['type'], r['doc_no'], r['description'],
            r['debit'], r['credit'], r['balance']
        ])

    filename = f'{supplier.name}_往来台账_{datetime.now().strftime("%Y%m%d")}.xlsx'
    data = export_to_excel(headers, rows, '往来台账', filename)

    return Response(data, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})
