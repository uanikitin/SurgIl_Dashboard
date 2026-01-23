import psycopg2

conn = psycopg2.connect(
    "postgresql://telegram_events_db_user:NBBhfT7BgYyvq3yRmgnJ1UnVXKMkYCX7@dpg-d43l9pre5dus73a4241g-a.frankfurt-postgres.render.com/telegram_events_db"
)
cursor = conn.cursor()

# 1. Таблицы
cursor.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    ORDER BY table_name
""")
print("=== ТАБЛИЦЫ ===")
for table in cursor.fetchall():
    print(f"- {table[0]}")

# 2. Структура equipment
print("\n=== СТРУКТУРА EQUIPMENT ===")
cursor.execute("""
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns 
    WHERE table_name = 'equipment' 
    ORDER BY ordinal_position
""")
for col in cursor.fetchall():
    print(f"{col[0]} | {col[1]} | {'NULL' if col[2] == 'YES' else 'NOT NULL'} | {col[3]}")

# 3. Таблицы обслуживания
print("\n=== ТАБЛИЦЫ ОБСЛУЖИВАНИЯ ===")
cursor.execute("""
    SELECT DISTINCT table_name 
    FROM information_schema.columns 
    WHERE table_name LIKE '%maintenance%' OR table_name LIKE '%service%'
""")
for table in cursor.fetchall():
    print(f"\nТаблица: {table[0]}")
    cursor.execute(f"""
        SELECT column_name, data_type
        FROM information_schema.columns 
        WHERE table_name = '{table[0]}'
        ORDER BY ordinal_position
    """)
    for col in cursor.fetchall():
        print(f"  {col[0]}: {col[1]}")

conn.close()