# -*- coding: utf-8 -*-
"""独立数据可视化大屏蓝图（项目层第三终端）。"""
from flask import Blueprint

bigscreen_bp = Blueprint('bigscreen', __name__, template_folder='../templates')

from app.bigscreen import routes  # noqa: E402,F401
