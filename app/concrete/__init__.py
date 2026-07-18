from flask import Blueprint

bp = Blueprint('concrete', __name__)

from app.concrete import routes
