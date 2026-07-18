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
    
    print("Database migration completed!")
    
    # 验证
    inspector2 = inspect(db.engine)
    print("\nCategories columns:", [c['name'] for c in inspector2.get_columns('categories')])
    print("Inventory columns:", [c['name'] for c in inspector2.get_columns('inventory')])
    
    all_tables = inspector2.get_table_names()
    print("\nAll tables:")
    for t in sorted(all_tables):
        print(f"  - {t}")
