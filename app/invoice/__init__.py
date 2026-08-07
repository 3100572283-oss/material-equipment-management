# M5 票据三流合一 蓝图
from flask import Blueprint

invoice_bp = Blueprint('invoice', __name__, template_folder='../templates')

from . import models  # noqa: E402,F401
from . import routes  # noqa: E402,F401
