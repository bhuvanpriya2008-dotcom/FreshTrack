import os
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    from werkzeug.security import generate_password_hash
except ImportError:
    import binascii
    import hashlib

    def generate_password_hash(password, method="pbkdf2:sha256"):
        salt = binascii.hexlify(os.urandom(16)).decode("ascii")
        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000
        )
        return f"pbkdf2:sha256:260000${salt}${binascii.hexlify(key).decode('ascii')}"

DATABASE = Path(__file__).resolve().parent / "freshtrack.db"


class Row(dict):
    """Row wrapper supporting column-name lookup, integer indexing, and dict conversion."""

    def __init__(self, description, values):
        self._keys = [
            desc.name if hasattr(desc, "name") else desc[0]
            for desc in description
        ]
        super().__init__(zip(self._keys, values))
        self._values = tuple(values)

    def __getitem__(self, item):
        if isinstance(item, (int, slice)):
            return self._values[item]
        try:
            return super().__getitem__(item)
        except KeyError:
            if isinstance(item, str):
                item_lower = item.lower()
                for k, v in self.items():
                    if k.lower() == item_lower:
                        return v
            raise

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class PostgresCursor:
    def __init__(self, raw_cursor, lastrowid=None, prefetched_row=None):
        self._cursor = raw_cursor
        self.lastrowid = lastrowid
        self._prefetched = prefetched_row

    def fetchone(self):
        if self._prefetched is not None:
            row = self._prefetched
            self._prefetched = None
            if self._cursor.description:
                return Row(self._cursor.description, row)
            return row
        if not self._cursor.description:
            return None
        row = self._cursor.fetchone()
        if row is None:
            return None
        return Row(self._cursor.description, row)

    def fetchall(self):
        rows = []
        if self._prefetched is not None:
            if self._cursor.description:
                rows.append(Row(self._cursor.description, self._prefetched))
            else:
                rows.append(self._prefetched)
            self._prefetched = None
        if not self._cursor.description:
            return rows
        desc = self._cursor.description
        rows.extend(Row(desc, r) for r in self._cursor.fetchall())
        return rows

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row


class PostgresConnection:
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._active_cursors = []

    def execute(self, sql, params=None):
        sql_pg = sql.replace("?", "%s")
        trimmed = sql_pg.strip()
        is_insert = trimmed.upper().startswith("INSERT INTO")
        has_returning = "RETURNING" in trimmed.upper()

        if is_insert and not has_returning:
            sql_pg = f"{trimmed.rstrip(';')} RETURNING id"

        cur = self._conn.cursor()
        self._active_cursors.append(cur)

        if params is None:
            cur.execute(sql_pg)
        else:
            cur.execute(sql_pg, params)

        lastrowid = None
        prefetched = None
        if is_insert:
            try:
                row = cur.fetchone()
                if row:
                    lastrowid = row[0]
                    prefetched = row
            except Exception:
                pass

        return PostgresCursor(
            cur, lastrowid=lastrowid, prefetched_row=prefetched
        )

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        for cur in self._active_cursors:
            try:
                cur.close()
            except Exception:
                pass
        self._active_cursors.clear()
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()


def get_sqlite_connection(db_path=DATABASE):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_postgres_connection(database_url):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "DATABASE_URL is configured, but 'psycopg' is not installed. "
            "Please install dependencies with: pip install -r requirements.txt"
        ) from exc

    normalized_url = database_url
    if normalized_url.startswith("postgres://"):
        normalized_url = "postgresql://" + normalized_url[len("postgres://"):]

    raw_conn = psycopg.connect(normalized_url)
    return PostgresConnection(raw_conn)


def get_connection():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return get_postgres_connection(database_url)
    return get_sqlite_connection()


def create_postgres_tables(conn_or_url):
    """Create PostgreSQL tables without inserting seed data."""
    is_url = isinstance(conn_or_url, str)
    conn = get_postgres_connection(conn_or_url) if is_url else conn_or_url
    try:
        # 1. Users table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. Food items table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_items (
                id SERIAL PRIMARY KEY,
                food_name VARCHAR(255) NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                purchase_date VARCHAR(50) NOT NULL,
                storage VARCHAR(50) NOT NULL,
                user_id INTEGER REFERENCES users(id)
            )
        """)

        # 3. Food waste table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_waste (
                id SERIAL PRIMARY KEY,
                food_name VARCHAR(255) NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                waste_date VARCHAR(50) NOT NULL,
                reason TEXT NOT NULL,
                user_id INTEGER REFERENCES users(id)
            )
        """)

        # 4. AI chat history table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        if is_url:
            conn.close()


def create_postgres_database(database_url):
    conn = get_postgres_connection(database_url)
    try:
        create_postgres_tables(conn)

        # 5. Idempotent default demo user
        existing_demo = conn.execute(
            "SELECT id FROM users WHERE username = ?", ("demo",)
        ).fetchone()

        if existing_demo:
            demo_user_id = existing_demo[0]
        else:
            demo_created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            demo_password_hash = generate_password_hash(
                "FreshTrack123!", method="pbkdf2:sha256"
            )
            cursor = conn.execute("""
                INSERT INTO users (username, email, password_hash, created_at)
                VALUES (?, ?, ?, ?)
            """, ("demo", "demo@freshtrack.local", demo_password_hash, demo_created))
            demo_user_id = cursor.lastrowid
            if not demo_user_id:
                demo_row = conn.execute("SELECT id FROM users WHERE username = ?", ("demo",)).fetchone()
                demo_user_id = demo_row[0] if demo_row else 1
            conn.commit()

        # 6. Migrate any unassigned records to demo user
        conn.execute(
            "UPDATE food_items SET user_id = ? WHERE user_id IS NULL",
            (demo_user_id,)
        )
        conn.execute(
            "UPDATE food_waste SET user_id = ? WHERE user_id IS NULL",
            (demo_user_id,)
        )
        conn.commit()
    finally:
        conn.close()


def create_sqlite_database(db_path=DATABASE):
    connection = sqlite3.connect(db_path)
    try:
        with connection:
            # 1. Users table
            connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Food items table with user_id foreign key
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

            # 3. Food waste table with user_id foreign key
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

            # 4. AI chat history table
            connection.execute("""
                CREATE TABLE IF NOT EXISTS ai_chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 5. Check & add user_id column to food_items if missing (idempotent migration)
            food_items_columns = [
                col[1]
                for col in connection.execute("PRAGMA table_info(food_items)").fetchall()
            ]
            if "user_id" not in food_items_columns:
                connection.execute(
                    "ALTER TABLE food_items ADD COLUMN user_id INTEGER REFERENCES users(id)"
                )

            # 6. Check & add user_id column to food_waste if missing (idempotent migration)
            food_waste_columns = [
                col[1]
                for col in connection.execute("PRAGMA table_info(food_waste)").fetchall()
            ]
            if "user_id" not in food_waste_columns:
                connection.execute(
                    "ALTER TABLE food_waste ADD COLUMN user_id INTEGER REFERENCES users(id)"
                )

            # 7. Ensure default demo user exists
            existing_demo = connection.execute(
                "SELECT id FROM users WHERE username = ?", ("demo",)
            ).fetchone()

            if existing_demo:
                demo_user_id = existing_demo[0]
            else:
                demo_created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                demo_password_hash = generate_password_hash(
                    "FreshTrack123!", method="pbkdf2:sha256"
                )
                cursor = connection.execute("""
                    INSERT INTO users (username, email, password_hash, created_at)
                    VALUES (?, ?, ?, ?)
                """, ("demo", "demo@freshtrack.local", demo_password_hash, demo_created))
                demo_user_id = cursor.lastrowid

            # 8. Migrate all unassigned food items and waste records to the demo account
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


def create_database(db_path=DATABASE):
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        create_postgres_database(database_url)
    else:
        create_sqlite_database(db_path)


if __name__ == "__main__":
    create_database()
    print("FreshTrack database schema migration completed successfully!")
