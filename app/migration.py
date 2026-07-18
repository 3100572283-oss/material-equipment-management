"""数据库迁移管理模块"""
import os
import re
from datetime import datetime
from flask import current_app
from app import db


def get_migrations_dir():
    """获取迁移脚本目录"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    migrations_dir = os.path.join(base_dir, 'migrations')
    os.makedirs(migrations_dir, exist_ok=True)
    return migrations_dir


def ensure_migration_table():
    """确保迁移记录表存在"""
    from sqlalchemy import text
    sql = """
    CREATE TABLE IF NOT EXISTS db_migration (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version VARCHAR(32) NOT NULL UNIQUE,
        description VARCHAR(256),
        applied_at DATETIME NOT NULL,
        status VARCHAR(16) DEFAULT 'success'
    )
    """
    db.session.execute(text(sql))
    db.session.commit()


def get_applied_versions():
    """获取已应用的迁移版本列表"""
    from sqlalchemy import text
    try:
        result = db.session.execute(
            text("SELECT version, description, applied_at, status FROM db_migration ORDER BY version")
        ).mappings().all()
        versions = []
        for row in result:
            row_dict = dict(row)
            # 转换字符串日期为datetime对象
            if isinstance(row_dict.get('applied_at'), str):
                try:
                    from datetime import datetime
                    row_dict['applied_at'] = datetime.fromisoformat(row_dict['applied_at'])
                except Exception:
                    pass
            versions.append(row_dict)
        return versions
    except Exception:
        return []


def get_pending_migrations():
    """获取待执行的迁移脚本"""
    migrations_dir = get_migrations_dir()
    applied = {m['version'] for m in get_applied_versions()}

    pending = []
    if os.path.exists(migrations_dir):
        for fname in sorted(os.listdir(migrations_dir)):
            if not fname.endswith('.sql'):
                continue
            # 文件名格式：V1__description.sql, V2__xxx.sql
            match = re.match(r'^V(\d+)__(.+)\.sql$', fname)
            if not match:
                continue
            version = match.group(1)
            description = match.group(2).replace('_', ' ')
            if version not in applied:
                pending.append({
                    'version': version,
                    'description': description,
                    'file': os.path.join(migrations_dir, fname),
                    'filename': fname,
                })
    return pending


def apply_migration(version, description, filepath):
    """执行单个迁移脚本"""
    from sqlalchemy import text

    with open(filepath, 'r', encoding='utf-8') as f:
        sql_content = f.read()

    # 按分号分割语句（简单处理，不处理存储过程等复杂情况）
    statements = [s.strip() for s in sql_content.split(';') if s.strip()]

    try:
        for stmt in statements:
            db.session.execute(text(stmt))

        # 记录迁移
        db.session.execute(
            text("""
            INSERT INTO db_migration (version, description, applied_at, status)
            VALUES (:version, :description, :applied_at, 'success')
            """),
            {
                'version': version,
                'description': description,
                'applied_at': datetime.utcnow(),
            }
        )
        db.session.commit()
        return True, None
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def run_migrations():
    """执行所有待执行的迁移"""
    ensure_migration_table()
    pending = get_pending_migrations()

    results = []
    for mig in pending:
        success, error = apply_migration(
            mig['version'], mig['description'], mig['file']
        )
        results.append({
            **mig,
            'success': success,
            'error': error,
        })
        if not success:
            # 遇到错误停止后续迁移
            break

    return results


def rollback_migration(version):
    """回滚到指定版本（删除该版本及之后的迁移记录，不执行实际DDL回滚）"""
    from sqlalchemy import text
    try:
        db.session.execute(
            text("DELETE FROM db_migration WHERE version >= :version"),
            {'version': version}
        )
        db.session.commit()
        return True, None
    except Exception as e:
        db.session.rollback()
        return False, str(e)
