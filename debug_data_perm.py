"""调试数据权限实际生效情况"""
import sys
sys.path.insert(0, '/opt/material-equipment-management')
from app import create_app, db
from app.models import User, StockIn, SysRoleDataScope
from app.utils import apply_data_scope

app = create_app()
with app.test_request_context('/stock_in/'):
    from flask import session
    session['current_project_id'] = None  # 汇总模式

    with app.app_context():
        for username in ['admin', 'test_user_a1', 'test_user_b1']:
            user = User.query.filter_by(username=username).first()
            if not user:
                print(f'{username}: 不存在')
                continue
            print(f'\n=== {username} ===')
            print(f'  user.id={user.id} dept_id={user.dept_id} role_id={user.role_id}')
            print(f'  is_admin={user.is_admin()}')
            print(f'  data_scope={user.get_data_scope()}')
            role = user.role_obj
            if role:
                print(f'  role_code={role.role_code}')
                cfg = SysRoleDataScope.query.filter_by(role_id=role.id).first()
                if cfg:
                    print(f'  data_scope_cfg: data_scope={cfg.data_scope} custom_depts={cfg.custom_depts}')

            # 模拟查询
            q = StockIn.query.filter(StockIn.status != 'voided')
            q = apply_data_scope(q, StockIn, user)
            count = q.count()
            print(f'  查询结果: {count} 条')
            # 详细看SQL
            from sqlalchemy.dialects import sqlite
            sql = str(q.statement.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
            print(f'  SQL: {sql[:300]}')
