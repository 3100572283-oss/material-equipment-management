from flask import Blueprint

bp = Blueprint('batch', __name__)

from app.batch import routes
