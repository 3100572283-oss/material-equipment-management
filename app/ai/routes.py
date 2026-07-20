"""AI助手路由"""
from flask import request, jsonify, session, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from app.ai import bp
from app.ai.service import get_ai_service, get_ai_config, save_ai_config
from app.models import (Inventory, Material, Supplier, StockIn, StockOut, 
                       Contract, PurchaseRequisition, StockCheck, 
                       MaterialTransfer, TurnoverRecord, Equipment)
from app import db
import json
from datetime import datetime, timedelta


@bp.route('/chat', methods=['POST'])
@login_required
def chat():
    """智能问答"""
    ai = get_ai_service()
    if not ai.is_enabled():
        return jsonify({'success': False, 'message': 'AI助手未启用'})
    
    data = request.get_json()
    user_message = data.get('message', '')
    context = data.get('context', [])
    
    if not user_message:
        return jsonify({'success': False, 'message': '请输入问题'})
    
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': '请先选择项目'})
    
    data_context = _build_data_context(user_message, project_id)
    
    response_text, error = ai.chat(user_message + '\n\n当前项目数据：\n' + json.dumps(data_context, ensure_ascii=False), context)
    
    if error:
        return jsonify({'success': False, 'message': error})
    
    return jsonify({'success': True, 'message': response_text})


def _build_data_context(user_message, project_id):
    """根据用户问题构建数据上下文"""
    context = {}
    msg = user_message.lower()
    
    if any(k in msg for k in ['库存', '材料', '物资']):
        inventories = Inventory.query.filter_by(project_id=project_id).all()
        context['inventory'] = []
        for inv in inventories[:20]:
            mat = inv.material
            context['inventory'].append({
                'material': mat.name,
                'specification': mat.specification or '',
                'quantity': float(inv.quantity or 0),
                'unit': mat.unit,
                'category': mat.category.name if mat.category else ''
            })
    
    if any(k in msg for k in ['供应商', '供货', '欠款', '评级']):
        suppliers = Supplier.query.filter_by(project_id=project_id).all()
        context['suppliers'] = []
        for s in suppliers[:20]:
            total_payment = sum(p.amount or 0 for p in s.contracts.join(db.session.query(Contract).filter(Contract.id == db.session.query(s.contracts).subquery().c.contract_id)).all()) if s.contracts.count() > 0 else 0
            context['suppliers'].append({
                'name': s.name,
                'code': s.code or ''
            })
    
    if any(k in msg for k in ['入库']):
        stock_ins = StockIn.query.filter_by(project_id=project_id).order_by(StockIn.stock_in_date.desc()).limit(10).all()
        context['stock_ins'] = []
        for si in stock_ins:
            context['stock_ins'].append({
                'code': si.code,
                'date': si.stock_in_date.strftime('%Y-%m-%d') if si.stock_in_date else '',
                'supplier': si.supplier.name if si.supplier else '',
                'total_amount': float(si.total_amount or 0),
                'items': [{'material': i.material.name, 'quantity': float(i.quantity or 0)} for i in si.items]
            })
    
    if any(k in msg for k in ['出库', '领料', '班组']):
        stock_outs = StockOut.query.filter_by(project_id=project_id).order_by(StockOut.stock_out_date.desc()).limit(10).all()
        context['stock_outs'] = []
        for so in stock_outs:
            context['stock_outs'].append({
                'code': so.code,
                'date': so.stock_out_date.strftime('%Y-%m-%d') if so.stock_out_date else '',
                'usage_unit': so.usage_unit.name if so.usage_unit else '',
                'total_amount': float(so.total_amount or 0),
                'items': [{'material': i.material.name, 'quantity': float(i.quantity or 0)} for i in so.items]
            })
    
    if any(k in msg for k in ['合同', '付款', '履约']):
        contracts = Contract.query.filter_by(project_id=project_id).limit(10).all()
        context['contracts'] = []
        for c in contracts:
            paid = sum(p.amount or 0 for p in c.payments)
            invoiced = sum(i.amount_with_tax or 0 for i in c.invoices)
            context['contracts'].append({
                'code': c.code,
                'name': c.name,
                'amount_with_tax': float(c.amount_with_tax or 0),
                'paid_amount': float(paid),
                'invoiced_amount': float(invoiced),
                'supplier': c.supplier.name if c.supplier else '',
                'status': c.status
            })
    
    if any(k in msg for k in ['采购', '申请']):
        if current_user.role == 'admin':
            requisitions = PurchaseRequisition.query.filter_by(project_id=project_id).order_by(PurchaseRequisition.apply_date.desc()).limit(10).all()
        else:
            requisitions = PurchaseRequisition.query.filter_by(project_id=project_id, apply_user=current_user.name or current_user.username).order_by(PurchaseRequisition.apply_date.desc()).limit(10).all()
        context['purchase_requisitions'] = []
        for r in requisitions:
            context['purchase_requisitions'].append({
                'pr_no': r.pr_no,
                'apply_date': r.apply_date.strftime('%Y-%m-%d') if r.apply_date else '',
                'apply_user': r.apply_user or '',
                'status': r.status,
                'items': [{'material': i.material_name, 'quantity': float(i.apply_qty or 0)} for i in r.items]
            })
    
    if any(k in msg for k in ['盘点', '盘盈', '盘亏']):
        checks = StockCheck.query.filter_by(project_id=project_id).order_by(StockCheck.check_date.desc()).limit(5).all()
        context['stock_checks'] = []
        for c in checks:
            context['stock_checks'].append({
                'check_no': c.check_no,
                'check_date': c.check_date.strftime('%Y-%m-%d'),
                'status': c.status,
                'diff_count': c.diff_count
            })
    
    if any(k in msg for k in ['调拨']):
        transfers = MaterialTransfer.query.filter(
            (MaterialTransfer.from_project_id == project_id) | (MaterialTransfer.to_project_id == project_id)
        ).order_by(MaterialTransfer.transfer_date.desc()).limit(5).all()
        context['transfers'] = []
        for t in transfers:
            context['transfers'].append({
                'transfer_no': t.transfer_no,
                'date': t.transfer_date.strftime('%Y-%m-%d'),
                'from_project': t.from_project.name,
                'to_project': t.to_project.name,
                'status': t.status
            })
    
    if any(k in msg for k in ['周转材', '租赁']):
        records = TurnoverRecord.query.filter_by(project_id=project_id, status='in_use').all()
        context['turnover_materials'] = []
        for r in records[:10]:
            context['turnover_materials'].append({
                'material': r.material.name,
                'qty': float(r.qty or 0),
                'using_qty': float(r.using_qty or 0),
                'team': r.team or '',
                'rent_fee': float(r.rent_fee or 0)
            })
    
    if any(k in msg for k in ['设备', '维保', '维修']):
        equipments = Equipment.query.filter_by(project_id=project_id).all()
        context['equipments'] = []
        for e in equipments[:10]:
            context['equipments'].append({
                'name': e.name,
                'code': e.code,
                'status': e.status,
                'department': e.department or '',
                'next_maintain_date': e.maintenance[-1].next_maintain_date.strftime('%Y-%m-%d') if e.maintenance else ''
            })
    
    return context


@bp.route('/analyze', methods=['POST'])
@login_required
def analyze():
    """报表智能分析"""
    ai = get_ai_service()
    if not ai.is_enabled():
        return jsonify({'success': False, 'message': 'AI助手未启用'})
    
    data = request.get_json()
    report_data = data.get('data', [])
    report_type = data.get('type', '报表')
    
    response_text, error = ai.analyze_report(report_data, report_type)
    
    if error:
        return jsonify({'success': False, 'message': error})
    
    return jsonify({'success': True, 'message': response_text})


@bp.route('/parse', methods=['POST'])
@login_required
def parse_input():
    """智能录入解析"""
    ai = get_ai_service()
    if not ai.is_enabled():
        return jsonify({'success': False, 'message': 'AI助手未启用'})
    
    data = request.get_json()
    user_input = data.get('input', '')
    
    if not user_input:
        return jsonify({'success': False, 'message': '请输入内容'})
    
    result, error = ai.parse_input(user_input)
    
    if error:
        return jsonify({'success': False, 'message': error})
    
    return jsonify({'success': True, 'data': result})


@bp.route('/summarize', methods=['POST'])
@login_required
def summarize():
    """单据智能摘要"""
    ai = get_ai_service()
    if not ai.is_enabled():
        return jsonify({'success': False, 'message': 'AI助手未启用'})
    
    data = request.get_json()
    doc_type = data.get('type', '单据')
    doc_data = data.get('data', {})
    
    response_text, error = ai.summarize_document(doc_type, doc_data)
    
    if error:
        return jsonify({'success': False, 'message': error})
    
    return jsonify({'success': True, 'message': response_text})


@bp.route('/opinion', methods=['POST'])
@login_required
def generate_opinion():
    """审批意见辅助生成"""
    ai = get_ai_service()
    if not ai.is_enabled():
        return jsonify({'success': False, 'message': 'AI助手未启用'})
    
    data = request.get_json()
    doc_type = data.get('type', '单据')
    doc_data = data.get('data', {})
    action = data.get('action', 'approve')
    
    response_text, error = ai.generate_opinion(doc_type, doc_data, action)
    
    if error:
        return jsonify({'success': False, 'message': error})
    
    return jsonify({'success': True, 'message': response_text})


@bp.route('/config', methods=['GET', 'POST'])
@login_required
def config():
    """AI配置"""
    from app.decorators import admin_required

    if not current_user.is_admin():
        flash('无权限', 'error')
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        config = {
            'enabled': request.form.get('ai_enabled', 'false'),
            'api_key': request.form.get('ai_api_key', ''),
            'model': request.form.get('ai_model', 'doubao-pro-32k'),
            'base_url': request.form.get('ai_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'),
            'max_tokens': int(request.form.get('ai_max_tokens', '2000')),
            'vision_enabled': request.form.get('ai_vision_enabled', 'false'),
            'vision_model': request.form.get('ai_vision_model', 'doubao-vision-pro-32k'),
            'vision_base_url': request.form.get('ai_vision_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'),
            'vision_max_tokens': int(request.form.get('ai_vision_max_tokens', '2000'))
        }
        save_ai_config(config)
        flash('AI配置已更新', 'success')
        return redirect(url_for('ai.config'))
    
    config = get_ai_config()
    return render_template('admin/ai_config.html', config=config)


@bp.route('/logs')
@login_required
def logs():
    """AI调用日志"""
    from app.decorators import admin_required
    from app.models import AICallLog
    
    if not current_user.is_admin():
        flash('无权限', 'error')
        return redirect(url_for('main.index'))
    
    page = request.args.get('page', 1, type=int)
    module = request.args.get('module', '', type=str)
    success = request.args.get('success', '', type=str)
    
    query = AICallLog.query
    if module:
        query = query.filter(AICallLog.module == module)
    if success:
        query = query.filter(AICallLog.success == (success == 'true'))
    
    pagination = query.order_by(AICallLog.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('admin/ai_logs.html', pagination=pagination)


@bp.route('/is_enabled')
@login_required
def is_enabled():
    """检查AI是否启用"""
    ai = get_ai_service()
    return jsonify({'enabled': ai.is_enabled(), 'vision_enabled': ai.is_vision_enabled()})


@bp.route('/vision/recognize', methods=['POST'])
@login_required
def vision_recognize():
    """视觉识别API"""
    ai = get_ai_service()
    if not ai.is_vision_enabled():
        return jsonify({'success': False, 'message': '视觉识别未启用'})
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效请求数据'})
    
    image_base64 = data.get('image', '')
    recognize_type = data.get('type', 'ocr')  # license/invoice/receipt/ocr
    
    if not image_base64:
        return jsonify({'success': False, 'message': '请上传图片'})
    
    # 移除可能的data:image/xxx;base64,前缀
    if ',' in image_base64:
        image_base64 = image_base64.split(',')[1]
    
    result = None
    error = None
    
    if recognize_type == 'license':
        result, error = ai.recognize_business_license(image_base64)
    elif recognize_type == 'invoice':
        result, error = ai.recognize_invoice(image_base64)
    elif recognize_type == 'receipt':
        result, error = ai.recognize_receipt(image_base64)
    else:
        result, error = ai.extract_text(image_base64)
    
    if error:
        return jsonify({'success': False, 'message': error})
    
    return jsonify({'success': True, 'data': result})
