#!/usr/bin/env python
"""合同模板种子数据导入脚本

在旧系统中创建合同模板表并导入8类合同模板种子数据。
变量schema内联定义，不依赖外部JSON文件。

使用方法：
    cd /opt/material-equipment-management
    venv/bin/python seed_contract_templates.py
"""
import json
import sys
import os

# 确保在正确的目录下运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import ContractTemplate, ContractInstance

# ============================================================
# 甲方固定信息常量
# ============================================================

PARTY_A = {
    'name': '中铁二十一局集团第六工程有限公司',
    'address': '天津滨海高新区塘沽海洋科技园盛安建设发展大厦1-2，4，5-106',
    'zipcode': '300459',
    'taxpayer_type': '增值税一般纳税人',
    'taxpayer_id': '91110302584495229A',
    'bank_name': '中国银行股份有限公司天津渤龙湖支行',
    'bank_account': '268794756854',
}


def _party_a_block(role_label='甲（买）方'):
    return """  {role}：{name}

  地址：{address}
  邮编：{zipcode}
  纳税人身份：{taxpayer_type}
  纳税人识别号：{taxpayer_id}
  开户银行名称：{bank_name}
  开户银行账号：{bank_account}""".format(
        role=role_label, name=PARTY_A['name'], address=PARTY_A['address'],
        zipcode=PARTY_A['zipcode'], taxpayer_type=PARTY_A['taxpayer_type'],
        taxpayer_id=PARTY_A['taxpayer_id'], bank_name=PARTY_A['bank_name'],
        bank_account=PARTY_A['bank_account'])


def _party_b_block(role_label='乙（卖）方', fields_prefix='seller'):
    return """  {role}：{{{{ {fp}_name }}}}

  注册地址: {{{{ {fp}_register_address }}}}     邮编：{{{{ {fp}_register_zipcode }}}}
  通讯地址：{{{{ {fp}_contact_address }}}}   邮编：{{{{ {fp}_contact_zipcode }}}}
  法定代表人：{{{{ {fp}_legal_person }}}}                         职务：{{{{ {fp}_legal_person_title }}}}
  营业执照编号：{{{{ {fp}_business_license_no }}}}
  资质证书编号：{{{{ {fp}_cert_no }}}}
  发证机关：{{{{ {fp}_cert_issuer }}}}
  资质专业及等级：{{{{ {fp}_cert_grade }}}}
  纳税人身份：{{{{ {fp}_taxpayer_type }}}}
  纳税人识别号：{{{{ {fp}_taxpayer_id }}}}
  复审时间及有效期：{{{{ {fp}_cert_review_date }}}}
  开户银行名称：{{{{ {fp}_bank_name }}}}
  开户银行账号：{{{{ {fp}_bank_account }}}}""".format(role=role_label, fp=fields_prefix)


# ============================================================
# 8类模板的变量Schema（内联定义）
# ============================================================

SCHEMA_MATERIAL_PURCHASE = [
    {"name": "project_name", "label": "项目名称", "type": "text", "required": True, "default": "", "placeholder": "中铁二十一局集团第六工程有限公司XXX项目部"},
    {"name": "package_no", "label": "包件编号", "type": "text", "required": False, "default": ""},
    {"name": "contract_no", "label": "合同编号", "type": "text", "required": True, "default": ""},
    {"name": "sign_date", "label": "合同签约时间", "type": "date", "required": True, "default": ""},
    {"name": "sign_location", "label": "合同签订地点", "type": "text", "required": False, "default": "甘肃省兰州市安宁区"},
    {"name": "perform_location", "label": "合同履行地点", "type": "text", "required": True, "default": ""},
    {"name": "seller_name", "label": "卖方名称", "type": "text", "required": True, "default": ""},
    {"name": "seller_register_address", "label": "卖方注册地址", "type": "text", "required": True, "default": ""},
    {"name": "seller_register_zipcode", "label": "卖方注册邮编", "type": "text", "required": False, "default": ""},
    {"name": "seller_contact_address", "label": "卖方通讯地址", "type": "text", "required": False, "default": ""},
    {"name": "seller_contact_zipcode", "label": "卖方通讯邮编", "type": "text", "required": False, "default": ""},
    {"name": "seller_legal_person", "label": "卖方法定代表人", "type": "text", "required": True, "default": ""},
    {"name": "seller_legal_person_title", "label": "卖方法人职务", "type": "text", "required": False, "default": ""},
    {"name": "seller_business_license_no", "label": "卖方营业执照编号", "type": "text", "required": False, "default": ""},
    {"name": "seller_cert_no", "label": "卖方资质证书编号", "type": "text", "required": False, "default": ""},
    {"name": "seller_cert_issuer", "label": "卖方发证机关", "type": "text", "required": False, "default": ""},
    {"name": "seller_cert_grade", "label": "卖方资质专业及等级", "type": "text", "required": False, "default": ""},
    {"name": "seller_taxpayer_type", "label": "卖方纳税人身份", "type": "select", "required": True, "default": "", "options": ["增值税一般纳税人", "小规模纳税人", "其他纳税人"]},
    {"name": "seller_taxpayer_id", "label": "卖方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "seller_cert_review_date", "label": "卖方资质复审时间及有效期", "type": "text", "required": False, "default": ""},
    {"name": "seller_bank_name", "label": "卖方开户银行", "type": "text", "required": True, "default": ""},
    {"name": "seller_bank_account", "label": "卖方银行账号", "type": "text", "required": True, "default": ""},
    {"name": "tender_no", "label": "招标编号", "type": "text", "required": False, "default": ""},
    {"name": "delivery_location", "label": "交货地点", "type": "text", "required": True, "default": ""},
    {"name": "total_amount", "label": "合同含税总价(数字)", "type": "number", "required": True, "default": ""},
    {"name": "total_amount_text", "label": "合同含税总价(大写)", "type": "text", "required": True, "default": ""},
    {"name": "amount_without_tax", "label": "不含税金额", "type": "number", "required": False, "default": ""},
    {"name": "tax_amount", "label": "税款", "type": "number", "required": False, "default": ""},
    {"name": "tax_rate", "label": "税率(%)", "type": "number", "required": True, "default": "13"},
    {"name": "contract_copies", "label": "合同份数", "type": "number", "required": False, "default": "4"},
    {"name": "buyer_contact_person", "label": "买方联系人", "type": "text", "required": False, "default": ""},
    {"name": "buyer_contact_phone", "label": "买方联系电话", "type": "text", "required": False, "default": ""},
    {"name": "seller_contact_person", "label": "卖方联系人", "type": "text", "required": False, "default": ""},
    {"name": "seller_contact_phone", "label": "卖方联系电话", "type": "text", "required": False, "default": ""},
    {"name": "buyer_agent", "label": "买方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "seller_agent", "label": "卖方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "items_table", "label": "订货明细表", "type": "table", "required": True, "default": [],
     "columns": [
       {"name": "seq", "label": "序号", "type": "number"},
       {"name": "material_name", "label": "物资名称", "type": "text"},
       {"name": "spec", "label": "规格型号", "type": "text"},
       {"name": "brand", "label": "牌号/商标/产地", "type": "text"},
       {"name": "unit", "label": "计量单位", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "price_without_tax", "label": "不含税单价(元)", "type": "number"},
       {"name": "tax_rate", "label": "增值税税率", "type": "number"},
       {"name": "tax_amount", "label": "增值税税额(元)", "type": "number"},
       {"name": "price_with_tax", "label": "含税单价(元)", "type": "number"},
       {"name": "total_price", "label": "价税合计(元)", "type": "number"},
       {"name": "remark", "label": "备注", "type": "text"}
     ]
    }
]

SCHEMA_TURNOVER_MATERIAL_LEASE = [
    {"name": "contract_no", "label": "合同编号", "type": "text", "required": True, "default": ""},
    {"name": "sign_date", "label": "签约时间", "type": "date", "required": True, "default": ""},
    {"name": "sign_location", "label": "签订地点", "type": "text", "required": False, "default": ""},
    {"name": "perform_location", "label": "履行地点", "type": "text", "required": True, "default": ""},
    {"name": "lessor_name", "label": "出租方名称", "type": "text", "required": True, "default": ""},
    {"name": "lessor_register_address", "label": "出租方注册地址", "type": "text", "required": True, "default": ""},
    {"name": "lessor_register_zipcode", "label": "出租方注册邮编", "type": "text", "required": False, "default": ""},
    {"name": "lessor_contact_address", "label": "出租方通讯地址", "type": "text", "required": False, "default": ""},
    {"name": "lessor_contact_zipcode", "label": "出租方通讯邮编", "type": "text", "required": False, "default": ""},
    {"name": "lessor_legal_person", "label": "出租方法定代表人", "type": "text", "required": True, "default": ""},
    {"name": "lessor_legal_person_title", "label": "出租方法人职务", "type": "text", "required": False, "default": ""},
    {"name": "lessor_business_license_no", "label": "出租方营业执照编号", "type": "text", "required": False, "default": ""},
    {"name": "lessor_cert_no", "label": "出租方资质证书编号", "type": "text", "required": False, "default": ""},
    {"name": "lessor_cert_issuer", "label": "出租方发证机关", "type": "text", "required": False, "default": ""},
    {"name": "lessor_cert_grade", "label": "出租方资质专业及等级", "type": "text", "required": False, "default": ""},
    {"name": "lessor_taxpayer_type", "label": "出租方纳税人身份", "type": "select", "required": True, "default": "", "options": ["增值税一般纳税人", "小规模纳税人", "其他纳税人"]},
    {"name": "lessor_taxpayer_id", "label": "出租方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "lessor_cert_review_date", "label": "出租方资质复审时间及有效期", "type": "text", "required": False, "default": ""},
    {"name": "lessor_bank_name", "label": "出租方开户银行", "type": "text", "required": True, "default": ""},
    {"name": "lessor_bank_account", "label": "出租方银行账号", "type": "text", "required": True, "default": ""},
    {"name": "lease_term_months", "label": "租赁期限(月)", "type": "number", "required": True, "default": ""},
    {"name": "lease_start_date", "label": "租赁开始日期", "type": "date", "required": True, "default": ""},
    {"name": "lease_end_date", "label": "租赁结束日期", "type": "date", "required": True, "default": ""},
    {"name": "delivery_location", "label": "交货地点", "type": "text", "required": True, "default": ""},
    {"name": "total_amount", "label": "合同含税总价(数字)", "type": "number", "required": True, "default": ""},
    {"name": "total_amount_text", "label": "合同含税总价(大写)", "type": "text", "required": True, "default": ""},
    {"name": "contract_copies", "label": "合同份数", "type": "number", "required": False, "default": "4"},
    {"name": "lessee_contact_person", "label": "承租方联系人", "type": "text", "required": False, "default": ""},
    {"name": "lessee_contact_phone", "label": "承租方联系电话", "type": "text", "required": False, "default": ""},
    {"name": "lessor_contact_person", "label": "出租方联系人", "type": "text", "required": False, "default": ""},
    {"name": "lessor_contact_phone", "label": "出租方联系电话", "type": "text", "required": False, "default": ""},
    {"name": "lessee_agent", "label": "承租方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "lessor_agent", "label": "出租方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "items_table", "label": "租赁物清单", "type": "table", "required": True, "default": [],
     "columns": [
       {"name": "material_name", "label": "租赁物名称", "type": "text"},
       {"name": "spec", "label": "规格型号", "type": "text"},
       {"name": "unit", "label": "计量单位", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "lease_method", "label": "租赁方式", "type": "text"},
       {"name": "price_without_tax", "label": "不含税单价(元)", "type": "number"},
       {"name": "tax_rate", "label": "增值税税率", "type": "number"},
       {"name": "tax_amount", "label": "增值税税额(元)", "type": "number"},
       {"name": "price_with_tax", "label": "含税单价(元)", "type": "number"},
       {"name": "total_price", "label": "价税合计(元)", "type": "number"},
       {"name": "remark", "label": "备注", "type": "text"}
     ]
    }
]

SCHEMA_EQUIPMENT_LEASE = [
    {"name": "contract_no", "label": "合同编号", "type": "text", "required": True, "default": ""},
    {"name": "sign_date", "label": "签订时间", "type": "date", "required": True, "default": ""},
    {"name": "sign_location", "label": "签约地点", "type": "text", "required": False, "default": ""},
    {"name": "perform_location", "label": "履行地点", "type": "text", "required": True, "default": ""},
    {"name": "lessee_legal_person", "label": "承租方法定代表人", "type": "text", "required": False, "default": ""},
    {"name": "lessor_name", "label": "出租方名称", "type": "text", "required": True, "default": ""},
    {"name": "lessor_cert_no", "label": "出租方资质证书号码", "type": "text", "required": False, "default": ""},
    {"name": "lessor_taxpayer_type", "label": "出租方纳税人资格", "type": "select", "required": True, "default": "", "options": ["一般纳税人", "小规模纳税人", "个人"]},
    {"name": "lessor_taxpayer_id", "label": "出租方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "lessor_register_address", "label": "出租方注册地址", "type": "text", "required": True, "default": ""},
    {"name": "lessor_legal_person", "label": "出租方法定代表人", "type": "text", "required": True, "default": ""},
    {"name": "lease_start_date", "label": "租赁开始日期", "type": "date", "required": True, "default": ""},
    {"name": "total_rent_amount", "label": "暂定租赁费总额(数字)", "type": "number", "required": True, "default": ""},
    {"name": "total_rent_amount_text", "label": "暂定租赁费总额(大写)", "type": "text", "required": True, "default": ""},
    {"name": "tax_method", "label": "纳税方式", "type": "text", "required": False, "default": ""},
    {"name": "vat_rate", "label": "增值税率(%)", "type": "number", "required": True, "default": "13"},
    {"name": "invoice_type", "label": "发票类型", "type": "select", "required": False, "default": "", "options": ["增值税专用发票", "增值税普通发票"]},
    {"name": "settlement_method", "label": "结算方式", "type": "select", "required": True, "default": "", "options": ["工作量法", "台班", "月租"]},
    {"name": "items_table", "label": "租赁设备清单", "type": "table", "required": True, "default": [],
     "columns": [
       {"name": "equipment_name", "label": "设备名称", "type": "text"},
       {"name": "spec", "label": "规格型号", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "unit", "label": "计价单位", "type": "text"},
       {"name": "rent_price", "label": "租赁单价(元)", "type": "number"},
       {"name": "vat_rate", "label": "增值税率(%)", "type": "number"},
       {"name": "vat_amount", "label": "增值税(元)", "type": "number"},
       {"name": "price_with_tax", "label": "含税单价(元)", "type": "number"},
       {"name": "lease_term", "label": "租赁期限", "type": "text"},
       {"name": "total_amount", "label": "租赁金额(元)", "type": "number"},
       {"name": "operator_count", "label": "操作人员人数", "type": "number"}
     ]
    },
    {"name": "operators_table", "label": "操作人员清单", "type": "table", "required": False, "default": [],
     "columns": [
       {"name": "name", "label": "操作人员名称", "type": "text"},
       {"name": "cert_no", "label": "证件编号", "type": "text"},
       {"name": "cert_issuer", "label": "发证单位", "type": "text"},
       {"name": "cert_expire", "label": "证件有效期", "type": "text"},
       {"name": "operator_type", "label": "操作人员类型", "type": "text"}
     ]
    }
]

SCHEMA_CONSTRUCTION_EQUIPMENT_PURCHASE = [
    {"name": "contract_no", "label": "合同编号", "type": "text", "required": True, "default": ""},
    {"name": "sign_date", "label": "签约时间", "type": "date", "required": True, "default": ""},
    {"name": "sign_location", "label": "签订地点", "type": "text", "required": False, "default": ""},
    {"name": "perform_location", "label": "履行地点", "type": "text", "required": True, "default": ""},
    {"name": "buyer_name", "label": "买方名称", "type": "text", "required": True, "default": ""},
    {"name": "buyer_address", "label": "买方地址", "type": "text", "required": True, "default": ""},
    {"name": "buyer_taxpayer_type", "label": "买方纳税人身份", "type": "select", "required": True, "default": "", "options": ["增值税一般纳税人", "小规模纳税人", "其他纳税人"]},
    {"name": "buyer_taxpayer_id", "label": "买方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "buyer_bank_name", "label": "买方开户银行", "type": "text", "required": True, "default": ""},
    {"name": "buyer_bank_account", "label": "买方银行账号", "type": "text", "required": True, "default": ""},
    {"name": "seller_name", "label": "卖方名称", "type": "text", "required": True, "default": ""},
    {"name": "seller_register_address", "label": "卖方注册地址", "type": "text", "required": True, "default": ""},
    {"name": "seller_legal_person", "label": "卖方法定代表人", "type": "text", "required": True, "default": ""},
    {"name": "seller_taxpayer_type", "label": "卖方纳税人身份", "type": "select", "required": True, "default": "", "options": ["增值税一般纳税人", "小规模纳税人", "其他纳税人"]},
    {"name": "seller_taxpayer_id", "label": "卖方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "project_name", "label": "工程名称", "type": "text", "required": True, "default": ""},
    {"name": "project_location", "label": "工程建设地点", "type": "text", "required": True, "default": ""},
    {"name": "project_scale", "label": "工程建设规模", "type": "text", "required": False, "default": ""},
    {"name": "equipment_name", "label": "设备名称", "type": "text", "required": True, "default": ""},
    {"name": "equipment_spec", "label": "设备规格", "type": "text", "required": True, "default": ""},
    {"name": "equipment_model", "label": "设备型号", "type": "text", "required": True, "default": ""},
    {"name": "manufacturer", "label": "生产厂家", "type": "text", "required": True, "default": ""},
    {"name": "total_amount", "label": "合同总价(数字)", "type": "number", "required": True, "default": ""},
    {"name": "total_amount_text", "label": "合同总价(大写)", "type": "text", "required": True, "default": ""},
    {"name": "invoice_type", "label": "发票类型", "type": "select", "required": True, "default": "", "options": ["增值税专用发票", "增值税普通发票"]},
    {"name": "delivery_location", "label": "交货地点", "type": "text", "required": True, "default": ""},
    {"name": "delivery_method", "label": "交货方式", "type": "select", "required": True, "default": "", "options": ["乙方送货到现场", "乙方代运", "甲方自提自运"]},
    {"name": "delivery_date", "label": "交货日期", "type": "date", "required": True, "default": ""},
    {"name": "install_location", "label": "安装地点", "type": "text", "required": True, "default": ""},
    {"name": "install_days", "label": "安装工期(天)", "type": "number", "required": True, "default": ""},
    {"name": "warranty_months", "label": "保修期(月)", "type": "number", "required": True, "default": ""},
    {"name": "contract_copies", "label": "合同份数", "type": "number", "required": False, "default": "4"},
    {"name": "buyer_contact_person", "label": "买方联系人", "type": "text", "required": False, "default": ""},
    {"name": "seller_contact_person", "label": "卖方联系人", "type": "text", "required": False, "default": ""},
    {"name": "items_table", "label": "设备购销一览表", "type": "table", "required": True, "default": [],
     "columns": [
       {"name": "seq", "label": "序号", "type": "number"},
       {"name": "equipment_name", "label": "设备名称", "type": "text"},
       {"name": "spec", "label": "规格型号", "type": "text"},
       {"name": "brand", "label": "牌号/商标/产地", "type": "text"},
       {"name": "unit", "label": "计量单位", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "price_without_tax", "label": "不含税单价(元)", "type": "number"},
       {"name": "tax_rate", "label": "增值税税率", "type": "number"},
       {"name": "tax_amount", "label": "增值税税额(元)", "type": "number"},
       {"name": "price_with_tax", "label": "含税单价(元)", "type": "number"},
       {"name": "total_price", "label": "价税合计(元)", "type": "number"},
       {"name": "remark", "label": "备注", "type": "text"}
     ]
    }
]

SCHEMA_GENERAL_GOODS_SALE = [
    {"name": "contract_no", "label": "合同编号", "type": "text", "required": True, "default": ""},
    {"name": "sign_date", "label": "签约时间", "type": "date", "required": True, "default": ""},
    {"name": "sign_location", "label": "签订地点", "type": "text", "required": False, "default": ""},
    {"name": "perform_location", "label": "履行地点", "type": "text", "required": True, "default": ""},
    {"name": "seller_name", "label": "卖方名称", "type": "text", "required": True, "default": ""},
    {"name": "seller_register_address", "label": "卖方注册地址", "type": "text", "required": True, "default": ""},
    {"name": "seller_legal_person", "label": "卖方法定代表人", "type": "text", "required": True, "default": ""},
    {"name": "seller_taxpayer_type", "label": "卖方纳税人身份", "type": "select", "required": True, "default": "", "options": ["增值税一般纳税人", "小规模纳税人", "其他纳税人"]},
    {"name": "seller_taxpayer_id", "label": "卖方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "seller_bank_name", "label": "卖方开户银行", "type": "text", "required": True, "default": ""},
    {"name": "seller_bank_account", "label": "卖方银行账号", "type": "text", "required": True, "default": ""},
    {"name": "business_scope", "label": "合同事宜", "type": "text", "required": True, "default": "", "placeholder": "如：XXX材料采购"},
    {"name": "delivery_location", "label": "交货地点", "type": "text", "required": True, "default": ""},
    {"name": "delivery_time", "label": "交货时间", "type": "text", "required": False, "default": "按照买方通知时间交货"},
    {"name": "delivery_method", "label": "运输方式", "type": "text", "required": False, "default": ""},
    {"name": "total_amount", "label": "合同含税总价(数字)", "type": "number", "required": True, "default": ""},
    {"name": "total_amount_text", "label": "合同含税总价(大写)", "type": "text", "required": True, "default": ""},
    {"name": "invoice_type", "label": "发票类型", "type": "select", "required": True, "default": "", "options": ["增值税专用发票", "增值税普通发票"]},
    {"name": "contract_copies", "label": "合同份数", "type": "number", "required": False, "default": "4"},
    {"name": "buyer_contact_person", "label": "买方联系人", "type": "text", "required": False, "default": ""},
    {"name": "seller_contact_person", "label": "卖方联系人", "type": "text", "required": False, "default": ""},
    {"name": "buyer_agent", "label": "买方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "seller_agent", "label": "卖方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "items_table", "label": "标的物清单", "type": "table", "required": True, "default": [],
     "columns": [
       {"name": "seq", "label": "序号", "type": "number"},
       {"name": "material_name", "label": "材料名称", "type": "text"},
       {"name": "spec", "label": "规格型号", "type": "text"},
       {"name": "brand", "label": "牌号/商标/产地", "type": "text"},
       {"name": "unit", "label": "计量单位", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "price_without_tax", "label": "不含税单价(元)", "type": "number"},
       {"name": "tax_rate", "label": "增值税税率", "type": "number"},
       {"name": "tax_amount", "label": "增值税税额(元)", "type": "number"},
       {"name": "price_with_tax", "label": "含税单价(元)", "type": "number"},
       {"name": "total_price", "label": "价税合计(元)", "type": "number"},
       {"name": "remark", "label": "备注", "type": "text"}
     ]
    }
]

SCHEMA_TRANSPORT_ENTRUST = [
    {"name": "contract_no", "label": "合同编号", "type": "text", "required": True, "default": ""},
    {"name": "sign_date", "label": "签约时间", "type": "date", "required": True, "default": ""},
    {"name": "sign_location", "label": "签订地点", "type": "text", "required": False, "default": ""},
    {"name": "perform_location", "label": "履行地点", "type": "text", "required": True, "default": ""},
    {"name": "carrier_name", "label": "承运方名称", "type": "text", "required": True, "default": ""},
    {"name": "carrier_register_address", "label": "承运方注册地址", "type": "text", "required": True, "default": ""},
    {"name": "carrier_legal_person", "label": "承运方法定代表人", "type": "text", "required": True, "default": ""},
    {"name": "carrier_taxpayer_type", "label": "承运方纳税人身份", "type": "select", "required": True, "default": "", "options": ["一般纳税人", "小规模纳税人", "其他纳税人"]},
    {"name": "carrier_taxpayer_id", "label": "承运方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "carrier_bank_name", "label": "承运方开户银行", "type": "text", "required": True, "default": ""},
    {"name": "carrier_bank_account", "label": "承运方银行账号", "type": "text", "required": True, "default": ""},
    {"name": "transport_scope", "label": "委托运输事宜", "type": "text", "required": True, "default": "", "placeholder": "如：XXX项目物资运输"},
    {"name": "departure_location", "label": "启运地点", "type": "text", "required": True, "default": ""},
    {"name": "arrival_location", "label": "到达地点", "type": "text", "required": True, "default": ""},
    {"name": "transport_vehicle_type", "label": "运输车型", "type": "text", "required": False, "default": ""},
    {"name": "transport_start_date", "label": "运输开始日期", "type": "date", "required": True, "default": ""},
    {"name": "transport_term_months", "label": "运输期限(月)", "type": "number", "required": True, "default": ""},
    {"name": "delivery_location", "label": "交货地点", "type": "text", "required": True, "default": ""},
    {"name": "delivery_time", "label": "交货时间", "type": "text", "required": False, "default": "按照甲方通知时间交货"},
    {"name": "total_amount", "label": "合同含税总价(数字)", "type": "number", "required": True, "default": ""},
    {"name": "total_amount_text", "label": "合同含税总价(大写)", "type": "text", "required": True, "default": ""},
    {"name": "invoice_type", "label": "发票类型", "type": "select", "required": True, "default": "", "options": ["增值税专用发票", "增值税普通发票"]},
    {"name": "contract_copies", "label": "合同份数", "type": "number", "required": False, "default": "4"},
    {"name": "consignor_contact_person", "label": "委托方联系人", "type": "text", "required": False, "default": ""},
    {"name": "carrier_contact_person", "label": "承运方联系人", "type": "text", "required": False, "default": ""},
    {"name": "consignor_agent", "label": "委托方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "carrier_agent", "label": "承运方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "items_table", "label": "运输货物清单", "type": "table", "required": True, "default": [],
     "columns": [
       {"name": "material_name", "label": "货物名称", "type": "text"},
       {"name": "unit", "label": "计量单位", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "price_without_tax", "label": "不含税单价(元)", "type": "number"},
       {"name": "tax_rate", "label": "增值税税率", "type": "number"},
       {"name": "tax_amount", "label": "增值税税额(元)", "type": "number"},
       {"name": "price_with_tax", "label": "含税单价(元)", "type": "number"},
       {"name": "total_price", "label": "价税合计(元)", "type": "number"},
       {"name": "remark", "label": "备注", "type": "text"}
     ]
    }
]

SCHEMA_MATERIAL_SUPPLEMENT = [
    {"name": "contract_no", "label": "合同编号(原合同编号-补序号)", "type": "text", "required": True, "default": ""},
    {"name": "original_contract_no", "label": "原合同编号", "type": "text", "required": True, "default": ""},
    {"name": "sign_date", "label": "合同签约时间", "type": "date", "required": True, "default": ""},
    {"name": "sign_location", "label": "合同签订地点", "type": "text", "required": False, "default": "甘肃省兰州市安宁区"},
    {"name": "perform_location", "label": "合同履行地点", "type": "text", "required": True, "default": ""},
    {"name": "project_name", "label": "项目名称", "type": "text", "required": True, "default": "", "placeholder": "中铁二十一局集团第六工程有限公司XXX项目部"},
    {"name": "seller_name", "label": "卖方名称", "type": "text", "required": True, "default": ""},
    {"name": "seller_register_address", "label": "卖方注册地址", "type": "text", "required": True, "default": ""},
    {"name": "seller_legal_person", "label": "卖方法定代表人", "type": "text", "required": True, "default": ""},
    {"name": "seller_taxpayer_type", "label": "卖方纳税人身份", "type": "select", "required": True, "default": "", "options": ["增值税一般纳税人", "小规模纳税人", "其他纳税人"]},
    {"name": "seller_taxpayer_id", "label": "卖方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "seller_bank_name", "label": "卖方开户银行", "type": "text", "required": True, "default": ""},
    {"name": "seller_bank_account", "label": "卖方银行账号", "type": "text", "required": True, "default": ""},
    {"name": "original_content", "label": "原合同约定内容", "type": "text", "required": True, "default": "", "placeholder": "如：XXX材料采购"},
    {"name": "adjust_start_date", "label": "调整开始日期", "type": "date", "required": False, "default": ""},
    {"name": "adjust_end_date", "label": "调整结束日期", "type": "date", "required": False, "default": ""},
    {"name": "adjust_material", "label": "调整材料名称", "type": "text", "required": False, "default": ""},
    {"name": "adjust_basis", "label": "调整基准", "type": "text", "required": False, "default": ""},
    {"name": "adjust_new_price", "label": "调整后含税到站结算单价", "type": "number", "required": False, "default": ""},
    {"name": "adjusted_amount", "label": "调整后合同额", "type": "number", "required": False, "default": ""},
    {"name": "adjusted_amount_without_tax", "label": "调整后不含税金额", "type": "number", "required": False, "default": ""},
    {"name": "adjusted_tax_amount", "label": "调整后税款", "type": "number", "required": False, "default": ""},
    {"name": "contract_copies", "label": "协议份数", "type": "number", "required": False, "default": "4"},
    {"name": "original_items_table", "label": "原合同订货明细表", "type": "table", "required": False, "default": [],
     "columns": [
       {"name": "seq", "label": "序号", "type": "number"},
       {"name": "material_name", "label": "材料名称", "type": "text"},
       {"name": "spec", "label": "规格型号", "type": "text"},
       {"name": "unit", "label": "计量单位", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "price_without_tax", "label": "不含税单价(元)", "type": "number"},
       {"name": "tax_rate", "label": "增值税税率(%)", "type": "number"},
       {"name": "tax_amount", "label": "增值税税额(元)", "type": "number"},
       {"name": "price_with_tax", "label": "含税单价(元)", "type": "number"},
       {"name": "total_price", "label": "价税合计(元)", "type": "number"},
       {"name": "remark", "label": "备注", "type": "text"}
     ]
    },
    {"name": "supplement_items_table", "label": "补充合同订货明细表", "type": "table", "required": False, "default": [],
     "columns": [
       {"name": "seq", "label": "序号", "type": "number"},
       {"name": "material_name", "label": "材料名称", "type": "text"},
       {"name": "spec", "label": "规格型号", "type": "text"},
       {"name": "unit", "label": "计量单位", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "price_without_tax", "label": "不含税单价(元)", "type": "number"},
       {"name": "tax_rate", "label": "增值税税率(%)", "type": "number"},
       {"name": "tax_amount", "label": "增值税税额(元)", "type": "number"},
       {"name": "price_with_tax", "label": "含税单价(元)", "type": "number"},
       {"name": "total_price", "label": "价税合计(元)", "type": "number"},
       {"name": "remark", "label": "备注", "type": "text"}
     ]
    }
]

SCHEMA_WASTE_DISPOSAL = [
    {"name": "contract_no", "label": "合同编号", "type": "text", "required": True, "default": ""},
    {"name": "tender_no", "label": "招标编号", "type": "text", "required": False, "default": ""},
    {"name": "package_no", "label": "包件编号", "type": "text", "required": False, "default": ""},
    {"name": "sign_date", "label": "合同签约时间", "type": "date", "required": True, "default": ""},
    {"name": "sign_location", "label": "合同签订地点", "type": "text", "required": False, "default": ""},
    {"name": "perform_location", "label": "合同履行地点", "type": "text", "required": False, "default": ""},
    {"name": "buyer_name", "label": "买方(处置受让方)名称", "type": "text", "required": True, "default": ""},
    {"name": "buyer_register_address", "label": "买方注册地址", "type": "text", "required": True, "default": ""},
    {"name": "buyer_legal_person", "label": "买方法定代表人", "type": "text", "required": True, "default": ""},
    {"name": "buyer_taxpayer_type", "label": "买方纳税人身份", "type": "select", "required": True, "default": "", "options": ["增值税一般纳税人", "小规模纳税人", "其他纳税人"]},
    {"name": "buyer_taxpayer_id", "label": "买方纳税人识别号", "type": "text", "required": True, "default": ""},
    {"name": "buyer_bank_name", "label": "买方开户银行", "type": "text", "required": True, "default": ""},
    {"name": "buyer_bank_account", "label": "买方银行账号", "type": "text", "required": True, "default": ""},
    {"name": "total_amount", "label": "合同总价(数字)", "type": "number", "required": True, "default": ""},
    {"name": "total_amount_text", "label": "合同总价(大写)", "type": "text", "required": True, "default": ""},
    {"name": "delivery_location", "label": "交货地点", "type": "text", "required": True, "default": ""},
    {"name": "payment_method", "label": "付款方式", "type": "select", "required": False, "default": "现金或银行转账(足额预付款)", "options": ["现金", "银行转账", "现金或银行转账(足额预付款)", "银行承兑汇票"]},
    {"name": "contract_copies", "label": "合同正本份数", "type": "number", "required": False, "default": "4"},
    {"name": "buyer_copies", "label": "买方执份数", "type": "number", "required": False, "default": "2"},
    {"name": "seller_contact_person", "label": "卖方联系人", "type": "text", "required": False, "default": ""},
    {"name": "buyer_contact_person", "label": "买方联系人", "type": "text", "required": False, "default": ""},
    {"name": "seller_agent", "label": "卖方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "buyer_agent", "label": "买方委托代理人", "type": "text", "required": False, "default": ""},
    {"name": "items_table", "label": "出售明细表", "type": "table", "required": True, "default": [],
     "columns": [
       {"name": "seq", "label": "序号", "type": "number"},
       {"name": "material_name", "label": "材料名称", "type": "text"},
       {"name": "spec", "label": "规格型号", "type": "text"},
       {"name": "unit", "label": "计量单位", "type": "text"},
       {"name": "quantity", "label": "数量", "type": "number"},
       {"name": "unit_price", "label": "单价(元)", "type": "number"},
       {"name": "amount", "label": "金额(元)", "type": "number"},
       {"name": "remark", "label": "备注", "type": "text"}
     ]
    }
]


# ============================================================
# 模板内容生成函数
# ============================================================

def _material_purchase_content():
    return """合同编号: {{ contract_no }}

项目名称：{{ project_name }}
包件编号：{{ package_no }}
买方：中铁二十一局集团第六工程有限公司
卖方：{{ seller_name }}

合同签约时间：{{ sign_date }}
合同签订地点：{{ sign_location }}
合同履行地点：{{ perform_location }}

# 物资采购合同

""" + _party_a_block('甲（买）方') + """

""" + _party_b_block('乙（卖）方', 'seller') + """

## 一、合同协议书

招标编号：{{ tender_no }}                  合同编号：{{ contract_no }}

买方（全称）：中铁二十一局集团第六工程有限公司
卖方（全称）：{{ seller_name }}

本合同含税总价为人民币 {{ total_amount_text }}（{{ total_amount }}元），其中不含税金额为{{ amount_without_tax }}元，税款为{{ tax_amount }}元，税率{{ tax_rate }}%（附订货明细表）。

本合同一式 {{ contract_copies }} 份，买卖双方各执同等份数，具有同等法律效力。

本合同自双方签字盖章之日起生效。

## 二、合同条款

（1）合同总价/合同金额指根据本合同规定卖方在正确地完全履行合同义务后，买方应支付给卖方的价款。
（2）物资指根据本合同规定，卖方须向买方提供订货明细表规定的一切物资。
（3）卖方应保证按合同规定向买方提供符合合同要求的物资设备。

交货地点：{{ delivery_location }}

## 附件1：订货明细表

{{ items_table }}

## 附件2：廉政协议书

本合同作为XX工程XX标物资采购的附件，与主合同具有同等的法律效力。


买（甲）方全称：中铁二十一局集团第六工程有限公司          卖（乙）方全称：{{ seller_name }}
（公章）                                      （公章）
联系人：{{ buyer_contact_person }}       联系人：{{ seller_contact_person }}
电话：{{ buyer_contact_phone }}          电话：{{ seller_contact_phone }}
法定代表人：                                  法定代表人：
委托代理人：{{ buyer_agent }}            委托代理人：{{ seller_agent }}
日期：{{ sign_date }}                     日期：{{ sign_date }}
"""


def _turnover_material_lease_content():
    return """合同编号：{{ contract_no }}

# 周转材料租赁合同

签约时间：{{ sign_date }}
签订地点：{{ sign_location }}
履行地点：{{ perform_location }}

""" + _party_a_block('甲（承租）方') + """

  乙（出租）方：{{ lessor_name }}
  注册地址: {{ lessor_register_address }}     邮编：{{ lessor_register_zipcode }}
  法定代表人：{{ lessor_legal_person }}       职务：{{ lessor_legal_person_title }}
  纳税人身份：{{ lessor_taxpayer_type }}
  纳税人识别号：{{ lessor_taxpayer_id }}
  开户银行名称：{{ lessor_bank_name }}
  开户银行账号：{{ lessor_bank_account }}

## 租赁物

{{ items_table }}

以上合同总价（含增值税）暂定为{{ total_amount }}元（人民币大写金额：{{ total_amount_text }}）。

## 租赁期限

租赁期限暂定为 {{ lease_term_months }} 个月，自 {{ lease_start_date }} 至 {{ lease_end_date }}。

## 交货地点

交货地点：{{ delivery_location }}

本合同一式 {{ contract_copies }} 份，双方各执同等份数。


承租（甲）方：中铁二十一局集团第六工程有限公司      出租（乙）方：{{ lessor_name }}
联系人：{{ lessee_contact_person }}    联系人：{{ lessor_contact_person }}
法定代表人：                      法定代表人：
委托代理人：{{ lessee_agent }}   委托代理人：{{ lessor_agent }}
"""


def _equipment_lease_content():
    return """合同编号：{{ contract_no }}

# 工程机械设备租赁合同

签约地点：{{ sign_location }}
履行地点：{{ perform_location }}
签订时间：{{ sign_date }}

甲方（承租方）：中铁二十一局集团第六工程有限公司
注册地址：天津滨海高新区塘沽海洋科技园盛安建设发展大厦1-2，4，5-106   邮编：300459
法定代表人：{{ lessee_legal_person }}
纳税人资格：一般纳税人

乙方（出租方）：{{ lessor_name }}
资质证书号码：{{ lessor_cert_no }}
纳税人资格：{{ lessor_taxpayer_type }}
纳税人识别号：{{ lessor_taxpayer_id }}
注册地址：{{ lessor_register_address }}
法定代表人：{{ lessor_legal_person }}

## 第一条 租赁设备及操作人员

{{ items_table }}

{{ operators_table }}

## 第二条 租赁期限、租金

租赁期限自 {{ lease_start_date }} 进场验收合格时开始计算。

本合同暂定租赁设备租赁费总额为 {{ total_rent_amount }} 元人民币（大写：{{ total_rent_amount_text }}）。

纳税方式：{{ tax_method }}。增值税率：{{ vat_rate }}%。发票类型：{{ invoice_type }}。

## 第三条 结算方式

结算方式：{{ settlement_method }}

本合同一式四份，甲方执叁份，乙方执壹份。


甲方（承租人）：中铁二十一局集团第六工程有限公司    乙方（出租人）：{{ lessor_name }}
法定代表人：                       法定代表人：
"""


def _construction_equipment_purchase_content():
    return """合同编号：{{ contract_no }}

# 建设工程设备采购合同

签订地点：{{ sign_location }}
履行地点：{{ perform_location }}

甲（买）方：{{ buyer_name }}
地址：{{ buyer_address }}
纳税人身份：{{ buyer_taxpayer_type }}
纳税人识别号：{{ buyer_taxpayer_id }}
开户银行：{{ buyer_bank_name }}
银行账号：{{ buyer_bank_account }}

乙（卖）方：{{ seller_name }}
注册地址：{{ seller_register_address }}
法定代表人：{{ seller_legal_person }}
纳税人身份：{{ seller_taxpayer_type }}
纳税人识别号：{{ seller_taxpayer_id }}

## 工程概况

工程名称：{{ project_name }}
工程建设地点：{{ project_location }}
工程建设规模：{{ project_scale }}

## 设备信息

设备名称：{{ equipment_name }}
规格：{{ equipment_spec }}
型号：{{ equipment_model }}
生产厂家：{{ manufacturer }}

## 合同价款

合同总价暂定为人民币 {{ total_amount_text }}（{{ total_amount }}元）。

发票类型：{{ invoice_type }}

## 交货与安装

交货地点：{{ delivery_location }}
交货方式：{{ delivery_method }}
交货日期：{{ delivery_date }}

安装地点：{{ install_location }}
安装工期：{{ install_days }} 日

保修期：{{ warranty_months }} 个月

## 设备购销一览表

{{ items_table }}

本合同一式 {{ contract_copies }} 份，具有同等法律效力。


买（甲）方全称：{{ buyer_name }}    卖（乙）方全称：{{ seller_name }}
联系人：{{ buyer_contact_person }}   联系人：{{ seller_contact_person }}
"""


def _general_goods_sale_content():
    return """合同编号：{{ contract_no }}

# 买卖合同

签约时间：{{ sign_date }}
签订地点：{{ sign_location }}
履行地点：{{ perform_location }}

甲（买）方：中铁二十一局集团第六工程有限公司
地址：天津滨海高新区塘沽海洋科技园盛安建设发展大厦1-2，4，5-106
邮编：300459
纳税人身份：增值税一般纳税人
纳税人识别号：91110302584495229A
开户银行：中国银行股份有限公司天津渤龙湖支行
开户银行账号：268794756854

乙（卖）方：{{ seller_name }}
注册地址：{{ seller_register_address }}
法定代表人：{{ seller_legal_person }}
纳税人身份：{{ seller_taxpayer_type }}
纳税人识别号：{{ seller_taxpayer_id }}
开户银行：{{ seller_bank_name }}
开户银行账号：{{ seller_bank_account }}

中铁二十一局集团第六工程有限公司（以下简称"买方"）和{{ seller_name }}（以下简称"卖方"）双方本着平等互利、友好协商的原则，依据《中华人民共和国民法典》等法律法规的规定，就 {{ business_scope }} 事宜达成以下合同。

## 标的物清单

{{ items_table }}

以上合同总价（含增值税）暂定为{{ total_amount }}元（人民币大写金额：{{ total_amount_text }}）。

## 交货方式

交货时间：{{ delivery_time }}
交货地点：{{ delivery_location }}
运输方式：{{ delivery_method }}

发票类型：{{ invoice_type }}

本合同一式 {{ contract_copies }} 份，买、卖双方各执同等份数。


买（甲）方全称：中铁二十一局集团第六工程有限公司    卖（乙）方全称：{{ seller_name }}
联系人：{{ buyer_contact_person }}   联系人：{{ seller_contact_person }}
委托代理人：{{ buyer_agent }}        委托代理人：{{ seller_agent }}
"""


def _transport_entrust_content():
    return """合同编号：{{ contract_no }}

# 委托运输合同

签约时间：{{ sign_date }}
签订地点：{{ sign_location }}
履行地点：{{ perform_location }}

""" + _party_a_block('甲（委托）方') + """

  乙（承运）方：{{ carrier_name }}
  注册地址：{{ carrier_register_address }}
  法定代表人：{{ carrier_legal_person }}     职务：
  纳税人身份：{{ carrier_taxpayer_type }}
  纳税人识别号：{{ carrier_taxpayer_id }}
  开户银行名称：{{ carrier_bank_name }}
  开户银行账号：{{ carrier_bank_account }}

双方就 {{ transport_scope }} 事宜达成以下合同。

## 第1条 运输内容

{{ items_table }}

以上合同总价（含增值税）暂定为{{ total_amount }}元（人民币大写金额：{{ total_amount_text }}）。

## 第2条 运输路线

启运地点：{{ departure_location }}
到达地点：{{ arrival_location }}
运输车型：{{ transport_vehicle_type }}

## 第3条 运输期限

运输期限暂定从 {{ transport_start_date }} 开始计算，暂定 {{ transport_term_months }} 个月运输结束。

## 第4条 交货

交货地点：{{ delivery_location }}
交货时间：{{ delivery_time }}

发票类型：{{ invoice_type }}

本合同一式 {{ contract_copies }} 份，甲、乙双方各执同等份数。


委托（甲）方全称：中铁二十一局集团第六工程有限公司    承运（乙）方全称：{{ carrier_name }}
联系人：{{ consignor_contact_person }}   联系人：{{ carrier_contact_person }}
委托代理人：{{ consignor_agent }}       委托代理人：{{ carrier_agent }}
"""


def _material_supplement_content():
    return """合同编号：{{ contract_no }}

# 采购补充合同

项目名称：{{ project_name }}

买方：中铁二十一局集团第六工程有限公司
卖方：{{ seller_name }}

合同签约时间：{{ sign_date }}
合同签订地点：{{ sign_location }}
合同履行地点：{{ perform_location }}

""" + _party_a_block('甲（买）方') + """

""" + _party_b_block('乙（卖）方', 'seller') + """

## 补充协议

合同编号：{{ contract_no }}

甲方：中铁二十一局集团第六工程有限公司
乙方：{{ seller_name }}

甲乙双方就原合同（合同编号为：{{ original_contract_no }}）所约定的 {{ original_content }} 进行调整，特制定本协议。

自 {{ adjust_start_date }} 至 {{ adjust_end_date }} 乙方所供甲方的 {{ adjust_material }} 材料以 {{ adjust_basis }} 为基准调整至 {{ adjust_new_price }} 作为双方含税到站结算单价。

合同额调整为 {{ adjusted_amount }} 元，不含税金额为 {{ adjusted_amount_without_tax }} 元，税款 {{ adjusted_tax_amount }} 元。

## 附件一：原合同订货明细表

{{ original_items_table }}

## 附件二：补充合同订货明细表

{{ supplement_items_table }}

本补充协议一式 {{ contract_copies }} 份，甲方执同等份数，乙方执同等份数。


甲方：中铁二十一局集团第六工程有限公司    乙方：{{ seller_name }}
法定代表人或委托代理人：     法定代表人或委托代理人：
签订日期：{{ sign_date }}  签订日期：
"""


def _waste_disposal_content():
    return """合同编号：{{ contract_no }}

# 废旧物资处置合同

包件编号：{{ package_no }}
买方：{{ buyer_name }}
卖方：中铁二十一局集团第六工程有限公司

合同签约时间：{{ sign_date }}
合同签订地点：{{ sign_location }}
合同履行地点：{{ perform_location }}

  甲（卖）方：中铁二十一局集团第六工程有限公司
  地址：天津滨海高新区塘沽海洋科技园盛安建设发展大厦1-2，4，5-106   邮编：300459
  纳税人身份：增值税一般纳税人
  纳税人识别号：91110302584495229A
  开户银行名称：中国银行股份有限公司天津渤龙湖支行
  开户银行账号：268794756854

  乙（买）方：{{ buyer_name }}
  注册地址：{{ buyer_register_address }}   邮编：
  法定代表人：{{ buyer_legal_person }}     职务：
  纳税人身份：{{ buyer_taxpayer_type }}
  纳税人识别号：{{ buyer_taxpayer_id }}
  开户银行名称：{{ buyer_bank_name }}
  开户银行账号：{{ buyer_bank_account }}

## 一、合同协议书

招标编号：{{ tender_no }}   合同编号：{{ contract_no }}

卖方(全称)：中铁二十一局集团第六工程有限公司
买方(全称)：{{ buyer_name }}

本合同总价为人民币 {{ total_amount_text }}（{{ total_amount }}元）。

付款方式：{{ payment_method }}

交货地点：{{ delivery_location }}

本合同正本一式 {{ contract_copies }} 份，买卖双方各执 {{ buyer_copies }} 份，具有同等法律效力。

## 附件1：出售明细表

{{ items_table }}

## 附件2：廉政协议书

本合同作为废旧物资处置合同的附件，与主合同具有同等的法律效力。


卖（甲）方全称：中铁二十一局集团第六工程有限公司    买（乙）方全称：{{ buyer_name }}
联系人：{{ seller_contact_person }}     联系人：{{ buyer_contact_person }}
法定代表人：                  法定代表人：
委托代理人：{{ seller_agent }}    委托代理人：{{ buyer_agent }}
"""


# ============================================================
# 模板定义列表
# ============================================================

TEMPLATES = [
    {
        'code': 'material_purchase',
        'name': '物资采购合同',
        'description': '用于项目物资设备采购，包含完整的合同条款、技术规格书和订货明细表',
        'category': '采购类',
        'template_content': _material_purchase_content(),
        'variables_schema': json.dumps(SCHEMA_MATERIAL_PURCHASE, ensure_ascii=False),
    },
    {
        'code': 'turnover_material_lease',
        'name': '周转材料租赁合同',
        'description': '用于周转材料（模板、脚手架等）的对外租赁，含租赁物清单和结算协议',
        'category': '租赁类',
        'template_content': _turnover_material_lease_content(),
        'variables_schema': json.dumps(SCHEMA_TURNOVER_MATERIAL_LEASE, ensure_ascii=False),
    },
    {
        'code': 'equipment_lease',
        'name': '机械设备租赁合同',
        'description': '用于工程机械设备（挖掘机、装载机等）的租赁，含设备清单、操作人员配备及结算附件',
        'category': '租赁类',
        'template_content': _equipment_lease_content(),
        'variables_schema': json.dumps(SCHEMA_EQUIPMENT_LEASE, ensure_ascii=False),
    },
    {
        'code': 'construction_equipment_purchase',
        'name': '建设工程设备采购合同',
        'description': '用于建设工程中固定设备的采购，含设备清单、安装调试、保修等条款',
        'category': '采购类',
        'template_content': _construction_equipment_purchase_content(),
        'variables_schema': json.dumps(SCHEMA_CONSTRUCTION_EQUIPMENT_PURCHASE, ensure_ascii=False),
    },
    {
        'code': 'general_goods_sale',
        'name': '一般货物买卖合同',
        'description': '通用货物买卖合同，适用于各类一般货物的采购与销售',
        'category': '买卖类',
        'template_content': _general_goods_sale_content(),
        'variables_schema': json.dumps(SCHEMA_GENERAL_GOODS_SALE, ensure_ascii=False),
    },
    {
        'code': 'transport_entrust',
        'name': '委托运输合同',
        'description': '用于委托运输物资，含运输内容清单、启运到达地点、验收等条款',
        'category': '运输类',
        'template_content': _transport_entrust_content(),
        'variables_schema': json.dumps(SCHEMA_TRANSPORT_ENTRUST, ensure_ascii=False),
    },
    {
        'code': 'material_supplement',
        'name': '材料补充合同',
        'description': '用于对已签订的原材料采购合同进行价格、数量、税率等调整的补充协议',
        'category': '采购类',
        'template_content': _material_supplement_content(),
        'variables_schema': json.dumps(SCHEMA_MATERIAL_SUPPLEMENT, ensure_ascii=False),
    },
    {
        'code': 'waste_disposal',
        'name': '废旧物资处置合同',
        'description': '用于废旧物资的出售处置，含出售明细表和廉政协议书',
        'category': '处置类',
        'template_content': _waste_disposal_content(),
        'variables_schema': json.dumps(SCHEMA_WASTE_DISPOSAL, ensure_ascii=False),
    },
]


# ============================================================
# 主函数
# ============================================================

def run_seed():
    """执行种子数据导入"""
    from datetime import datetime

    # 创建数据库表
    db.create_all()
    print('数据库表创建完成')

    created_count = 0
    updated_count = 0

    for tmpl_data in TEMPLATES:
        existing = ContractTemplate.query.filter_by(code=tmpl_data['code']).first()
        if existing:
            existing.name = tmpl_data['name']
            existing.description = tmpl_data['description']
            existing.template_content = tmpl_data['template_content']
            existing.variables_schema = tmpl_data['variables_schema']
            existing.category = tmpl_data['category']
            existing.updated_at = datetime.now()
            updated_count += 1
            print('  更新模板: {} ({})'.format(tmpl_data['name'], tmpl_data['code']))
        else:
            tmpl = ContractTemplate(
                code=tmpl_data['code'],
                name=tmpl_data['name'],
                description=tmpl_data['description'],
                template_content=tmpl_data['template_content'],
                variables_schema=tmpl_data['variables_schema'],
                category=tmpl_data['category'],
                is_active=True,
                version='1.0',
            )
            db.session.add(tmpl)
            created_count += 1
            print('  创建模板: {} ({})'.format(tmpl_data['name'], tmpl_data['code']))

    db.session.commit()
    print('\n种子数据导入完成：新建 {} 个模板，更新 {} 个模板'.format(created_count, updated_count))

    # 验证
    total = ContractTemplate.query.count()
    print('数据库中共有 {} 个合同模板'.format(total))


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        run_seed()
