from flask import Blueprint

bp = Blueprint('approval', __name__)

from app.approval import routes  # noqa
