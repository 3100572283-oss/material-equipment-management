# app/subcontractor/__init__.py
"""M6 分包商核验蓝图。

与既有 app/subcontract（分包扣款）区分命名，避免蓝图/端点冲突。
"""
from flask import Blueprint

subcontractor_bp = Blueprint('subcontractor', __name__, url_prefix='/subcontractor')

# 导入模型，使 db.create_all / Flask-Migrate 能识别 subcontractor_* 表
from . import models  # noqa: E402,F401
# 导入路由（必须在 subcontractor_bp 定义之后）
from . import routes  # noqa: E402,F401


@subcontractor_bp.route('/health')
def health():
    """M6 模块健康探针"""
    return {'module': 'subcontractor', 'status': 'ok'}
