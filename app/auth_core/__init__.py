# app/auth_core/__init__.py
from flask import Blueprint

bp = Blueprint('auth_core', __name__, template_folder='templates')

# 确保模型注册到 db.metadata（供 db.create_all 建表）
from app.auth_core import models  # noqa: E402,F401
from app.auth_core import routes  # noqa: E402,F401
