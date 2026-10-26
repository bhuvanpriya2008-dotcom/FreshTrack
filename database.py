import sqlite3
from pathlib import Path
from datetime import datetime
try:
    from werkzeug.security import generate_password_hash
except ImportError:
    import hashlib, os, binascii
    def generate_password_hash(password, method="pbkdf2:sha256"):
        salt = binascii.hexlify(os.urandom(16)).decode("ascii")
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
        return f"pbkdf2:sha256:260000${salt}${binascii.hexlify(key).decode('ascii')}"

DATABASE = Path(__file__).resolve().parent / "freshtrack.db"


def create_database(db_path=DATABASE):
    connection = sqlite3.connect(db_path)

    try:
        with connection:
            # 1. Create users table
            connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # 2. Create food_items table with user_id foreign key
            connection.execute("""
                CREATE TABLE IF NOT EXISTS food_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    food_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    purchase_date TEXT NOT NULL,
                    storage TEXT NOT NULL,
                    user_id INTEGER REFERENCES users(id)
                )
            """)

            # 3. Create food_waste table with user_id foreign key
            connection.execute("""
                CREATE TABLE IF NOT EXISTS food_waste (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    food_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    waste_date TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    user_id INTEGER REFERENCES users(id)
                )
            """)

            # 4. Check & add user_id column to food_items if missing (idempotent migration)
            food_items_columns = [
                col[1] for col in connection.execute("PRAGMA table_info(food_items)").fetchall()
            ]
            if "user_id" not in food_items_columns:
                connection.execute("ALTER TABLE food_items ADD COLUMN user_id INTEGER REFERENCES users(id)")

            # 5. Check & add user_id column to food_waste if missing (idempotent migration)
            food_waste_columns = [
                col[1] for col in connection.execute("PRAGMA table_info(food_waste)").fetchall()
            ]
            if "user_id" not in food_waste_columns:
                connection.execute("ALTER TABLE food_waste ADD COLUMN user_id INTEGER REFERENCES users(id)")

            # 6. Ensure default demo user exists
            existing_demo = connection.execute(
                "SELECT id FROM users WHERE username = ?", ("demo",)
            ).fetchone()

            if existing_demo:
                demo_user_id = existing_demo[0]
            else:
                demo_created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                demo_password_hash = generate_password_hash("FreshTrack123!", method="pbkdf2:sha256")
                cursor = connection.execute("""
                    INSERT INTO users (username, email, password_hash, created_at)
                    VALUES (?, ?, ?, ?)
                """, ("demo", "demo@freshtrack.local", demo_password_hash, demo_created))
                demo_user_id = cursor.lastrowid

            # 7. Migrate all unassigned food items and waste records to the demo account
            connection.execute(
                "UPDATE food_items SET user_id = ? WHERE user_id IS NULL",
                (demo_user_id,)
            )
            connection.execute(
                "UPDATE food_waste SET user_id = ? WHERE user_id IS NULL",
                (demo_user_id,)
            )

    finally:
        connection.close()


if __name__ == "__main__":
    create_database()
    print("FreshTrack database schema migration completed successfully!")
