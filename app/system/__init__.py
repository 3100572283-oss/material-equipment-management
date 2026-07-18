from flask import Blueprint

bp = Blueprint('system', __name__, template_folder='templates')

from app.system import rbac_routes
