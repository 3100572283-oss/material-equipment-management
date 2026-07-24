"""消息中心接口"""
from flask import request, jsonify, render_template, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import or_
from datetime import datetime

from app.message import bp
from app import db
from app.models import Message


@bp.route('/count')
@login_required
def count():
    """获取当前用户未读消息数"""
    unread = Message.query.filter_by(
        user_id=current_user.id, is_read=False
    ).count()
    return jsonify({'success': True, 'count': unread})


@bp.route('/list')
@login_required
def list_messages():
    """获取消息列表（支持按类型筛选和分页）"""
    msg_type = request.args.get('type', '', type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = Message.query.filter_by(user_id=current_user.id)
    if msg_type:
        query = query.filter_by(msg_type=msg_type)
    query = query.order_by(Message.created_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    messages = pagination.items

    msg_type_map = {
        'approval': '审批通知',
        'system': '系统通知',
        'warning': '预警通知',
        'info': '系统通知',
    }

    return jsonify({
        'success': True,
        'messages': [{
            'id': m.id,
            'title': m.title,
            'content': m.content or '',
            'type': m.msg_type,
            'type_label': msg_type_map.get(m.msg_type, m.msg_type),
            'biz_type': m.biz_type,
            'biz_id': m.biz_id,
            'url': m.url,
            'is_read': m.is_read,
            'created_at': m.created_at.strftime('%Y-%m-%d %H:%M') if m.created_at else '',
        } for m in messages],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page,
        'has_next': pagination.has_next,
    })


@bp.route('/read/<int:id>', methods=['PUT', 'POST'])
@login_required
def read_one(id):
    """标记单条消息已读"""
    msg = Message.query.get_or_404(id)
    if msg.user_id != current_user.id:
        return jsonify({'success': False, 'message': '无权操作'}), 403
    if not msg.is_read:
        msg.is_read = True
        msg.read_at = datetime.now()
        db.session.commit()
    return jsonify({'success': True})


@bp.route('/read-all', methods=['PUT', 'POST'])
@login_required
def read_all():
    """全部标记已读"""
    unread_msgs = Message.query.filter_by(
        user_id=current_user.id, is_read=False
    ).all()
    now = datetime.now()
    for m in unread_msgs:
        m.is_read = True
        m.read_at = now
    db.session.commit()
    return jsonify({'success': True, 'count': len(unread_msgs)})


@bp.route('/')
@login_required
def index():
    """消息中心页面"""
    return render_template('message/index.html')
