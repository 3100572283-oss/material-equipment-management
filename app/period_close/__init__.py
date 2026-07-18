from flask import Blueprint

bp = Blueprint('period_close', __name__)

from app.period_close import routes
