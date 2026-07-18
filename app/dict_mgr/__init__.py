from flask import Blueprint
bp = Blueprint('dict_mgr', __name__)
from app.dict_mgr import routes
