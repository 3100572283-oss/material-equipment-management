from app import create_app, db
from app.models import User
from werkzeug.security import generate_password_hash

app = create_app()

with app.app_context():
    db.create_all()

    # Create default users if not exists
    default_users = [
        {'username': 'admin', 'password': 'Admin@2024', 'role': 'admin', 'name': '系统管理员'},
        {'username': 'editor', 'password': 'Editor@2024', 'role': 'editor', 'name': '数据录入员'},
        {'username': 'viewer', 'password': 'Viewer@2024', 'role': 'viewer', 'name': '查看员'}
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
