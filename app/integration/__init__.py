# app/integration/__init__.py
"""外部对接中心（Integration Hub）。

统一管理与外部平台的对接能力：
- 外部数据源凭据（企查查等，API Key 加密存储，后台可配置，无需改代码）
- 查询结果缓存（按次计费接口的调用节流）
- 导出模板（铁建云链等平台的资料包/台账字段可配置映射）

设计原则：
1) 凭据、接口地址、模板字段全部落库可配置 —— 拿到真实 key / 真实模板时零改码上线。
2) 外部依赖不可用时全链路优雅降级为人工录入，绝不阻断主业务。
"""
from flask import Blueprint

integration_bp = Blueprint('integration', __name__, template_folder='../templates')

from app.integration import routes  # noqa: E402,F401
