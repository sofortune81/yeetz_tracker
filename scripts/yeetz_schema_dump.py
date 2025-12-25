# --- extract_db_context.py ---
import psycopg2
import os
from dotenv import load_dotenv
from pathlib import Path

# --- CONFIGURATION ---
load_dotenv()
DB_CONNECTION = os.getenv("SUPABASE_DB_URL")
OUTPUT_FILE = Path("db_snapshot.sql")


def extract_snapshot():
    if not DB_CONNECTION:
        print("❌ Error: SUPABASE_DB_URL not found in .env")
        return

    conn = None
    try:
        conn = psycopg2.connect(DB_CONNECTION)
        cursor = conn.cursor()

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("-- 🛡️ YEETZ TRACKER DATABASE SNAPSHOT\n\n")

            # 1. SCHEMA DUMP (Structure)
            f.write("-" * 30 + "\n-- 1. TABLE STRUCTURES --\n" + "-" * 30 + "\n")
            cursor.execute("""
                           SELECT table_name, column_name, data_type, column_default
                           FROM information_schema.columns
                           WHERE table_schema = 'public'
                           ORDER BY table_name, ordinal_position
                           """)

            current_table = ""
            for table_name, col_name, dtype, default in cursor.fetchall():
                if table_name != current_table:
                    if current_table != "": f.write(");\n\n")
                    f.write(f"CREATE TABLE {table_name} (\n")
                    current_table = table_name
                else:
                    f.write(",\n")
                f.write(f"    {col_name} {dtype} DEFAULT {default}")
            f.write("\n);\n\n")

            # 2. DATA DUMP (The actual values causing the errors)
            f.write("-" * 30 + "\n-- 2. TABLE DATA (whale_alerts) --\n" + "-" * 30 + "\n")
            cursor.execute("SELECT * FROM whale_alerts")
            rows = cursor.fetchall()
            col_names = [desc[0] for desc in cursor.description]

            for row in rows:
                values = []
                for val in row:
                    if val is None:
                        values.append("NULL")
                    elif isinstance(val, (int, float)):
                        values.append(str(val))
                    else:
                        values.append(f"'{str(val).replace("'", "''")}'")

                f.write(f"INSERT INTO whale_alerts ({', '.join(col_names)}) VALUES ({', '.join(values)});\n")

        print(f"✅ Success! Please share {OUTPUT_FILE} with me.")

    except Exception as e:
        print(f"🔥 Error: {e}")
    finally:
        if conn: conn.close()


if __name__ == "__main__":
    extract_snapshot()