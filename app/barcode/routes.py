from flask import (render_template, request, redirect, url_for, flash,
                   jsonify, session, send_file)
from flask_login import login_required, current_user
from io import BytesIO
from app import db
from app.models import BarcodeLabel, Material, Category, Project

# 尝试导入二维码 / 条形码库；若未安装则降级为纯文本显示
try:
    import qrcode
    _HAS_QRCODE = True
except ImportError:
    qrcode = None
    _HAS_QRCODE = False

try:
    import barcode
    from barcode.writer import ImageWriter
    _HAS_BARCODE = True
except ImportError:
    barcode = None
    ImageWriter = None
    _HAS_BARCODE = False

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    Image = None
    _HAS_PIL = False

from app.barcode import bp


def _libs_available():
    """检查生成图片所需的库是否齐全"""
    return _HAS_QRCODE and _HAS_PIL


def _barcode_lib_available():
    """检查生成条形码所需的库是否齐全"""
    return _HAS_BARCODE and _HAS_PIL


@bp.route('/')
@login_required
def index():
    """标签打印主页"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    materials = Material.query.filter_by(project_id=project_id) \
        .order_by(Material.code.asc(), Material.name.asc()).all()
    categories = Category.query.filter_by(project_id=project_id) \
        .order_by(Category.sort_order.asc(), Category.name.asc()).all()

    return render_template('barcode/index.html',
                           materials=materials,
                           categories=categories,
                           libs_available=_libs_available(),
                           barcode_available=_barcode_lib_available())


@bp.route('/generate', methods=['POST'])
@login_required
def generate():
    """生成标签：为每个物资创建 BarcodeLabel 记录并返回标签信息（含图片数据）"""
    project_id = session.get('current_project_id')
    if not project_id:
        return jsonify({'success': False, 'message': '请先选择项目。'}), 400

    material_ids = request.form.getlist('material_ids')
    if not material_ids:
        # 兼容 JSON 请求
        data = request.get_json(silent=True) or {}
        material_ids = data.get('material_ids') or []
    label_size = request.form.get('label_size', '40x30')
    quantity = request.form.get('quantity', '1', type=int) or 1
    label_type = request.form.get('label_type', 'qrcode')  # qrcode / barcode

    if not material_ids:
        return jsonify({'success': False, 'message': '请至少选择一个物资。'}), 400

    labels = []
    for mid in material_ids:
        try:
            material_id = int(mid)
        except (TypeError, ValueError):
            continue
        material = Material.query.filter_by(id=material_id, project_id=project_id).first()
        if not material:
            continue

        label = BarcodeLabel(
            project_id=project_id,
            material_id=material.id,
            material_name=material.name,
            material_code=material.code or '',
            spec=material.specification or '',
            unit=material.unit or '',
            label_size=label_size,
            quantity=quantity,
            created_by=current_user.id,
        )
        db.session.add(label)

        # 构造图片URL（前端按需加载）
        if label_type == 'barcode' and _barcode_lib_available():
            img_url = url_for('barcode.api_barcode', material_id=material.id,
                              _external=False)
        else:
            img_url = url_for('barcode.api_qrcode', material_id=material.id,
                              _external=False)

        labels.append({
            'label_id': None,  # 提交后才有 id
            'material_id': material.id,
            'material_name': material.name,
            'material_code': material.code or '',
            'spec': material.specification or '',
            'unit': material.unit or '',
            'label_size': label_size,
            'quantity': quantity,
            'label_type': label_type,
            'image_url': img_url,
        })

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'保存失败：{e}'}), 500

    return jsonify({
        'success': True,
        'message': f'已生成 {len(labels)} 条标签记录',
        'labels': labels,
        'libs_available': _libs_available(),
        'barcode_available': _barcode_lib_available(),
    })


@bp.route('/preview')
@login_required
def preview():
    """预览标签（页面，可打印）"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    material_ids = request.args.getlist('material_id')
    if not material_ids:
        # 兼容逗号分隔
        raw = request.args.get('material_ids', '', type=str)
        if raw:
            material_ids = [x for x in raw.split(',') if x.strip()]
    label_size = request.args.get('label_size', '40x30')
    quantity = request.args.get('quantity', '1', type=int) or 1
    label_type = request.args.get('label_type', 'qrcode')

    materials = []
    for mid in material_ids:
        try:
            material_id = int(mid)
        except (TypeError, ValueError):
            continue
        m = Material.query.filter_by(id=material_id, project_id=project_id).first()
        if m:
            materials.append(m)

    return render_template('barcode/preview.html',
                           materials=materials,
                           label_size=label_size,
                           quantity=quantity,
                           label_type=label_type,
                           libs_available=_libs_available(),
                           barcode_available=_barcode_lib_available())


@bp.route('/api/qrcode/<int:material_id>')
@login_required
def api_qrcode(material_id):
    """生成物资编码的二维码 PNG 图片"""
    if not _libs_available():
        # 库未安装：返回纯文本提示图（用 PIL 绘制），不可用时返回 400
        if _HAS_PIL:
            return _render_text_image('请安装 qrcode 库')
        return jsonify({'error': '请安装 qrcode python-barcode Pillow 库'}), 500

    project_id = session.get('current_project_id')
    material = Material.query.filter_by(id=material_id, project_id=project_id).first_or_404()
    content = material.code or str(material.id)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=1,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')

    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png',
                     download_name=f'qrcode_{material.id}.png')


@bp.route('/api/barcode/<int:material_id>')
@login_required
def api_barcode(material_id):
    """生成物资编码的条形码 PNG 图片（CODE128）"""
    if not _barcode_lib_available():
        if _HAS_PIL:
            return _render_text_image('请安装 python-barcode 库')
        return jsonify({'error': '请安装 qrcode python-barcode Pillow 库'}), 500

    project_id = session.get('current_project_id')
    material = Material.query.filter_by(id=material_id, project_id=project_id).first_or_404()
    content = material.code or str(material.id)
    # CODE128 仅支持 ASCII，且不能为空
    if not content or not content.isascii():
        content = str(material.id)

    code128 = barcode.get('code128', content, writer=ImageWriter())
    buf = BytesIO()
    code128.write(buf, options={
        'module_width': 0.2,
        'module_height': 10.0,
        'quiet_zone': 1.0,
        'write_text': True,
        'font_size': 8,
    })
    buf.seek(0)
    return send_file(buf, mimetype='image/png',
                     download_name=f'barcode_{material.id}.png')


@bp.route('/history')
@login_required
def history():
    """打印历史"""
    project_id = session.get('current_project_id')
    query = BarcodeLabel.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    labels = query.order_by(BarcodeLabel.created_at.desc()).limit(500).all()
    return render_template('barcode/history.html', labels=labels)


@bp.route('/batch_print')
@login_required
def batch_print():
    """批量打印"""
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    category_id = request.args.get('category_id', 0, type=int)

    categories = Category.query.filter_by(project_id=project_id) \
        .order_by(Category.sort_order.asc(), Category.name.asc()).all()

    materials_query = Material.query.filter_by(project_id=project_id)
    if category_id:
        materials_query = materials_query.filter_by(category_id=category_id)
    materials = materials_query.order_by(Material.code.asc(), Material.name.asc()).all()

    return render_template('barcode/batch_print.html',
                           categories=categories,
                           materials=materials,
                           category_id=category_id,
                           libs_available=_libs_available(),
                           barcode_available=_barcode_lib_available())


def _render_text_image(text):
    """当依赖库未安装时，返回纯文本提示 PNG 图片"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGB', (300, 100), color='white')
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.text((10, 40), text, fill='black', font=font)
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name='notice.png')
