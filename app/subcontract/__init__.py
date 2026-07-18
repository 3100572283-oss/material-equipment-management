from flask import Blueprint

bp = Blueprint('subcontract', __name__)

from app.subcontract import routes
