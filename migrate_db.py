from app import create_app, db
from sqlalchemy import text, inspect

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    
    # 1. categories表添加negative_stock_policy列
    categories_cols = [c['name'] for c in inspector.get_columns('categories')]
    if 'negative_stock_policy' not in categories_cols:
        print("Adding negative_stock_policy to categories...")
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE categories ADD COLUMN negative_stock_policy VARCHAR(16) DEFAULT 'global'"))
            conn.commit()
    
    # 2. inventory表添加in_transit_qty列
    inventory_cols = [c['name'] for c in inspector.get_columns('inventory')]
    if 'in_transit_qty' not in inventory_cols:
        print("Adding in_transit_qty to inventory...")
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE inventory ADD COLUMN in_transit_qty NUMERIC(18,4) DEFAULT 0"))
            conn.commit()
    
    # 3. 检查并创建新表 (material_quotas, period_closes, movement_snapshots)
    from app.models import MaterialQuota, PeriodClose, MovementSnapshot

    # 创建所有新表
    db.create_all()

    # 4. 审批单据状态统一改造：为没有status字段的表添加status列
    tables_to_add_status = [
        ('stock_ins', 'approved'),
        ('stock_outs', 'approved'),
        ('material_scrap', 'draft'),
    ]
    for table_name, default_val in tables_to_add_status:
        cols = [c['name'] for c in inspector.get_columns(table_name)]
        if 'status' not in cols:
            print(f"Adding status to {table_name}...")
            with db.engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN status VARCHAR(16) DEFAULT '{default_val}'"))
                conn.commit()

    # 5. 更新reconciliations表的status值（旧值'草稿'→'draft'）
    with db.engine.connect() as conn:
        conn.execute(text("UPDATE reconciliations SET status = 'draft' WHERE status = '草稿'"))
        conn.commit()

    # 6. 历史数据兼容性：所有已有审批类单据的status设为approved（已通过）
    # 对于stock_ins和stock_outs，历史数据默认已通过
    with db.engine.connect() as conn:
        # stock_ins: 如果approval_status是passed则status设为approved
        conn.execute(text("UPDATE stock_ins SET status = 'approved' WHERE approval_status = 'passed' AND (status IS NULL OR status = '')"))
        # stock_outs
        conn.execute(text("UPDATE stock_outs SET status = 'approved' WHERE approval_status = 'passed' AND (status IS NULL OR status = '')"))
        # reconciliations: 已有status字段，如果原来是'草稿'现在已是'draft'
        conn.execute(text("UPDATE reconciliations SET status = 'approved' WHERE approval_status = 'passed' AND (status IS NULL OR status = '')"))
        # material_scrap
        conn.execute(text("UPDATE material_scrap SET status = 'approved' WHERE approval_status = 'passed' AND (status IS NULL OR status = '')"))
        conn.commit()

    print("Database migration completed!")
    
    # 验证
    inspector2 = inspect(db.engine)
    print("\nCategories columns:", [c['name'] for c in inspector2.get_columns('categories')])
    print("Inventory columns:", [c['name'] for c in inspector2.get_columns('inventory')])
    
    all_tables = inspector2.get_table_names()
    print("\nAll tables:")
    for t in sorted(all_tables):
        print(f"  - {t}")
