from flask import Blueprint

bp = Blueprint('work_number', __name__)

from app.work_number import routes
