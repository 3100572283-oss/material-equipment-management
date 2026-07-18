from flask import Blueprint

bp = Blueprint('equipment', __name__)

from app.equipment import routes
