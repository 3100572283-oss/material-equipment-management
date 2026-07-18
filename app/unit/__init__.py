from flask import Blueprint

bp = Blueprint('unit', __name__)

from app.unit import routes
