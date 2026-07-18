from flask import Blueprint

bp = Blueprint('stock_out', __name__)

from app.stock_out import routes
