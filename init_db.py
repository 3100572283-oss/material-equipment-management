"""
数据库初始化脚本

首次运行或需要重置数据库时执行：
    python init_db.py

本脚本会：
1. 创建所有数据表（如果不存在）
2. 初始化系统基础数据（菜单、角色、字典、配置等）
3. 创建默认管理员账号

注意：create_app() 本身已包含自动初始化逻辑，
      正常情况下直接运行 run.py 即可自动完成初始化。
"""
from app import create_app, db
from app.models import User
from werkzeug.security import generate_password_hash

app = create_app()

with app.app_context():
    # 创建所有表
    db.create_all()

    # 初始化默认管理员账号（如果不存在）
    admin_password = app.config.get('ADMIN_DEFAULT_PASSWORD', 'Admin@2024')

    default_users = [
        {'username': 'admin', 'password': admin_password, 'role': 'admin', 'name': '系统管理员'},
        {'username': 'editor', 'password': admin_password, 'role': 'editor', 'name': '数据录入员'},
        {'username': 'viewer', 'password': admin_password, 'role': 'viewer', 'name': '查看员'}
    ]

    for u in default_users:
        if not User.query.filter_by(username=u['username']).first():
            user = User(
                username=u['username'],
                password_hash=generate_password_hash(u['password'], method='pbkdf2:sha256'),
                role=u['role'],
                name=u['name']
            )
            db.session.add(user)

    db.session.commit()
    print('Database initialized and default users created.')
    print(f'Admin password: {admin_password}')
