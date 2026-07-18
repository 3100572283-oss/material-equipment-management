from flask import Blueprint
bp = Blueprint('steel', __name__)
from app.steel import routes
