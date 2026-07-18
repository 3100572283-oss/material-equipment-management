from flask import Blueprint

bp = Blueprint('ai', __name__, url_prefix='/ai')

from app.ai import routes
