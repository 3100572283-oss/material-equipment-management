import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    """应用配置类示例 - 复制为 config.py 或创建 .env 文件使用"""

    # Flask 安全密钥（生产环境务必修改为随机长字符串！）
    # 生成命令: python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here-change-in-production'

    # 数据库配置（默认 SQLite，生产环境可改为 MySQL/PostgreSQL）
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'material_mgmt.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 上传文件目录
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or \
        os.path.join(basedir, 'app', 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

    # 分页配置
    ITEMS_PER_PAGE = int(os.environ.get('ITEMS_PER_PAGE', 10))

    # AI 配置（可选，不设置则禁用 AI 功能）
    AI_API_KEY = os.environ.get('AI_API_KEY', '')
    AI_MODEL = os.environ.get('AI_MODEL', 'doubao-pro-32k')

    # 管理员初始密码（首次初始化时使用，部署后请立即修改）
    ADMIN_DEFAULT_PASSWORD = os.environ.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')
