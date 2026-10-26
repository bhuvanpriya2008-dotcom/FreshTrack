import sqlite3

conn = sqlite3.connect("freshtrack.db")
conn.row_factory = sqlite3.Row

for table in ["users", "food_items", "food_waste"]:
    print(f"\n--- {table} ---")
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()

    for row in rows:
        data = dict(row)

        # Do not print password hashes
        if table == "users":
            data.pop("password_hash", None)

        print(data)

conn.close()