from flask import render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from app.steel import bp
from app import db
from app.models import SteelSpecWeight


# 规格类型映射
SPEC_TYPE_MAP = {
    'rebar': '钢筋',
    'plate': '钢板',
    'angle': '等边角钢',
    'pipe': '钢管',
    'channel': '槽钢',
    'ibeam': '工字钢',
    'hbeam': 'H型钢',
    'flat': '扁钢',
    'square': '方钢',
    'angle_unequal': '不等边角钢',
    'round': '圆钢',
    'square_tube': '方管/矩形管',
    'building': '建材体积重量',
}


def _to_float(value, default=0):
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@bp.route('/')
@login_required
def index():
    """钢材换算工具页面"""
    specs = SteelSpecWeight.query.order_by(
        SteelSpecWeight.spec_type,
        SteelSpecWeight.id
    ).all()
    specs_data = [
        {
            'id': s.id,
            'spec_type': s.spec_type,
            'spec_name': s.spec_name,
            'theoretical_weight': float(s.theoretical_weight or 0),
            'unit': s.unit,
            'remark': s.remark or '',
        }
        for s in specs
    ]
    return render_template(
        'steel/calculator.html',
        specs=specs_data,
        spec_type_map=SPEC_TYPE_MAP,
    )


@bp.route('/api/calculate', methods=['POST'])
@login_required
def api_calculate():
    """换算API"""
    try:
        spec_type = request.form.get('spec_type', '').strip()
        spec_name = request.form.get('spec_name', '').strip()
        count = request.form.get('count', '1')
        length = request.form.get('length', '0')
        width = request.form.get('width', '0')
        volume = request.form.get('volume', '0')

        if not spec_type or not spec_name:
            return jsonify({'success': False, 'message': '请选择规格类型和规格名称'})

        spec = SteelSpecWeight.query.filter_by(
            spec_type=spec_type, spec_name=spec_name
        ).first()
        if not spec:
            return jsonify({'success': False, 'message': f'未找到规格 {spec_name}'})

        theoretical_weight = float(spec.theoretical_weight or 0)
        detail = {
            'spec_type': SPEC_TYPE_MAP.get(spec_type, spec_type),
            'spec_name': spec.spec_name,
            'theoretical_weight': theoretical_weight,
            'unit': spec.unit,
        }

        if spec_type == 'building':
            # 建材体积重量换算：密度(t/m³) × 体积(m³) = 重量(吨)
            volume_m3 = _to_float(volume)
            if volume_m3 <= 0:
                return jsonify({'success': False, 'message': '请输入有效体积'})
            weight_t = round(theoretical_weight * volume_m3, 4)
            weight_kg = round(weight_t * 1000, 2)
            detail.update({
                'volume': volume_m3,
                'density': theoretical_weight,
                'formula': f'{theoretical_weight} t/m³ × {volume_m3} m³',
            })
        elif spec_type == 'plate':
            # 钢板：理论重量(kg/㎡) × 长度(m) × 宽度(m) = 总重量(kg)
            length_m = _to_float(length)
            width_m = _to_float(width)
            if length_m <= 0 or width_m <= 0:
                return jsonify({'success': False, 'message': '钢板需输入有效长度和宽度'})
            weight = theoretical_weight * length_m * width_m
            weight_kg = round(weight, 2)
            weight_t = round(weight / 1000, 4)
            detail.update({
                'length': length_m,
                'width': width_m,
                'formula': f'{theoretical_weight} kg/㎡ × {length_m} m × {width_m} m',
            })
        else:
            # 钢筋/钢管/角钢/型钢：理论重量(kg/m) × 长度(m) × 根数 = 总重量(kg)
            length_m = _to_float(length)
            count_n = int(_to_float(count, 1))
            if count_n <= 0:
                count_n = 1
            if length_m <= 0:
                return jsonify({'success': False, 'message': '请输入有效长度'})
            weight = theoretical_weight * length_m * count_n
            weight_kg = round(weight, 2)
            weight_t = round(weight / 1000, 4)
            detail.update({
                'length': length_m,
                'count': count_n,
                'formula': f'{theoretical_weight} kg/m × {length_m} m × {count_n} 根',
            })

        return jsonify({
            'success': True,
            'weight': weight_kg,
            'weight_t': weight_t,
            'detail': detail,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'计算失败：{str(e)}'})


@bp.route('/specs')
@login_required
def specs():
    """规格表管理页面"""
    spec_type = request.args.get('spec_type', '', type=str)
    query = SteelSpecWeight.query
    if spec_type:
        query = query.filter_by(spec_type=spec_type)
    specs_list = query.order_by(
        SteelSpecWeight.spec_type,
        SteelSpecWeight.id
    ).all()
    return render_template(
        'steel/specs.html',
        specs=specs_list,
        spec_type_map=SPEC_TYPE_MAP,
        current_spec_type=spec_type,
    )


@bp.route('/specs/add', methods=['POST'])
@login_required
def specs_add():
    """添加规格"""
    if not current_user.can_edit():
        flash('无权限操作', 'danger')
        return redirect(url_for('steel.specs'))

    spec_type = request.form.get('spec_type', '').strip()
    spec_name = request.form.get('spec_name', '').strip()
    theoretical_weight = request.form.get('theoretical_weight', '').strip()
    unit = request.form.get('unit', '').strip()
    remark = request.form.get('remark', '').strip()

    if not spec_type or not spec_name or not theoretical_weight:
        flash('规格类型、规格名称、理论重量为必填项', 'danger')
        return redirect(url_for('steel.specs'))

    if spec_type not in SPEC_TYPE_MAP:
        flash('规格类型无效', 'danger')
        return redirect(url_for('steel.specs'))

    # 校验重复
    existing = SteelSpecWeight.query.filter_by(
        spec_type=spec_type, spec_name=spec_name
    ).first()
    if existing:
        flash(f'规格 {spec_name} 已存在', 'danger')
        return redirect(url_for('steel.specs'))

    try:
        weight_val = float(theoretical_weight)
    except (TypeError, ValueError):
        flash('理论重量必须为数字', 'danger')
        return redirect(url_for('steel.specs'))

    if not unit:
        if spec_type == 'plate':
            unit = 'kg/㎡'
        elif spec_type == 'building':
            unit = 't/m³'
        else:
            unit = 'kg/m'

    s = SteelSpecWeight(
        spec_type=spec_type,
        spec_name=spec_name,
        theoretical_weight=weight_val,
        unit=unit,
        remark=remark,
    )
    db.session.add(s)
    db.session.commit()
    flash(f'规格 {spec_name} 添加成功', 'success')
    return redirect(url_for('steel.specs', spec_type=spec_type))


@bp.route('/specs/<int:id>/delete', methods=['POST'])
@login_required
def specs_delete(id):
    """删除规格"""
    if not current_user.can_edit():
        flash('无权限操作', 'danger')
        return redirect(url_for('steel.specs'))

    s = SteelSpecWeight.query.get_or_404(id)
    spec_type = s.spec_type
    spec_name = s.spec_name
    db.session.delete(s)
    db.session.commit()
    flash(f'规格 {spec_name} 已删除', 'success')
    return redirect(url_for('steel.specs', spec_type=spec_type))


@bp.route('/api/specs')
@login_required
def api_specs():
    """获取规格列表JSON，支持按spec_type过滤"""
    spec_type = request.args.get('spec_type', '', type=str)
    query = SteelSpecWeight.query
    if spec_type:
        query = query.filter_by(spec_type=spec_type)
    specs_list = query.order_by(
        SteelSpecWeight.spec_type,
        SteelSpecWeight.id
    ).all()
    return jsonify({
        'success': True,
        'data': [
            {
                'id': s.id,
                'spec_type': s.spec_type,
                'spec_type_label': SPEC_TYPE_MAP.get(s.spec_type, s.spec_type),
                'spec_name': s.spec_name,
                'theoretical_weight': float(s.theoretical_weight or 0),
                'unit': s.unit,
                'remark': s.remark or '',
            }
            for s in specs_list
        ]
    })
