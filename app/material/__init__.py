from flask import Blueprint

bp = Blueprint('material', __name__)

from app.material import routes
