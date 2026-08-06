import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


class Config:
    """应用配置类"""

    # Flask 安全密钥（生产环境务必修改！）
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY 环境变量未设置。\n"
            "请复制 .env.example 为 .env 并设置 SECRET_KEY，\n"
            "或在环境变量中 export SECRET_KEY='your-secret-key'"
        )

    # 数据库配置
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'material_mgmt.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 上传文件配置
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or \
        os.path.join(basedir, 'app', 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

    # 分页配置
    ITEMS_PER_PAGE = int(os.environ.get('ITEMS_PER_PAGE', 10))

    # AI 配置（可选，不设置则禁用 AI 功能）
    AI_API_KEY = os.environ.get('AI_API_KEY', '')
    AI_MODEL = os.environ.get('AI_MODEL', 'doubao-pro-32k')

    # 管理员初始密码（首次初始化时创建 admin 账号使用）
    # 生产环境部署后请立即修改 admin 密码
    ADMIN_DEFAULT_PASSWORD = os.environ.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')

    # M0 权限中台灰度开关：true 时启用 auth_core 新权限（已迁移用户走新判定）
    # 默认 false —— 旧权限逻辑继续生效，保证平滑回退
    AUTH_CORE_ENABLED = os.environ.get('AUTH_CORE_ENABLED', 'false').lower() == 'true'
