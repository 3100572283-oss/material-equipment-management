import sqlite3

conn = sqlite3.connect('material_mgmt.db')
cursor = conn.cursor()

try:
    cursor.execute('ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0')
    conn.commit()
    print('Column must_change_password added successfully')
except Exception as e:
    print(f'Error: {e}')

cursor.execute('PRAGMA table_info(users)')
for row in cursor.fetchall():
    print(row)

conn.close()
