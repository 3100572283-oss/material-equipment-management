from flask import Blueprint

bp = Blueprint('stock_in', __name__)

from app.stock_in import routes
