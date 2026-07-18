from flask import Blueprint

bp = Blueprint('purchase_requisition', __name__, url_prefix='/purchase_requisition')

from app.purchase_requisition import routes