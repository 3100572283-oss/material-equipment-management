from flask import Blueprint

bp = Blueprint('material_return', __name__)

from app.material_return import routes
