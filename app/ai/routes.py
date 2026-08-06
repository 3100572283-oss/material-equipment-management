"""AI助手路由（统一中台版本）

所有AI接口统一在此，受系统AI总开关 + 场景开关 + 用户权限三重控制。
权限校验规则：
1. 系统AI总开关关闭 → 所有AI接口返回禁用
2. 场景未启用 → 对应接口返回禁用
3. 用户无对应按钮权限 → 403拦截
"""
from flask import request, jsonify, session, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from app.ai import bp

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

def _validate_upload_files(files):
    """校验上传文件列表扩展名，不通过则abort 400"""
    from app.utils import validate_file_extension
    from flask import abort
    for f in files:
        if f and f.filename:
            ok, err = validate_file_extension(f.filename)
            if not ok:
                abort(400, err)

from app.ai.service import get_ai_service, get_ai_config, save_ai_config
from app.models import (Inventory, Material, Supplier, StockIn, StockOut,
                       Contract, PurchaseRequisition, StockCheck,
                       MaterialTransfer, TurnoverRecord, Equipment,
                       AICallLog, AIScenePrompt, SysMenu)
from app import db
from app.decorators import admin_required, permission_required
import json
from datetime import datetime, timedelta
from sqlalchemy import func
import time
from collections import defaultdict


# ================= P2-14: AI接口速率限制 =================

class AIRateLimiter:
    """简单的内存速率限制器（每用户每分钟N次）"""
    _instance = None
    _requests = defaultdict(list)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def check(self, user_id, max_per_minute=10):
        """检查是否允许请求"""
        now = time.time()
        window = 60
        self._requests[user_id] = [t for t in self._requests[user_id] if now - t < window]
        if len(self._requests[user_id]) >= max_per_minute:
            oldest = self._requests[user_id][0]
            retry_after = int(window - (now - oldest)) + 1
            return False, max(retry_after, 1)
        self._requests[user_id].append(now)
        return True, 0

ai_rate_limiter = AIRateLimiter()


def _check_ai_rate_limit():
    """检查AI接口速率限制"""
    allowed, retry_after = ai_rate_limiter.check(current_user.id, max_per_minute=10)
    if not allowed:
        return jsonify({
            'success': False,
            'code': 429,
            'message': f'请求过于频繁，请在{retry_after}秒后重试'
        }), 429, {'Retry-After': str(retry_after)}
    return None


# ================= 全局权限校验 =================

@bp.before_request
def _ai_global_check():
    """全局AI中间件：校验系统总开关

    注意：配置页面和查询接口本身不受开关限制（管理员需要能看到配置页）
    """
    # 白名单：配置、日志、权限查询等管理功能不受总开关限制
    public_paths = ['/config', '/logs', '/is_enabled', '/permission/', '/scene/', '/statistics', '/api/']
    path = request.path.replace('/ai', '', 1) if request.path.startswith('/ai') else request.path

    # 配置和管理页面只需要管理员权限，不受AI总开关影响
    for pp in public_paths:
        if path.startswith(pp):
            return None

    # 其他AI调用接口检查总开关
    ai = get_ai_service()
    if not ai.is_enabled():
        if request.path.startswith('/api/') or request.is_json or request.method == 'POST':
            return jsonify({'success': False, 'code': 403, 'message': 'AI助手未启用，请联系管理员开启'}), 403
        flash('AI助手未启用', 'warning')
        return redirect(url_for('main.index'))


# ================= 权限查询接口 =================

@bp.route('/permission/check')
@login_required
def permission_check():
    """批量查询当前用户可用的AI功能权限

    供前端判断是否显示AI按钮，返回所有AI场景的启用状态和当前用户权限。
    返回格式：
    {
      "enabled": true,          // AI总开关
      "vision_enabled": true,   // 视觉开关
      "speech_enabled": false,  // 语音开关
      "scenes": {
        "vision:invoice": { "enabled": true, "has_permission": true },
        ...
      }
    }
    """
    ai = get_ai_service()
    scenes = ai.get_all_scenes()

    scene_status = {}
    for s in scenes:
        has_perm = True
        if not current_user.is_admin() and s.permission_code:
            from app.services.permission_service import permission_service
            has_perm = permission_service.check_permission(current_user, s.permission_code)

        scene_status[s.scene_code] = {
            'enabled': s.is_enabled,
            'has_permission': has_perm,
            'scene_name': s.scene_name,
            'scene_type': s.scene_type,
        }

    return jsonify({
        'enabled': ai.is_enabled(),
        'vision_enabled': ai.is_vision_enabled(),
        'speech_enabled': ai.is_speech_enabled(),
        'scenes': scene_status,
    })


# ================= 基础功能接口 =================

@bp.route('/chat', methods=['POST'])
@login_required
def chat():
    """智能问答"""
    ai = get_ai_service()

    data = request.get_json(silent=True) or request.form
    user_message = (data.get('message', '') or '').strip()
    context = data.get('context', [])

    if not user_message:
        return jsonify({'success': False, 'message': '请输入问题'})

    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': '请先选择项目'})

    data_context = _build_data_context(user_message, project_id)

    data_json = json.dumps(data_context, ensure_ascii=False)
    if len(data_json) > 5000:
        data_json = data_json[:5000] + '...(数据已截断)'

    response_text, error = ai.chat(
        user_message + '\n\n当前项目数据：\n' + data_json,
        context
    )

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
            context['suppliers'].append({
                'name': s.name[:4] + '***' if s.name and len(s.name) > 4 else (s.name or ''),
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

    data = request.get_json()
    doc_type = data.get('type', '单据')
    doc_data = data.get('data', {})
    action = data.get('action', 'approve')

    response_text, error = ai.generate_opinion(doc_type, doc_data, action)

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'message': response_text})


# ================= 视觉识别 =================

@bp.route('/vision/recognize', methods=['POST'])
@login_required
def vision_recognize():
    # P2-14: 速率限制
    _rate_err = _check_ai_rate_limit()
    if _rate_err:
        return _rate_err

    """视觉识别API（统一入口）

    支持两种上传方式：
    1. JSON: {type: 'license', image: 'base64...'}
    2. FormData: image=文件对象, type=license (支持图片和PDF)

    type: license/invoice/receipt/concrete/ocr
    """
    ai = get_ai_service()
    if not ai.is_vision_enabled():
        return jsonify({'success': False, 'message': '视觉识别未启用'})

    import base64 as _b64

    def _do_recognize(image_base64, recognize_type):
        """执行识别，返回 (result, error)"""
        if recognize_type == 'license':
            return ai.recognize_business_license(image_base64)
        elif recognize_type == 'invoice':
            return ai.recognize_invoice(image_base64)
        elif recognize_type == 'receipt':
            return ai.recognize_receipt(image_base64)
        elif recognize_type == 'concrete':
            return ai.recognize_concrete_ticket(image_base64)
        else:
            return ai.extract_text(image_base64)

    # 尝试JSON方式（base64图片）
    data = request.get_json(silent=True)
    if data:
        image_base64 = data.get('image', '')
        recognize_type = data.get('type', 'ocr')
        if not image_base64:
            return jsonify({'success': False, 'message': '请上传图片'})
        if ',' in image_base64:
            image_base64 = image_base64.split(',')[1]
        result, error = _do_recognize(image_base64, recognize_type)
        if error:
            return jsonify({'success': False, 'message': error})
        return jsonify({'success': True, 'data': result})

    # FormData方式（文件上传，支持图片和PDF）
    file = request.files.get('image') or request.files.get('file')
    recognize_type = request.form.get('type', 'ocr')

    if not file:
        return jsonify({'success': False, 'message': '请上传文件'})

    filename = (file.filename or '').lower()

    if filename.endswith('.pdf') or file.content_type == 'application/pdf':
        # PDF处理：用PyMuPDF将每页转为图片
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return jsonify({'success': False, 'message': '服务器未安装PDF处理库，请联系管理员'})

        try:
            pdf_bytes = file.read()
            doc = fitz.open(stream=pdf_bytes, filetype='pdf')
            total_pages = len(doc)

            if total_pages == 0:
                return jsonify({'success': False, 'message': 'PDF文件为空'})

            # 限制最多处理10页
            max_pages = min(total_pages, 10)
            results = []

            for page_num in range(max_pages):
                page = doc[page_num]
                # 渲染页面为图片（DPI=200保证清晰度）
                mat = fitz.Matrix(200/72, 200/72)
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes('png')
                img_base64 = _b64.b64encode(img_bytes).decode('utf-8')

                result, error = _do_recognize(img_base64, recognize_type)
                if error:
                    results.append({'page': page_num + 1, 'error': error})
                else:
                    results.append({'page': page_num + 1, 'data': result})

            doc.close()

            # 单页PDF直接返回结果
            if max_pages == 1:
                if results[0].get('error'):
                    return jsonify({'success': False, 'message': results[0]['error']})
                return jsonify({'success': True, 'data': results[0]['data']})

            # 多页PDF返回分页结果
            return jsonify({'success': True, 'data': results, 'pages': max_pages, 'total_pages': total_pages})

        except Exception as e:
            return jsonify({'success': False, 'message': f'PDF处理失败: {str(e)}'})

    else:
        # 图片处理：转为base64
        img_bytes = file.read()
        if len(img_bytes) > 10 * 1024 * 1024:
            return jsonify({'success': False, 'message': '文件大小不能超过10MB'})
        image_base64 = _b64.b64encode(img_bytes).decode('utf-8')
        result, error = _do_recognize(image_base64, recognize_type)
        if error:
            return jsonify({'success': False, 'message': error})
        return jsonify({'success': True, 'data': result})


# ================= 语音转文字 =================

@bp.route('/speech/to_text', methods=['POST'])
@login_required
def speech_to_text():
    """语音转文字

    支持 multipart/form-data 上传音频文件，或 base64 格式的JSON请求。
    """
    ai = get_ai_service()
    if not ai.is_speech_enabled():
        return jsonify({'success': False, 'message': '语音识别未启用'})

    audio_data = None
    audio_format = 'webm'

    # 文件上传方式
    if 'file' in request.files:
        f = request.files['file']
        _validate_upload_file(f)
        if f.filename:
            audio_data = f.read()
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'webm'
            audio_format = ext if ext in ['webm', 'mp3', 'wav', 'm4a', 'ogg'] else 'webm'
    else:
        # base64方式
        data = request.get_json() or {}
        audio_base64 = data.get('audio', '')
        if not audio_base64:
            return jsonify({'success': False, 'message': '请上传音频文件'})
        if ',' in audio_base64:
            audio_base64 = audio_base64.split(',')[1]
        import base64
        try:
            audio_data = base64.b64decode(audio_base64)
        except Exception:
            return jsonify({'success': False, 'message': '音频数据格式错误'})
        audio_format = data.get('format', 'webm')

    if not audio_data:
        return jsonify({'success': False, 'message': '音频数据为空'})

    result_text, error = ai.speech_to_text(audio_data, audio_format=audio_format)

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'text': result_text})


# ================= 结构化生成 =================

@bp.route('/structured/generate', methods=['POST'])
@login_required
def structured_generate():
    """结构化文档生成"""
    ai = get_ai_service()

    data = request.get_json()
    doc_type = data.get('doc_type', '')
    biz_data = data.get('data', {})
    template = data.get('template', None)

    if not doc_type:
        return jsonify({'success': False, 'message': '请指定文档类型'})

    result, error = ai.generate_structured(doc_type, biz_data, template)

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'data': result})


# ================= AI配置管理 =================

@bp.route('/config', methods=['GET', 'POST'])
@login_required
@admin_required
def config():
    """AI配置（管理员）"""
    if request.method == 'POST':
        config_data = {
            'enabled': request.form.get('ai_enabled', 'false'),
            'provider': request.form.get('ai_provider', 'doubao'),
            'api_key': request.form.get('ai_api_key', ''),
            'model': request.form.get('ai_model', 'doubao-1.5-pro-32k'),
            'base_url': request.form.get('ai_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'),
            'max_tokens': int(request.form.get('ai_max_tokens', '2000')),
            'vision_enabled': request.form.get('ai_vision_enabled', 'false'),
            'vision_model': request.form.get('ai_vision_model', 'doubao-1.5-vision-pro'),
            'vision_base_url': request.form.get('ai_vision_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'),
            'vision_max_tokens': int(request.form.get('ai_vision_max_tokens', '2000')),
            'speech_enabled': request.form.get('ai_speech_enabled', 'false'),
            'speech_model': request.form.get('ai_speech_model', 'doubao-speech'),
            'speech_base_url': request.form.get('ai_speech_base_url', 'https://ark.cn-beijing.volces.com/api/v3/audio/transcriptions'),
            'text_price': float(request.form.get('ai_text_price', '0.003')),
            'vision_price': float(request.form.get('ai_vision_price', '0.01')),
        }
        save_ai_config(config_data)
        flash('AI配置已更新', 'success')
        return redirect(url_for('ai.config'))

    config_data = get_ai_config()
    return render_template('admin/ai_config.html', config=config_data)


@bp.route('/is_enabled')
@login_required
def is_enabled():
    """检查AI是否启用"""
    ai = get_ai_service()
    return jsonify({
        'enabled': ai.is_enabled(),
        'vision_enabled': ai.is_vision_enabled(),
        'speech_enabled': ai.is_speech_enabled(),
    })


# ================= 场景Prompt管理 =================

@bp.route('/scene/list')
@login_required
@admin_required
def scene_list():
    """场景Prompt列表页"""
    page = request.args.get('page', 1, type=int)
    scene_type = request.args.get('scene_type', '', type=str)
    keyword = request.args.get('keyword', '', type=str).strip()

    query = AIScenePrompt.query
    if scene_type:
        query = query.filter(AIScenePrompt.scene_type == scene_type)
    if keyword:
        query = query.filter(
            (AIScenePrompt.scene_name.like(f'%{keyword}%')) |
            (AIScenePrompt.scene_code.like(f'%{keyword}%'))
        )

    pagination = query.order_by(AIScenePrompt.sort_order.asc(), AIScenePrompt.id.asc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template('admin/ai_scene_prompt.html', pagination=pagination,
                         scene_type=scene_type, keyword=keyword)


@bp.route('/scene/save', methods=['POST'])
@login_required
@admin_required
def scene_save():
    """保存场景Prompt（新增/编辑）"""
    scene_id = request.form.get('id', type=int)
    scene_code = request.form.get('scene_code', '').strip()
    scene_name = request.form.get('scene_name', '').strip()
    scene_type = request.form.get('scene_type', 'text')
    system_prompt = request.form.get('system_prompt', '')
    output_format = request.form.get('output_format', '')
    permission_code = request.form.get('permission_code', '')
    is_enabled = request.form.get('is_enabled') == '1'
    sort_order = request.form.get('sort_order', 0, type=int)
    remark = request.form.get('remark', '')

    if not scene_code or not scene_name:
        return jsonify({'success': False, 'message': '场景编码和名称不能为空'})

    if scene_id:
        sp = AIScenePrompt.query.get(scene_id)
        if not sp:
            return jsonify({'success': False, 'message': '场景不存在'})
    else:
        if AIScenePrompt.query.filter_by(scene_code=scene_code).first():
            return jsonify({'success': False, 'message': '场景编码已存在'})
        sp = AIScenePrompt(scene_code=scene_code)

    sp.scene_name = scene_name
    sp.scene_type = scene_type
    sp.system_prompt = system_prompt
    sp.output_format = output_format
    sp.permission_code = permission_code
    sp.is_enabled = is_enabled
    sp.sort_order = sort_order
    sp.remark = remark

    db.session.add(sp)
    db.session.commit()

    # 失效缓存
    ai = get_ai_service()
    ai.invalidate_scene_cache()

    return jsonify({'success': True, 'message': '保存成功'})


@bp.route('/scene/delete', methods=['POST'])
@login_required
@admin_required
def scene_delete():
    """删除场景Prompt"""
    scene_id = request.form.get('id', type=int)
    if not scene_id:
        return jsonify({'success': False, 'message': '参数错误'})

    sp = AIScenePrompt.query.get(scene_id)
    if not sp:
        return jsonify({'success': False, 'message': '场景不存在'})

    db.session.delete(sp)
    db.session.commit()

    ai = get_ai_service()
    ai.invalidate_scene_cache()

    return jsonify({'success': True, 'message': '删除成功'})


@bp.route('/scene/detail/<int:scene_id>')
@login_required
@admin_required
def scene_detail(scene_id):
    """获取场景详情"""
    sp = AIScenePrompt.query.get(scene_id)
    if not sp:
        return jsonify({'success': False, 'message': '场景不存在'})
    return jsonify({'success': True, 'data': sp.to_dict()})


# ================= AI调用日志 =================

@bp.route('/logs')
@login_required
@admin_required
def logs():
    """AI调用日志"""
    page = request.args.get('page', 1, type=int)
    module = request.args.get('module', '', type=str)
    scene_code = request.args.get('scene_code', '', type=str)
    success = request.args.get('success', '', type=str)
    username = request.args.get('username', '', type=str).strip()

    query = AICallLog.query
    if module:
        query = query.filter(AICallLog.module == module)
    if scene_code:
        query = query.filter(AICallLog.scene_code == scene_code)
    if success:
        query = query.filter(AICallLog.success == (success == 'true'))
    if username:
        query = query.filter(AICallLog.username.like(f'%{username}%'))

    pagination = query.order_by(AICallLog.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template('admin/ai_logs.html', pagination=pagination)


# ================= AI调用统计 =================

@bp.route('/statistics')
@login_required
@admin_required
def statistics():
    """AI调用统计页"""
    # 默认统计最近30天
    days = request.args.get('days', 30, type=int)
    if days < 1:
        days = 30
    if days > 365:
        days = 365

    start_date = datetime.now().date() - timedelta(days=days - 1)

    # 1. 每日调用量趋势
    daily_stats = db.session.query(
        func.date(AICallLog.created_at).label('date'),
        func.count(AICallLog.id).label('count'),
        func.sum(AICallLog.total_tokens).label('tokens'),
        func.sum(AICallLog.cost_amount).label('cost'),
    ).filter(
        AICallLog.created_at >= datetime.combine(start_date, datetime.min.time())
    ).group_by(
        func.date(AICallLog.created_at)
    ).order_by('date').all()

    # 2. 场景分布
    scene_stats = db.session.query(
        AICallLog.scene_code,
        func.count(AICallLog.id).label('count'),
        func.sum(AICallLog.total_tokens).label('tokens'),
        func.sum(AICallLog.cost_amount).label('cost'),
    ).filter(
        AICallLog.created_at >= datetime.combine(start_date, datetime.min.time())
    ).group_by(AICallLog.scene_code).order_by(func.count(AICallLog.id).desc()).limit(10).all()

    # 3. 用户排行
    user_stats = db.session.query(
        AICallLog.username,
        func.count(AICallLog.id).label('count'),
        func.sum(AICallLog.total_tokens).label('tokens'),
        func.sum(AICallLog.cost_amount).label('cost'),
    ).filter(
        AICallLog.created_at >= datetime.combine(start_date, datetime.min.time()),
        AICallLog.username.isnot(None),
    ).group_by(AICallLog.username).order_by(func.count(AICallLog.id).desc()).limit(10).all()

    # 4. 总体概览
    total_count = db.session.query(func.count(AICallLog.id)).filter(
        AICallLog.created_at >= datetime.combine(start_date, datetime.min.time())
    ).scalar() or 0
    total_tokens = db.session.query(func.sum(AICallLog.total_tokens)).filter(
        AICallLog.created_at >= datetime.combine(start_date, datetime.min.time())
    ).scalar() or 0
    total_cost = db.session.query(func.sum(AICallLog.cost_amount)).filter(
        AICallLog.created_at >= datetime.combine(start_date, datetime.min.time())
    ).scalar() or 0
    success_count = db.session.query(func.count(AICallLog.id)).filter(
        AICallLog.created_at >= datetime.combine(start_date, datetime.min.time()),
        AICallLog.success == True
    ).scalar() or 0
    success_rate = round(success_count / total_count * 100, 1) if total_count > 0 else 0

    # 转为字典列表，避免Row对象无法JSON序列化
    daily_data = [{'date': str(r.date), 'count': r.count,
                   'tokens': int(r.tokens or 0), 'cost': float(r.cost or 0)}
                  for r in daily_stats]
    scene_data = [{'scene_code': r.scene_code or '', 'count': r.count,
                   'tokens': int(r.tokens or 0), 'cost': float(r.cost or 0)}
                  for r in scene_stats]
    user_data = [{'username': r.username or '', 'count': r.count,
                  'tokens': int(r.tokens or 0), 'cost': float(r.cost or 0)}
                 for r in user_stats]

    return render_template('admin/ai_statistics.html',
                         days=days,
                         daily_stats=daily_data,
                         scene_stats=scene_data,
                         user_stats=user_data,
                         total_count=total_count,
                         total_tokens=int(total_tokens or 0),
                         total_cost=float(total_cost or 0),
                         success_rate=success_rate)


@bp.route('/statistics/data')
@login_required
@admin_required
def statistics_data():
    """统计数据JSON接口（图表用）"""
    days = request.args.get('days', 30, type=int)
    start_date = datetime.now().date() - timedelta(days=days - 1)

    daily_stats = db.session.query(
        func.date(AICallLog.created_at).label('date'),
        func.count(AICallLog.id).label('count'),
        func.sum(AICallLog.total_tokens).label('tokens'),
        func.sum(AICallLog.cost_amount).label('cost'),
    ).filter(
        AICallLog.created_at >= datetime.combine(start_date, datetime.min.time())
    ).group_by(
        func.date(AICallLog.created_at)
    ).order_by('date').all()

    data = []
    for row in daily_stats:
        data.append({
            'date': str(row.date),
            'count': row.count or 0,
            'tokens': int(row.tokens or 0),
            'cost': float(row.cost or 0),
        })

    return jsonify({'success': True, 'data': data})


# ============================================================
# AI助手页面
# ============================================================

@bp.route('/assistant')
@login_required
def assistant():
    """AI智能助手页面"""
    from app.models import AICallLog
    from datetime import datetime, timedelta
    from sqlalchemy import func
    
    # 统计数据
    today = datetime.now().date()
    month_start = today.replace(day=1)
    
    today_count = AICallLog.query.filter(
        func.date(AICallLog.created_at) == today
    ).count() if hasattr(AICallLog, 'created_at') else 0
    
    month_count = AICallLog.query.filter(
        func.date(AICallLog.created_at) >= month_start
    ).count() if hasattr(AICallLog, 'created_at') else 0
    
    total_count = AICallLog.query.count()
    
    ai_stats = {
        'today_count': today_count,
        'month_count': month_count,
        'total_count': total_count
    }
    
    return render_template('ai/assistant.html', ai_stats=ai_stats)


# ============================================================
# P2: AI Inventory Analysis API
# ============================================================

@bp.route('/api/inventory_analysis', methods=['POST'])
@login_required
def api_inventory_analysis():
    """AI inventory analysis - stale materials / restock suggestions"""
    err = _check_ai_rate_limit()
    if err:
        return err

    ai = get_ai_service()

    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': 'Please select a project first'})

    # Build inventory context
    inventories = Inventory.query.filter_by(project_id=project_id).all()
    inventory_data = []
    for inv in inventories[:50]:
        mat = inv.material
        safety = getattr(inv, 'safety_stock', 0) or 0
        inventory_data.append({
            'material': mat.name if mat else '',
            'specification': mat.specification if mat else '',
            'quantity': float(inv.quantity or 0),
            'unit': mat.unit if mat else '',
            'category': mat.category.name if mat and mat.category else '',
            'safety_stock': float(safety),
        })

    if not inventory_data:
        return jsonify({'success': False, 'message': 'No inventory data found'})

    result, error = ai.analyze_inventory({'inventory': inventory_data})

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'data': result})


# ============================================================
# P2: AI Supplier Evaluation API
# ============================================================

@bp.route('/api/supplier_evaluation', methods=['POST'])
@login_required
def api_supplier_evaluation():
    """AI supplier evaluation"""
    err = _check_ai_rate_limit()
    if err:
        return err

    ai = get_ai_service()

    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': 'Please select a project first'})

    suppliers = Supplier.query.filter_by(project_id=project_id).limit(20).all()
    supplier_data = []
    for s in suppliers:
        supplier_data.append({
            'name': s.name[:4] + '***' if s.name and len(s.name) > 4 else (s.name or ''),
            'code': s.code or '',
        })

    if not supplier_data:
        return jsonify({'success': False, 'message': 'No supplier data found'})

    result, error = ai.evaluate_supplier({'suppliers': supplier_data})

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'data': result})


# ============================================================
# P2: AI Contract Review API
# ============================================================

@bp.route('/api/contract_review', methods=['POST'])
@login_required
def api_contract_review():
    """AI contract review"""
    err = _check_ai_rate_limit()
    if err:
        return err

    ai = get_ai_service()

    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': 'Please select a project first'})

    contracts = Contract.query.filter_by(project_id=project_id, is_deleted=False).limit(10).all()
    contract_data = []
    for c in contracts:
        paid = sum(p.amount or 0 for p in c.payments)
        contract_data.append({
            'code': c.code,
            'name': c.name,
            'amount_with_tax': float(c.amount_with_tax or 0),
            'paid_amount': float(paid),
            'status': c.status,
        })

    if not contract_data:
        return jsonify({'success': False, 'message': 'No contract data found'})

    result, error = ai.review_contract({'contracts': contract_data})

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'data': result})


# ============================================================
# P2: AI Approval Opinion Generation API
# ============================================================

@bp.route('/api/approval_opinion', methods=['POST'])
@login_required
def api_approval_opinion():
    """AI-assisted approval opinion generation"""
    err = _check_ai_rate_limit()
    if err:
        return err

    ai = get_ai_service()

    data = request.get_json() or {}
    biz_type = data.get('biz_type', '')
    action = data.get('action', 'approve')
    biz_data = data.get('biz_data', {})

    if not biz_type:
        return jsonify({'success': False, 'message': 'Missing biz_type'})

    context = {
        'biz_type': biz_type,
        'action': action,
        'biz_data': biz_data,
    }

    result, error = ai.call_with_scene(
        'text:approval_opinion',
        'Generate %s opinion for %s' % (action, biz_type),
        extra_context=context,
    )

    if error:
        return jsonify({'success': False, 'message': error})

    return jsonify({'success': True, 'data': result})


# ============================================================
# P2: AI Monthly Budget Status API
# ============================================================

@bp.route('/api/budget_status')
@login_required
def api_budget_status():
    """Check monthly AI budget usage"""
    try:
        from app.utils import get_config
        budget_enabled = str(get_config('ai_monthly_budget_enabled', 'false')).lower() == 'true'
        budget_amount = float(get_config('ai_monthly_budget_amount', '100'))

        from datetime import datetime
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        from sqlalchemy import func
        total_cost = db.session.query(
            func.sum(AICallLog.cost_amount)
        ).filter(
            AICallLog.created_at >= month_start
        ).scalar() or 0

        return jsonify({
            'success': True,
            'data': {
                'enabled': budget_enabled,
                'budget_amount': budget_amount,
                'used_amount': float(total_cost),
                'remaining': float(budget_amount) - float(total_cost),
                'usage_percent': round(float(total_cost) / budget_amount * 100, 1) if budget_amount > 0 else 0,
            }
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

