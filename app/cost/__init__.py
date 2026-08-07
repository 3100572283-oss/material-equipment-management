# app/cost/__init__.py
"""M4 成本核算蓝图（P0 骨架：仅导入模型，路由在 P1/P2/P3 落地）。

注册时机：P1 实现责任成本测算路由后，在 app/__init__.py 的蓝图注册区
`from app.cost import cost_bp` + `app.register_blueprint(cost_bp)` 即可启用。
本骨架不注册即不暴露任何端点，保证生产稳定；模型随蓝图 import 被 db.create_all 识别。
"""
from flask import Blueprint

cost_bp = Blueprint('cost', __name__, url_prefix='/cost')

# 导入模型，使 db.create_all / Flask-Migrate 能识别 cost_* 表
from . import models  # noqa: E402,F401
# 导入路由（P1：责任成本测算 + P2/P3 占位）。必须在 cost_bp 定义之后。
from . import routes  # noqa: E402,F401


@cost_bp.route('/health')
def health():
    """M4 模块健康探针（部署后可用于探活，不影响业务）"""
    return {'module': 'cost', 'status': 'ok'}
