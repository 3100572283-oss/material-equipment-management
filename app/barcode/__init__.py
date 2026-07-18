from flask import Blueprint

bp = Blueprint('barcode', __name__)

from app.barcode import routes
