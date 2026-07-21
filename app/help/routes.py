from flask import render_template
from app.help import bp


@bp.route('/')
def index():
    """帮助中心首页"""
    return render_template('help/index.html')


@bp.route('/faq')
def faq():
    """常见问题"""
    return render_template('help/faq.html')


@bp.route('/guide')
def guide():
    """使用指南"""
    return render_template('help/guide.html')


@bp.route('/manual')
def manual():
    """操作手册"""
    return render_template('help/manual.html')