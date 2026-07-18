from flask import Blueprint

bp = Blueprint('org_sync', __name__)

from app.org_sync import routes
