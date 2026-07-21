from flask import render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func, extract, text
from datetime import datetime, timedelta
from app.main import bp
from app import db
from app.decorators import log_audit
from app.utils import apply_data_scope
from app.models import (Project, Material, Supplier, UsageUnit, Contract,
                        StockIn, StockInItem, StockOut, StockOutItem,
                        Category, Payment, PurchaseRequisition,
                        TurnoverMaterial, Equipment, EquipmentMaintenance,
                        MaterialTransfer, Reconciliation, SysAnnouncement, SysAnnouncementRead)


@bp.route('/')
@login_required
def index():
    return redirect(url_for('main.dashboard'))


@bp.route('/dashboard')
@login_required
def dashboard():
    project_id = session.get('current_project_id')
    is_all_projects_mode = (project_id is None)

    # 获取当前用户可见项目列表（用于汇总模式）
    if is_all_projects_mode:
        visible_projects = current_user.get_visible_projects()
        visible_project_ids = [p.id for p in visible_projects]
        if not visible_project_ids:
            return render_template('index.html', stats={}, is_all_projects_mode=True,
                                   visible_projects=[])

    # 1. 关键指标
    if is_all_projects_mode:
        material_count = Material.query.filter(Material.project_id.in_(visible_project_ids)).count()
        supplier_count = Supplier.query.filter(Supplier.project_id.in_(visible_project_ids)).count()
        usage_unit_count = UsageUnit.query.filter(UsageUnit.project_id.in_(visible_project_ids)).count()
        contract_count = Contract.query.filter(Contract.project_id.in_(visible_project_ids)).count()
    else:
        material_count = Material.query.filter_by(project_id=project_id).count()
        supplier_count = Supplier.query.filter_by(project_id=project_id).count()
        usage_unit_count = UsageUnit.query.filter_by(project_id=project_id).count()
        contract_count = Contract.query.filter_by(project_id=project_id).count()

    # 2. 金额统计
    if is_all_projects_mode:
        stock_in_total = db.session.query(func.coalesce(func.sum(StockIn.total_amount), 0)).filter(
            StockIn.project_id.in_(visible_project_ids)).scalar() or 0
        contract_total = db.session.query(func.coalesce(func.sum(Contract.amount_with_tax), 0)).filter(
            Contract.project_id.in_(visible_project_ids)).scalar() or 0
        payment_total = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.project_id.in_(visible_project_ids)).scalar() or 0
    else:
        stock_in_total = db.session.query(func.coalesce(func.sum(StockIn.total_amount), 0)).filter(
            StockIn.project_id == project_id).scalar() or 0
        contract_total = db.session.query(func.coalesce(func.sum(Contract.amount_with_tax), 0)).filter(
            Contract.project_id == project_id).scalar() or 0
        payment_total = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.project_id == project_id).scalar() or 0

    # 履约异常合同
    if is_all_projects_mode:
        warning_contracts = Contract.query.filter(
            Contract.project_id.in_(visible_project_ids),
            Contract.status.in_(['履约异常', '已终止'])
        ).count()
    else:
        warning_contracts = Contract.query.filter(
            Contract.project_id == project_id,
            Contract.status.in_(['履约异常', '已终止'])
        ).count()

    # 待审批采购申请
    if is_all_projects_mode:
        pending_pr_count = PurchaseRequisition.query.filter(
            PurchaseRequisition.project_id.in_(visible_project_ids),
            PurchaseRequisition.status.in_(['pending', 'approving'])
        ).count()
    else:
        pending_pr_count = PurchaseRequisition.query.filter(
            PurchaseRequisition.project_id == project_id,
            PurchaseRequisition.status.in_(['pending', 'approving'])
        ).count()

    from app.utils import ConfigCache, is_module_enabled
    enable_quality_check = is_module_enabled('module_quality_check')
    pending_quality_count = 0
    if enable_quality_check:
        if is_all_projects_mode:
            pending_quality_count = StockIn.query.filter(
                StockIn.project_id.in_(visible_project_ids),
                StockIn.quality_status == 'pending'
            ).count()
        else:
            pending_quality_count = StockIn.query.filter(
                StockIn.project_id == project_id,
                StockIn.quality_status == 'pending'
            ).count()

    # 供应商资质到期提醒
    today = datetime.now().date()
    warning_date = today + timedelta(days=30)
    expiring_suppliers = []
    if is_all_projects_mode:
        suppliers = Supplier.query.filter(Supplier.project_id.in_(visible_project_ids)).all()
    else:
        suppliers = Supplier.query.filter_by(project_id=project_id).all()
    for s in suppliers:
        if s.license_expire_date and s.license_expire_date < today:
            expiring_suppliers.append({'name': s.name, 'type': '营业执照', 'date': s.license_expire_date, 'status': 'expired'})
        elif s.license_expire_date and s.license_expire_date <= warning_date:
            expiring_suppliers.append({'name': s.name, 'type': '营业执照', 'date': s.license_expire_date, 'status': 'warning'})
        elif s.certificate_expire_date and s.certificate_expire_date < today:
            expiring_suppliers.append({'name': s.name, 'type': '资质证书', 'date': s.certificate_expire_date, 'status': 'expired'})
        elif s.certificate_expire_date and s.certificate_expire_date <= warning_date:
            expiring_suppliers.append({'name': s.name, 'type': '资质证书', 'date': s.certificate_expire_date, 'status': 'warning'})

    # 周转材统计
    if is_all_projects_mode:
        turnover_count = TurnoverMaterial.query.filter(TurnoverMaterial.project_id.in_(visible_project_ids)).count()
    else:
        turnover_count = TurnoverMaterial.query.filter_by(project_id=project_id).count()

    # 设备统计
    if is_all_projects_mode:
        equipment_count = Equipment.query.filter(Equipment.project_id.in_(visible_project_ids)).count()
        equipment_in_use = Equipment.query.filter(Equipment.project_id.in_(visible_project_ids), Equipment.status == 'in_use').count()
        equipment_repairing = Equipment.query.filter(Equipment.project_id.in_(visible_project_ids), Equipment.status == 'repairing').count()
    else:
        equipment_count = Equipment.query.filter_by(project_id=project_id).count()
        equipment_in_use = Equipment.query.filter_by(project_id=project_id, status='in_use').count()
        equipment_repairing = Equipment.query.filter_by(project_id=project_id, status='repairing').count()

    # 设备维保到期提醒
    expiring_maintenance = []
    if is_all_projects_mode:
        maint_records = EquipmentMaintenance.query.join(Equipment).filter(
            Equipment.project_id.in_(visible_project_ids),
            EquipmentMaintenance.next_maintain_date != None,
            EquipmentMaintenance.next_maintain_date <= warning_date
        ).order_by(EquipmentMaintenance.next_maintain_date.asc()).limit(5).all()
    else:
        maint_records = EquipmentMaintenance.query.join(Equipment).filter(
            Equipment.project_id == project_id,
            EquipmentMaintenance.next_maintain_date != None,
            EquipmentMaintenance.next_maintain_date <= warning_date
        ).order_by(EquipmentMaintenance.next_maintain_date.asc()).limit(5).all()
    for r in maint_records:
        status = 'expired' if r.next_maintain_date < today else 'warning'
        expiring_maintenance.append({
            'equipment_name': r.equipment.name if r.equipment else '-',
            'next_date': r.next_maintain_date,
            'status': status
        })

    # 在途调拨统计
    if is_all_projects_mode:
        in_transfer_count = MaterialTransfer.query.filter(
            MaterialTransfer.from_project_id.in_(visible_project_ids),
            MaterialTransfer.status == 'out_done'
        ).count()
        pending_transfer_count = MaterialTransfer.query.filter(
            MaterialTransfer.to_project_id.in_(visible_project_ids),
            MaterialTransfer.status == 'out_done'
        ).count()
    else:
        in_transfer_count = MaterialTransfer.query.filter(
            MaterialTransfer.from_project_id == project_id,
            MaterialTransfer.status == 'out_done'
        ).count()
        pending_transfer_count = MaterialTransfer.query.filter(
            MaterialTransfer.to_project_id == project_id,
            MaterialTransfer.status == 'out_done'
        ).count()

    # 汇总模式特有数据
    project_stats = []
    if is_all_projects_mode:
        # 各项目库存金额排行
        for p in visible_projects:
            p_stock_in = db.session.query(func.coalesce(func.sum(StockIn.total_amount), 0)).filter(
                StockIn.project_id == p.id).scalar() or 0
            project_stats.append({
                'id': p.id,
                'name': p.name,
                'stock_in_total': round(float(p_stock_in) / 10000, 2),
                'material_count': Material.query.filter_by(project_id=p.id).count(),
                'contract_count': Contract.query.filter_by(project_id=p.id).count(),
            })
        # 按库存金额降序排序
        project_stats.sort(key=lambda x: x['stock_in_total'], reverse=True)

    # 在建项目数、项目总数
    active_project_count = len([p for p in (visible_projects if is_all_projects_mode else []) if p.status == 'active'])
    total_project_count = len(visible_projects) if is_all_projects_mode else 0

    stats = {
        'material_count': material_count,
        'supplier_count': supplier_count,
        'usage_unit_count': usage_unit_count,
        'contract_count': contract_count,
        'stock_in_total': round(float(stock_in_total) / 10000, 2),
        'contract_total': round(float(contract_total) / 10000, 2),
        'payment_total': round(float(payment_total) / 10000, 2),
        'warning_contracts': warning_contracts,
        'pending_pr_count': pending_pr_count,
        'pending_quality_count': pending_quality_count,
        'enable_quality_check': enable_quality_check,
        'expiring_suppliers': expiring_suppliers,
        'turnover_count': turnover_count,
        'equipment_count': equipment_count,
        'equipment_in_use': equipment_in_use,
        'equipment_repairing': equipment_repairing,
        'expiring_maintenance': expiring_maintenance,
        'in_transfer_count': in_transfer_count,
        'pending_transfer_count': pending_transfer_count,
    }

    return render_template('index.html', stats=stats,
                           is_all_projects_mode=is_all_projects_mode,
                           visible_projects=visible_projects if is_all_projects_mode else [],
                           project_stats=project_stats,
                           active_project_count=active_project_count,
                           total_project_count=total_project_count)


@bp.route('/api/dashboard_data')
@login_required
def dashboard_data():
    """仪表盘图表数据API"""
    project_id = session.get('current_project_id')
    level1_id = request.args.get('level1', 0, type=int)
    if not project_id:
        return jsonify({
            'category_distribution': [],
            'monthly_trend': {'months': [], 'in_data': [], 'out_data': []},
            'top_consumption': []
        })

    # 1. 物资分类库存分布
    category_distribution = []
    if level1_id > 0:
        level2_categories = Category.query.filter_by(project_id=project_id, parent_id=level1_id, level=2).all()
        for l2 in level2_categories:
            l3_ids = [c.id for c in Category.query.filter_by(parent_id=l2.id).all()]
            all_ids = [l2.id] + l3_ids
            
            amount = db.session.query(
                func.coalesce(func.sum(StockInItem.quantity * StockInItem.unit_price), 0)
            ).select_from(Material).outerjoin(
                StockInItem, StockInItem.material_id == Material.id
            ).outerjoin(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(
                Material.project_id == project_id,
                Material.category_id.in_(all_ids)
            ).scalar()
            
            if amount and float(amount) > 0:
                category_distribution.append({
                    'name': l2.name,
                    'value': round(float(amount or 0), 2)
                })
    else:
        level1_categories = Category.query.filter_by(project_id=project_id, level=1).all()
        for l1 in level1_categories:
            l2_ids = [c.id for c in Category.query.filter_by(parent_id=l1.id).all()]
            all_ids = [l1.id] + l2_ids
            for cid in l2_ids:
                all_ids.extend([cc.id for cc in Category.query.filter_by(parent_id=cid).all()])
            
            amount = db.session.query(
                func.coalesce(func.sum(StockInItem.quantity * StockInItem.unit_price), 0)
            ).select_from(Material).outerjoin(
                StockInItem, StockInItem.material_id == Material.id
            ).outerjoin(
                StockIn, StockIn.id == StockInItem.stock_in_id
            ).filter(
                Material.project_id == project_id,
                Material.category_id.in_(all_ids)
            ).scalar()
            
            if amount and float(amount) > 0:
                category_distribution.append({
                    'name': l1.name,
                    'value': round(float(amount or 0), 2),
                    'level1_id': l1.id
                })

    # 2. 月度出入库趋势（最近6个月）
    now = datetime.now()
    months = []
    for i in range(5, -1, -1):
        d = now - timedelta(days=i * 30)
        months.append(d.strftime('%Y-%m'))

    monthly_in = {}
    monthly_out = {}
    for m in months:
        monthly_in[m] = 0
        monthly_out[m] = 0

    # 入库
    month_label = func.strftime('%Y-%m', StockIn.stock_in_date).label('month')
    in_data = db.session.query(
        month_label,
        func.coalesce(func.sum(StockIn.total_amount), 0).label('amount')
    ).filter(
        StockIn.project_id == project_id,
        StockIn.stock_in_date >= now - timedelta(days=180)
    ).group_by(month_label).all()

    for month_val, amount in in_data:
        if month_val in monthly_in:
            monthly_in[month_val] = round(float(amount or 0) / 10000, 2)

    # 出库
    out_month_label = func.strftime('%Y-%m', StockOut.stock_out_date).label('month')
    out_data = db.session.query(
        out_month_label,
        func.coalesce(func.sum(StockOut.total_amount), 0).label('amount')
    ).filter(
        StockOut.project_id == project_id,
        StockOut.stock_out_date >= now - timedelta(days=180)
    ).group_by(out_month_label).all()

    for month, amount in out_data:
        if month in monthly_out:
            monthly_out[month] = round(float(amount or 0) / 10000, 2)

    monthly_trend = {
        'months': months,
        'in_data': [monthly_in[m] for m in months],
        'out_data': [monthly_out[m] for m in months]
    }

    # 3. 消耗量Top10
    top_consumption = db.session.query(
        Material.name,
        func.coalesce(func.sum(StockOutItem.quantity), 0).label('total_qty')
    ).join(
        StockOutItem, StockOutItem.material_id == Material.id
    ).join(
        StockOut, StockOut.id == StockOutItem.stock_out_id
    ).filter(
        StockOut.project_id == project_id
    ).group_by(Material.id, Material.name).order_by(
        func.sum(StockOutItem.quantity).desc()
    ).limit(10).all()

    top_consumption = [
        {'name': name, 'value': round(float(total_qty or 0), 2)}
        for name, total_qty in top_consumption
    ]

    return jsonify({
        'category_distribution': category_distribution,
        'monthly_trend': monthly_trend,
        'top_consumption': top_consumption
    })


@bp.route('/set_project/<int:project_id>')
@login_required
@log_audit(module='main', operation='切换项目')
def set_project(project_id):
    project = Project.query.get_or_404(project_id)
    # 权限校验：用户必须可访问该项目
    if not current_user.can_access_project(project.id):
        flash('您没有权限访问该项目', 'error')
        return redirect(request.referrer or url_for('main.index'))
    session['current_project_id'] = project.id
    session['current_project_name'] = project.name
    flash(f'已切换到项目：{project.name}', 'info')
    return redirect(request.referrer or url_for('main.index'))


@bp.route('/set_all_projects')
@login_required
def set_all_projects():
    """切换到全部项目模式（仅全部数据权限用户可用）"""
    if not (current_user.get_data_scope() == 'all' or current_user.is_admin()):
        flash('您没有权限查看全部项目', 'error')
        return redirect(request.referrer or url_for('main.index'))
    session['current_project_id'] = None
    session['current_project_name'] = '全部项目'
    flash('已切换到全部项目模式', 'info')
    return redirect(request.referrer or url_for('main.index'))


@bp.route('/api/global_search')
@login_required
def global_search():
    q = request.args.get('q', '').strip()
    project_id = session.get('current_project_id')
    if len(q) < 1:
        return jsonify({})

    keyword = f'%{q}%'
    result = {}

    def _build_query(model_cls, filter_cond):
        query = model_cls.query
        if project_id:
            query = query.filter(model_cls.project_id == project_id)
        else:
            query = apply_data_scope(query, model_cls)
        return query.filter(filter_cond)

    # 物资
    mats = _build_query(Material,
        db.or_(Material.name.like(keyword), Material.code.like(keyword), Material.specification.like(keyword))
    ).limit(5).all()
    result['materials'] = [
        {'name': m.name, 'code': m.code, 'url': url_for('material.edit', id=m.id)}
        for m in mats
    ]

    # 供应商
    sups = _build_query(Supplier, Supplier.name.like(keyword)).limit(5).all()
    result['suppliers'] = [
        {'name': s.name, 'code': s.code or '', 'url': url_for('supplier.detail', id=s.id)}
        for s in sups
    ]

    # 项目（全局搜索）
    projs = Project.query.filter(
        Project.name.like(keyword)
    ).limit(5).all()
    result['projects'] = [
        {'name': p.name, 'code': '', 'url': url_for('project.edit', id=p.id)}
        for p in projs
    ]

    # 入库单
    sis = _build_query(StockIn, StockIn.code.like(keyword)).limit(5).all()
    result['stock_ins'] = [
        {'name': s.code, 'code': '', 'url': url_for('stock_in.detail', id=s.id)}
        for s in sis
    ]

    # 出库单
    sos = _build_query(StockOut, StockOut.code.like(keyword)).limit(5).all()
    result['stock_outs'] = [
        {'name': s.code, 'code': '', 'url': url_for('stock_out.detail', id=s.id)}
        for s in sos
    ]

    # 合同
    cons = _build_query(Contract,
        db.or_(Contract.code.like(keyword), Contract.name.like(keyword))
    ).limit(5).all()
    result['contracts'] = [
        {'name': c.name or c.code, 'code': c.code, 'url': url_for('contract.detail', id=c.id)}
        for c in cons
    ]

    # 对账单
    recs = _build_query(Reconciliation, Reconciliation.code.like(keyword)).limit(5).all()
    result['reconciliations'] = [
        {'name': r.code, 'code': '', 'url': url_for('reconciliation.detail', id=r.id)}
        for r in recs
    ]

    # 付款单
    pays = _build_query(Payment, Payment.payment_code.like(keyword)).limit(5).all()
    result['payments'] = [
        {'name': p.payment_code, 'code': '', 'url': url_for('contract.detail', id=p.contract_id) if p.contract_id else '#'}
        for p in pays
    ]

    return jsonify(result)


@bp.context_processor
def inject_announcements():
    """注入当前有效的公告和到期提醒数量到所有模板"""
    from flask_login import current_user
    if not current_user.is_authenticated:
        return {'active_announcements': [], 'unread_popup_announcements': [], 'expiry_alert_count': 0}

    now = datetime.now()
    query = SysAnnouncement.query.filter(
        SysAnnouncement.status == True,
        SysAnnouncement.publish_time <= now,
        db.or_(SysAnnouncement.expire_time == None, SysAnnouncement.expire_time >= now)
    ).order_by(SysAnnouncement.created_at.desc())

    all_active = query.limit(5).all()

    # 过滤可见范围
    import json
    user_role_id = str(current_user.role_id) if current_user.role_id else ''
    user_dept_id = str(current_user.dept_id) if current_user.dept_id else ''

    def is_visible(ann):
        if ann.visible_scope == 'all':
            return True
        elif ann.visible_scope == 'role' and ann.visible_roles:
            try:
                role_ids = json.loads(ann.visible_roles)
                return user_role_id in role_ids
            except:
                return False
        elif ann.visible_scope == 'dept' and ann.visible_depts:
            try:
                dept_ids = json.loads(ann.visible_depts)
                return user_dept_id in dept_ids
            except:
                return False
        return True

    all_active = [a for a in all_active if is_visible(a)]

    # 未读的弹窗公告
    read_ids = [r.announcement_id for r in SysAnnouncementRead.query.filter_by(user_id=current_user.id).all()]
    unread_popups = [a for a in all_active if a.is_popup and a.id not in read_ids]

    # 序列化为可 JSON 化的字典列表
    def serialize_announcement(ann):
        return {
            'id': ann.id,
            'title': ann.title,
            'content': ann.content,
            'type': ann.type,
            'is_popup': ann.is_popup,
            'publish_time': ann.publish_time.strftime('%Y-%m-%dT%H:%M:%S') if ann.publish_time else None,
            'expire_time': ann.expire_time.strftime('%Y-%m-%dT%H:%M:%S') if ann.expire_time else None,
        }

    serialized_active = [serialize_announcement(a) for a in all_active]
    serialized_unread = [serialize_announcement(a) for a in unread_popups]

    # 到期提醒数量（当前项目）
    expiry_count = 0
    project_id = session.get('current_project_id')
    if project_id:
        today = datetime.now().date()
        warning_date = today + timedelta(days=30)

        # 供应商资质到期
        sup_count = Supplier.query.filter(
            Supplier.project_id == project_id,
            db.or_(
                db.and_(Supplier.license_expire_date != None, Supplier.license_expire_date <= warning_date),
                db.and_(Supplier.certificate_expire_date != None, Supplier.certificate_expire_date <= warning_date)
            )
        ).count()
        expiry_count += sup_count

        # 设备维保到期
        maint_count = EquipmentMaintenance.query.join(Equipment).filter(
            Equipment.project_id == project_id,
            EquipmentMaintenance.next_maintain_date != None,
            EquipmentMaintenance.next_maintain_date <= warning_date
        ).count()
        expiry_count += maint_count

    return {
        'active_announcements': serialized_active,
        'unread_popup_announcements': serialized_unread,
        'expiry_alert_count': expiry_count
    }


@bp.route('/api/announcement/<int:id>/read', methods=['POST'])
@login_required
def mark_announcement_read(id):
    """标记公告为已读"""
    from flask_login import current_user
    existing = SysAnnouncementRead.query.filter_by(
        announcement_id=id, user_id=current_user.id
    ).first()
    if not existing:
        db.session.add(SysAnnouncementRead(announcement_id=id, user_id=current_user.id))
        db.session.commit()
    return jsonify({'success': True})


def _needs_init():
    """判断是否需要初始化向导（系统中没有任何用户）"""
    try:
        from app.models import User
        return User.query.count() == 0
    except Exception:
        return False


@bp.route('/wizard')
def wizard():
    """初始化向导"""
    from app.models import User
    # 如果已经有用户了，不允许再走向导
    if User.query.count() > 0:
        return redirect(url_for('auth.login'))

    step = request.args.get('step', '1', type=str)
    if step not in ('1', '2', '3', '4', '5'):
        step = '1'
    return render_template('wizard/step_' + step + '.html', step=step)


@bp.route('/wizard/submit', methods=['POST'])
def wizard_submit():
    """向导提交处理"""
    from app.models import User, SysRole, SysDept, Project
    from werkzeug.security import generate_password_hash

    # 已经有用户了，不允许
    if User.query.count() > 0:
        return redirect(url_for('auth.login'))

    action = request.form.get('action', '')

    # Step 1: 欢迎 -> 跳转到 Step 2
    if action == 'start':
        return redirect(url_for('main.wizard', step='2'))

    # Step 2: 创建管理员账号
    if action == 'admin':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        name = request.form.get('name', '').strip()

        errors = []
        if not username:
            errors.append('请输入用户名')
        elif len(username) > 64:
            errors.append('用户名不能超过64个字符')
        if not password:
            errors.append('请输入密码')
        elif len(password) < 6:
            errors.append('密码长度不能少于6位')
        if password != confirm:
            errors.append('两次输入的密码不一致')
        if not name:
            errors.append('请输入姓名')

        if errors:
            return render_template('wizard/step_2.html', step='2', errors=errors,
                                   username=username, name=name)

        # 暂存在session中，到最后一步一起创建
        session['wizard_admin'] = {
            'username': username,
            'password': password,
            'name': name,
        }
        return redirect(url_for('main.wizard', step='3'))

    # Step 3: 创建部门
    if action == 'dept':
        dept_name = request.form.get('dept_name', '').strip()
        dept_code = request.form.get('dept_code', '').strip()
        skip = request.form.get('skip', '')

        if not skip and (not dept_name or not dept_code):
            errors = ['请填写部门信息，或选择跳过']
            return render_template('wizard/step_3.html', step='3', errors=errors,
                                   dept_name=dept_name, dept_code=dept_code)

        if skip:
            session['wizard_dept'] = None
        else:
            session['wizard_dept'] = {
                'dept_name': dept_name,
                'dept_code': dept_code,
            }
        return redirect(url_for('main.wizard', step='4'))

    # Step 4: 创建项目
    if action == 'project':
        project_name = request.form.get('project_name', '').strip()
        project_code = request.form.get('project_code', '').strip()
        skip = request.form.get('skip', '')

        if not skip and (not project_name or not project_code):
            errors = ['请填写项目信息，或选择跳过']
            return render_template('wizard/step_4.html', step='4', errors=errors,
                                   project_name=project_name, project_code=project_code)

        if skip:
            session['wizard_project'] = None
        else:
            session['wizard_project'] = {
                'project_name': project_name,
                'project_code': project_code,
            }
        return redirect(url_for('main.wizard', step='5'))

    # Step 5: 确认并完成
    if action == 'finish':
        admin_data = session.get('wizard_admin')
        dept_data = session.get('wizard_dept')
        project_data = session.get('wizard_project')

        if not admin_data:
            return redirect(url_for('main.wizard', step='2'))

        try:
            # 1. 确保有超级管理员角色
            super_role = SysRole.query.filter_by(role_code='super_admin').first()
            if not super_role:
                super_role = SysRole(
                    role_code='super_admin',
                    role_name='超级管理员',
                    description='拥有系统所有权限',
                    data_scope='all',
                    status=True,
                )
                db.session.add(super_role)
                db.session.flush()

            # 2. 创建部门（如果有）
            dept_id = None
            if dept_data:
                dept = SysDept(
                    dept_code=dept_data['dept_code'],
                    dept_name=dept_data['dept_name'],
                    status=True,
                    sort=1,
                )
                db.session.add(dept)
                db.session.flush()
                dept_id = dept.id

            # 3. 创建项目（如果有）
            project_id = None
            if project_data:
                proj = Project(
                    code=project_data['project_code'],
                    name=project_data['project_name'],
                    status='active',
                    created_at=datetime.now(),
                )
                db.session.add(proj)
                db.session.flush()
                project_id = proj.id

            # 4. 创建超级管理员
            admin = User(
                username=admin_data['username'],
                password_hash=generate_password_hash(admin_data['password'], method='pbkdf2:sha256'),
                role='admin',
                role_id=super_role.id,
                dept_id=dept_id,
                name=admin_data['name'],
            )
            db.session.add(admin)
            db.session.commit()

            # 清除session中的向导数据
            for k in ('wizard_admin', 'wizard_dept', 'wizard_project'):
                session.pop(k, None)
            session['wizard_done'] = True

            return redirect(url_for('main.wizard_done'))

        except Exception as e:
            db.session.rollback()
            errors = ['初始化失败：' + str(e)]
            return render_template('wizard/step_5.html', step='5', errors=errors)

    return redirect(url_for('main.wizard'))


@bp.route('/wizard/done')
def wizard_done():
    """向导完成页"""
    if not session.get('wizard_done'):
        from app.models import User
        if User.query.count() == 0:
            return redirect(url_for('main.wizard'))
        return redirect(url_for('auth.login'))
    session.pop('wizard_done', None)
    return render_template('wizard/done.html')
