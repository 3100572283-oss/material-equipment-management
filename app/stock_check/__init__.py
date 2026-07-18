from flask import Blueprint

bp = Blueprint('stock_check', __name__)

from app.stock_check import routes
