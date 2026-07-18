from flask import Blueprint

bp = Blueprint('turnover_material', __name__)

from app.turnover_material import routes
