from app import create_app, db
from sqlalchemy import text, inspect

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)

    concrete_ticket_cols = [c['name'] for c in inspector.get_columns('concrete_ticket')]

    if 'contract_id' not in concrete_ticket_cols:
        print("Adding contract_id to concrete_ticket...")
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE concrete_ticket ADD COLUMN contract_id INTEGER"))
            conn.commit()
        print("contract_id column added successfully!")
    else:
        print("contract_id column already exists")

    if 'material_id' not in concrete_ticket_cols:
        print("Adding material_id to concrete_ticket...")
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE concrete_ticket ADD COLUMN material_id INTEGER"))
            conn.commit()
        print("material_id column added successfully!")
    else:
        print("material_id column already exists")

    print("\nConcrete ticket columns:", [c['name'] for c in inspector.get_columns('concrete_ticket')])
