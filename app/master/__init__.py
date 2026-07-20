from flask import Blueprint

# 公司级主数据管理蓝图：物资主库、供应商主库、物资分类
master_bp = Blueprint('master', __name__, url_prefix='/master')

from app.master import routes  # noqa: E402,F401
