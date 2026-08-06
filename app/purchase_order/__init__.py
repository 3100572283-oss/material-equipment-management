from flask import Blueprint

bp = Blueprint('purchase_order', __name__)

from app.purchase_order import routes
