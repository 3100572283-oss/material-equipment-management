from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func

from app import db
from app.models import (
    PeriodClose, MovementSnapshot, StockIn, StockOut, StockInItem, StockOutItem,
    Inventory, Material, Reconciliation
)
from app.decorators import editor_required, log_audit
from app.period_close import bp


def _is_period_closed(project_id, period):
    """检查期间是否已结账"""
    pc = PeriodClose.query.filter_by(project_id=project_id, period=period, status='closed').first()
    return pc is not None


def _get_period_from_date(d):
    """从日期获取期间字符串 YYYY-MM"""
    if isinstance(d, datetime):
        return d.strftime('%Y-%m')
    if isinstance(d, date):
        return d.strftime('%Y-%m')
    return str(d)[:7]


def _check_period_close(project_id, period):
    """检查期间是否已结账，已结账则抛出异常"""
    if _is_period_closed(project_id, period):
        raise ValueError(f'{period} 期间已结账，不能进行此操作')


@bp.route('/')
@login_required
@editor_required
def index():
    from flask import session
    project_id = session.get('current_project_id')
    if not project_id:
        flash('请先选择项目。', 'warning')
        return redirect(url_for('main.index'))

    closes = PeriodClose.query.filter_by(project_id=project_id).order_by(PeriodClose.period.desc()).all()
    return render_template('admin/period_close.html', closes=closes)


@bp.route('/check')
@login_required
@editor_required
def check():
    """结账前校验"""
    from flask import session
    project_id = session.get('current_project_id')
    period = request.args.get('period', '', type=str)
    if not period:
        flash('请选择期间。', 'warning')
        return redirect(url_for('period_close.index'))

    issues = []

    # 1. 未审核的入库单
    unchecked = StockIn.query.filter(
        StockIn.project_id == project_id,
        func.strftime('%Y-%m', StockIn.stock_in_date) == period,
        StockIn.approval_status != 'passed'
    ).count()
    if unchecked > 0:
        issues.append(f'存在 {unchecked} 张未审核通过的入库单')

    # 2. 未对账的入库单
    unreconciled = StockIn.query.filter(
        StockIn.project_id == project_id,
        func.strftime('%Y-%m', StockIn.stock_in_date) == period,
        StockIn.is_reconciled == False
    ).count()
    if unreconciled > 0:
        issues.append(f'存在 {unreconciled} 张未对账的入库单')

    # 3. 未审批的出库单
    unapproved_out = StockOut.query.filter(
        StockOut.project_id == project_id,
        func.strftime('%Y-%m', StockOut.stock_out_date) == period,
        StockOut.approval_status != 'passed'
    ).count()
    if unapproved_out > 0:
        issues.append(f'存在 {unapproved_out} 张未审核通过的出库单')

    # 4. 库存为负的物资
    neg_stock = Inventory.query.filter(
        Inventory.project_id == project_id,
        Inventory.quantity < 0
    ).count()
    if neg_stock > 0:
        issues.append(f'存在 {neg_stock} 种库存为负的物资')

    can_close = len(issues) == 0

    return render_template('admin/period_close_check.html',
                           period=period, issues=issues, can_close=can_close)


@bp.route('/do_close', methods=['POST'])
@login_required
@editor_required
@log_audit(module='period_close', operation='期末结账')
def do_close():
    from flask import session
    project_id = session.get('current_project_id')
    period = request.form.get('period', '', type=str)
    if not period:
        flash('请选择期间。', 'warning')
        return redirect(url_for('period_close.index'))

    if _is_period_closed(project_id, period):
        flash(f'{period} 已结账，无需重复操作。', 'warning')
        return redirect(url_for('period_close.index'))

    # 生成快照数据
    try:
        _generate_snapshots(project_id, period)
    except Exception as e:
        flash(f'生成快照失败：{str(e)}', 'danger')
        return redirect(url_for('period_close.index'))

    # 记录结账
    pc = PeriodClose.query.filter_by(project_id=project_id, period=period).first()
    if not pc:
        pc = PeriodClose(project_id=project_id, period=period)
        db.session.add(pc)
    pc.status = 'closed'
    pc.closed_at = datetime.now()
    pc.closed_by = current_user.name or current_user.username
    db.session.commit()

    flash(f'{period} 期间结账成功。', 'success')
    return redirect(url_for('period_close.index'))


def _generate_snapshots(project_id, period):
    """生成物资动态月结快照"""
    # 删除旧快照
    MovementSnapshot.query.filter_by(project_id=project_id, period=period).delete()

    # 获取该项目所有物资
    materials = Material.query.filter_by(project_id=project_id).all()

    year, month = period.split('-')
    start_date = date(int(year), int(month), 1)
    if month == '12':
        end_date = date(int(year) + 1, 1, 1)
    else:
        end_date = date(int(year), int(month) + 1, 1)

    for mat in materials:
        # 期初（上月期末）
        prev_period = date(int(year), int(month), 1)
        from dateutil.relativedelta import relativedelta
        prev_date = start_date - relativedelta(months=1)
        prev_period_str = prev_date.strftime('%Y-%m')

        prev_snap = MovementSnapshot.query.filter_by(
            project_id=project_id, period=prev_period_str, material_id=mat.id
        ).first()

        begin_qty = float(prev_snap.end_qty) if prev_snap else 0
        begin_amount = float(prev_snap.end_amount) if prev_snap else 0

        # 本期入库
        in_total = db.session.query(
            func.coalesce(func.sum(StockInItem.quantity), 0),
            func.coalesce(func.sum(StockInItem.amount), 0)
        ).join(StockIn, StockInItem.stock_in_id == StockIn.id).filter(
            StockIn.project_id == project_id,
            StockInItem.material_id == mat.id,
            StockIn.stock_in_date >= start_date,
            StockIn.stock_in_date < end_date,
            StockIn.approval_status == 'passed'
        ).first()
        in_qty = float(in_total[0] or 0)
        in_amount = float(in_total[1] or 0)

        # 本期出库
        out_total = db.session.query(
            func.coalesce(func.sum(StockOutItem.quantity), 0),
            func.coalesce(func.sum(StockOutItem.amount), 0)
        ).join(StockOut, StockOutItem.stock_out_id == StockOut.id).filter(
            StockOut.project_id == project_id,
            StockOutItem.material_id == mat.id,
            StockOut.stock_out_date >= start_date,
            StockOut.stock_out_date < end_date,
            StockOut.approval_status == 'passed'
        ).first()
        out_qty = float(out_total[0] or 0)
        out_amount = float(out_total[1] or 0)

        end_qty = begin_qty + in_qty - out_qty
        end_amount = begin_amount + in_amount - out_amount

        snap = MovementSnapshot(
            project_id=project_id,
            period=period,
            material_id=mat.id,
            begin_qty=begin_qty,
            begin_amount=round(begin_amount, 2),
            in_qty=in_qty,
            in_amount=round(in_amount, 2),
            out_qty=out_qty,
            out_amount=round(out_amount, 2),
            end_qty=end_qty,
            end_amount=round(end_amount, 2)
        )
        db.session.add(snap)

    db.session.flush()


@bp.route('/reopen', methods=['POST'])
@login_required
@editor_required
@log_audit(module='period_close', operation='反结账')
def reopen():
    from flask import session
    project_id = session.get('current_project_id')
    period = request.form.get('period', '', type=str)
    reason = request.form.get('reason', '').strip()

    if not period:
        flash('请选择期间。', 'warning')
        return redirect(url_for('period_close.index'))

    pc = PeriodClose.query.filter_by(project_id=project_id, period=period, status='closed').first()
    if not pc:
        flash(f'{period} 未结账，无需反结账。', 'warning')
        return redirect(url_for('period_close.index'))

    # 删除快照
    MovementSnapshot.query.filter_by(project_id=project_id, period=period).delete()

    pc.status = 'open'
    pc.reopen_at = datetime.now()
    pc.reopen_by = current_user.name or current_user.username
    pc.reopen_reason = reason
    db.session.commit()

    flash(f'{period} 期间反结账成功。', 'success')
    return redirect(url_for('period_close.index'))
