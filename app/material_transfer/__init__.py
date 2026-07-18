from flask import Blueprint

bp = Blueprint('material_transfer', __name__, url_prefix='/material_transfer')

from app.material_transfer import routes