from flask import Blueprint

bp = Blueprint('price_formula', __name__)

from app.price_formula import routes
