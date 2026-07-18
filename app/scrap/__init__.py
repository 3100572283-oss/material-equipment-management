from flask import Blueprint

bp = Blueprint('scrap', __name__)

from app.scrap import routes
