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

    # P1-7: CSRF保护配置
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # P2: Token有效期1小时
    WTF_CSRF_SSL_STRICT = False
    # JWT配置（移动端API认证）
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', SECRET_KEY)
    JWT_EXPIRATION_HOURS = 24          # access token有效期
    JWT_REFRESH_EXPIRATION_DAYS = 30   # refresh token有效期  # 允许HTTP（nginx代理）

    # 数据库配置
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'material_mgmt.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # P1安全加固: SQLAlchemy连接池配置（SQLite 本地/测试无需连接池，避免报错）
    if SQLALCHEMY_DATABASE_URI.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS = {}
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_size': 10,
            'pool_recycle': 3600,
            'pool_pre_ping': True,
            'max_overflow': 20
        }

    # P1安全加固: Session安全属性
    SESSION_COOKIE_SECURE = True       # 仅HTTPS传输
    SESSION_COOKIE_HTTPONLY = True     # 禁止JS访问cookie
    SESSION_COOKIE_SAMESITE = 'Lax'    # 防止CSRF

    # 上传文件配置
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or \
        os.path.join(basedir, 'app', 'static', 'uploads')
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # P2: 10MB max upload

    # 分页配置
    ITEMS_PER_PAGE = int(os.environ.get('ITEMS_PER_PAGE', 10))

    # AI 配置（可选，不设置则禁用 AI 功能）
    AI_API_KEY = os.environ.get('AI_API_KEY', '')
    AI_MODEL = os.environ.get('AI_MODEL', 'doubao-1.5-pro-32k')


    # P2: Redis 缓存配置
    REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
    REDIS_DB = int(os.environ.get('REDIS_DB', 0))
    REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', None)

    # 管理员初始密码（首次初始化时创建 admin 账号使用）
    # 生产环境部署后请立即修改 admin 密码
    ADMIN_DEFAULT_PASSWORD = os.environ.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')

    # M0 权限中台灰度开关：true 时启用 auth_core 新权限（已迁移用户走新判定）
    # 默认 false —— 旧权限逻辑继续生效，保证平滑回退
    AUTH_CORE_ENABLED = os.environ.get('AUTH_CORE_ENABLED', 'false').lower() == 'true'
