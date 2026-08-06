# migrate_auth_to_core.py
"""M0 权限中台 ETL 迁移脚本：旧权限 → auth_core（单向、幂等）。

运行方式（必须在项目根目录、有 .env 的环境下）：
    python migrate_auth_to_core.py

说明：
- 旧 users 当前均为测试内容，init_auth_core_data() 不自动迁移，仅建骨架+超级管理员。
- 本脚本用于「后续需要把真实（或现有）旧用户/角色/组织迁到 auth_core」时按需运行。
- 仅读取旧表，写入 auth_core_* 表；旧表不被修改、不被删除。
- 幂等：已迁移的跳過（按 username / org_code / role_code 去重）。

安全：本脚本只用本地轻量 app 上下文，不触发 create_app 重依赖；不连接生产服务器。
"""
import os
import sys

# 最小环境变量（config 需要 SECRET_KEY），避免触发 RuntimeError
os.environ.setdefault('SECRET_KEY', 'migrate-only-secret')

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# 复用仓库的 db 单例，避免重复绑定
from app import db

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or \
    'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'material_mgmt.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)


def main():
    with app.app_context():
        # 确保 auth_core 表存在
        from app.auth_core import models  # noqa
        db.create_all()
        from app.auth_core import adapter
        print("[migrate] start: legacy -> auth_core (one-way, idempotent)")
        report = adapter.sync_all_from_legacy()
        print("[migrate] done. report =", report)
        return report


if __name__ == '__main__':
    main()
