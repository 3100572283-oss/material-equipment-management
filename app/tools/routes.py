from flask import render_template
from flask_login import login_required
from app.tools import bp


@bp.route('/calculators')
@login_required
def calculators():
    """工程计算器页面"""
    return render_template('tools/calculators.html')
