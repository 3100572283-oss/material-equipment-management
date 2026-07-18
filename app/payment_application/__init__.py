from flask import Blueprint

bp = Blueprint('payment_application', __name__)

from app.payment_application import routes
