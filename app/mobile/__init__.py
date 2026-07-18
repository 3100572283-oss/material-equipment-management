from flask import Blueprint

bp = Blueprint('mobile', __name__)

from app.mobile import routes
