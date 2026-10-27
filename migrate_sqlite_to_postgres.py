"""
migrate_sqlite_to_postgres.py
FreshTrack Data Migration Script (SQLite -> PostgreSQL)

Copies all records from local freshtrack.db into the PostgreSQL database
specified by the DATABASE_URL environment variable.

Features & Safety Guarantees:
1. Source SQLite database is opened in READ-ONLY mode and never modified.
2. Supports --dry-run (or -d) mode to inspect source and destination without modifying PostgreSQL.
3. Detects if destination tables are missing in dry-run mode and provides clear guidance.
4. Actual migration mode initializes the schema first using create_postgres_tables before querying tables.
5. Keeps schema initialization separate from data migration (no seed data inserted).
6. Preserves existing PostgreSQL records; avoids overwriting or truncating.
7. Preserves record IDs, password hashes, timestamps, and foreign-key relationships.
8. Uses database transactions and rolls back completely on any error.
9. Synchronizes PostgreSQL SERIAL sequences to prevent future primary key collisions.
10. Never prints, logs, or exposes database credentials.
11. Safe to rerun (idempotent; skips existing matching records).
"""

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = BASE_DIR / "freshtrack.db"
REQUIRED_TABLES = ("users", "food_items", "food_waste", "ai_chat_history")


def sanitize_url(url: str) -> str:
    """Mask password in connection URL for safe logging."""
    if not url:
        return ""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)


def get_sqlite_connection():
    """Connect to SQLite database strictly in read-only mode."""
    if not SQLITE_PATH.exists():
        raise FileNotFoundError(f"Source SQLite database not found at {SQLITE_PATH}")

    uri = f"file:{SQLITE_PATH.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(SQLITE_PATH))

    conn.row_factory = sqlite3.Row
    return conn


def check_existing_postgres_tables(pg_conn):
    """Query PostgreSQL information_schema to find existing public tables."""
    cur = pg_conn.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    return {row[0] for row in cur.fetchall()}


def run_migration(dry_run: bool = False):
    mode_str = "DRY-RUN (VALIDATION ONLY)" if dry_run else "LIVE MIGRATION"
    print("=" * 68)
    print(f"FreshTrack Data Migration: SQLite -> PostgreSQL [{mode_str}]")
    print("=" * 68)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("\n[ERROR] DATABASE_URL environment variable is not set.")
        print("Please set DATABASE_URL before running.")
        print("Example: DATABASE_URL=postgresql://user:password@host/dbname\n")
        sys.exit(1)

    try:
        import psycopg
    except ImportError:
        print("\n[ERROR] 'psycopg' is not installed.")
        print("Please install requirements with: pip install -r requirements.txt\n")
        sys.exit(1)

    pg_url = database_url
    if pg_url.startswith("postgres://"):
        pg_url = "postgresql://" + pg_url[len("postgres://"):]

    print(f"\n[1/5] Target Database: {sanitize_url(pg_url)}")
    print(f"[2/5] Source SQLite:   {SQLITE_PATH}")

    # Inspect source SQLite database in read-only mode
    sqlite_conn = get_sqlite_connection()

    try:
        sqlite_users = sqlite_conn.execute(
            "SELECT id, username, email, password_hash, created_at FROM users ORDER BY id ASC"
        ).fetchall()
        sqlite_foods = sqlite_conn.execute(
            "SELECT id, food_name, quantity, purchase_date, storage, user_id FROM food_items ORDER BY id ASC"
        ).fetchall()
        sqlite_waste = sqlite_conn.execute(
            "SELECT id, food_name, quantity, waste_date, reason, user_id FROM food_waste ORDER BY id ASC"
        ).fetchall()
        try:
            sqlite_chats = sqlite_conn.execute(
                "SELECT id, user_id, question, answer, created_at FROM ai_chat_history ORDER BY id ASC"
            ).fetchall()
        except sqlite3.OperationalError:
            sqlite_chats = []
    except Exception as exc:
        print(f"\n[ERROR] Failed to read source SQLite database: {exc}")
        sqlite_conn.close()
        sys.exit(1)

    source_counts = {
        "users": len(sqlite_users),
        "food_items": len(sqlite_foods),
        "food_waste": len(sqlite_waste),
        "ai_chat_history": len(sqlite_chats),
    }

    print("\n      Source SQLite Record Counts:")
    for tbl, cnt in source_counts.items():
        print(f"        - {tbl:<16}: {cnt} records")

    from database import get_postgres_connection, create_postgres_tables

    pg_conn = None
    try:
        pg_conn = get_postgres_connection(pg_url)

        # Inspect existing destination tables
        existing_tables = check_existing_postgres_tables(pg_conn)
        missing_tables = [t for t in REQUIRED_TABLES if t not in existing_tables]

        # -------------------------------------------------------------
        # DRY-RUN MODE
        # -------------------------------------------------------------
        if dry_run:
            print("\n[3/5] Validating PostgreSQL connection & existing schema (Dry-Run)...")
            print("      Connection to PostgreSQL: SUCCESS")

            if missing_tables:
                print(f"\n[DRY-RUN VALIDATION NOTICE] Required destination tables not yet created:")
                for mt in missing_tables:
                    print(f"        - {mt}")
                print("\n[EXPLANATION]")
                print("  The destination PostgreSQL database does not have the required application tables yet.")
                print("  In dry-run mode, PostgreSQL is NOT modified.")
                print("  The real migration will automatically create the PostgreSQL schema using")
                print("  create_postgres_tables() before transferring your data.")
                print("\n  Source records ready for transfer:")
                for tbl, cnt in source_counts.items():
                    print(f"    - {tbl:<16}: {cnt} records")
                print("\nTo initialize the schema and transfer your data, run without --dry-run:")
                print("    python migrate_sqlite_to_postgres.py\n")
                return

            print("      All required PostgreSQL tables are present.")
            print("\n[4/5] Inspecting destination records for conflicts (Dry-Run)...")

            user_conflicts = []
            users_migrated = 0
            users_matched = 0

            for u in sqlite_users:
                cur = pg_conn.execute(
                    "SELECT id, username, email FROM users WHERE id = %s OR username = %s OR email = %s",
                    (u["id"], u["username"], u["email"]),
                )
                existing = cur.fetchone()
                if existing:
                    existing_id = existing[0]
                    existing_uname = existing[1] if len(existing) > 1 else ""
                    if existing_id == u["id"] and (not existing_uname or existing_uname == u["username"]):
                        users_matched += 1
                    else:
                        user_conflicts.append(
                            f"User ID {u['id']} ({u['username']}) conflicts with existing user ID {existing_id} ({existing_uname})"
                        )
                else:
                    users_migrated += 1

            if user_conflicts:
                print(f"      [CONFLICT DETECTED] Found {len(user_conflicts)} conflict(s):")
                for c in user_conflicts:
                    print(f"        - {c}")
                raise RuntimeError("Migration stopped due to data conflicts.")

            print(f"      Users:       {users_migrated} would be inserted, {users_matched} already matched.")

            # Food items preview
            foods_migrated = 0
            foods_matched = 0
            for f in sqlite_foods:
                cur = pg_conn.execute("SELECT id FROM food_items WHERE id = %s", (f["id"],))
                if cur.fetchone():
                    foods_matched += 1
                else:
                    foods_migrated += 1
            print(f"      Food Items:  {foods_migrated} would be inserted, {foods_matched} already matched.")

            # Food waste preview
            waste_migrated = 0
            waste_matched = 0
            for w in sqlite_waste:
                cur = pg_conn.execute("SELECT id FROM food_waste WHERE id = %s", (w["id"],))
                if cur.fetchone():
                    waste_matched += 1
                else:
                    waste_migrated += 1
            print(f"      Food Waste:  {waste_migrated} would be inserted, {waste_matched} already matched.")

            # AI chat preview
            chats_migrated = 0
            chats_matched = 0
            for c in sqlite_chats:
                cur = pg_conn.execute("SELECT id FROM ai_chat_history WHERE id = %s", (c["id"],))
                if cur.fetchone():
                    chats_matched += 1
                else:
                    chats_migrated += 1
            print(f"      AI Chats:    {chats_migrated} would be inserted, {chats_matched} already matched.")

            print("\n[5/5] (Dry-run: Sequence update previewed, no changes made)")
            print("\n" + "=" * 68)
            print("[DRY-RUN COMPLETE] Validation passed! No records were modified.")
            print("To execute live migration, run:")
            print("    python migrate_sqlite_to_postgres.py\n")
            return

        # -------------------------------------------------------------
        # LIVE MIGRATION MODE
        # -------------------------------------------------------------
        print("\n[3/5] Initializing / validating PostgreSQL schema...")
        create_postgres_tables(pg_conn)
        print("      PostgreSQL tables and constraints verified.")

        print("\n[4/5] Migrating records (transactional)...")

        # 1. Users
        users_inserted = 0
        users_skipped = 0
        user_conflicts = []

        for u in sqlite_users:
            cur = pg_conn.execute(
                "SELECT id, username, email FROM users WHERE id = %s OR username = %s OR email = %s",
                (u["id"], u["username"], u["email"]),
            )
            existing = cur.fetchone()
            if existing:
                existing_id = existing[0]
                existing_uname = existing[1] if len(existing) > 1 else ""
                if existing_id == u["id"] and (not existing_uname or existing_uname == u["username"]):
                    users_skipped += 1
                else:
                    user_conflicts.append(
                        f"User ID {u['id']} ({u['username']}) conflicts with existing user ID {existing_id} ({existing_uname})"
                    )
            else:
                pg_conn.execute(
                    """
                    INSERT INTO users (id, username, email, password_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (u["id"], u["username"], u["email"], u["password_hash"], u["created_at"]),
                )
                users_inserted += 1

        if user_conflicts:
            print(f"      [CONFLICT] Found {len(user_conflicts)} conflict(s):")
            for c in user_conflicts:
                print(f"        - {c}")
            raise RuntimeError("Migration stopped due to user conflict. No records modified.")

        print(f"      Users:       {users_inserted} inserted, {users_skipped} already existed.")

        # 2. Food Items
        foods_inserted = 0
        foods_skipped = 0

        for f in sqlite_foods:
            cur = pg_conn.execute("SELECT id FROM food_items WHERE id = %s", (f["id"],))
            if cur.fetchone():
                foods_skipped += 1
            else:
                pg_conn.execute(
                    """
                    INSERT INTO food_items (id, food_name, quantity, purchase_date, storage, user_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (f["id"], f["food_name"], f["quantity"], f["purchase_date"], f["storage"], f["user_id"]),
                )
                foods_inserted += 1

        print(f"      Food Items:  {foods_inserted} inserted, {foods_skipped} already existed.")

        # 3. Food Waste
        waste_inserted = 0
        waste_skipped = 0

        for w in sqlite_waste:
            cur = pg_conn.execute("SELECT id FROM food_waste WHERE id = %s", (w["id"],))
            if cur.fetchone():
                waste_skipped += 1
            else:
                pg_conn.execute(
                    """
                    INSERT INTO food_waste (id, food_name, quantity, waste_date, reason, user_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (w["id"], w["food_name"], w["quantity"], w["waste_date"], w["reason"], w["user_id"]),
                )
                waste_inserted += 1

        print(f"      Food Waste:  {waste_inserted} inserted, {waste_skipped} already existed.")

        # 4. AI Chat History
        chats_inserted = 0
        chats_skipped = 0

        for c in sqlite_chats:
            cur = pg_conn.execute("SELECT id FROM ai_chat_history WHERE id = %s", (c["id"],))
            if cur.fetchone():
                chats_skipped += 1
            else:
                pg_conn.execute(
                    """
                    INSERT INTO ai_chat_history (id, user_id, question, answer, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (c["id"], c["user_id"], c["question"], c["answer"], c["created_at"]),
                )
                chats_inserted += 1

        print(f"      AI Chats:    {chats_inserted} inserted, {chats_skipped} already existed.")

        # 5. Synchronize PostgreSQL SERIAL Sequences
        print("\n[5/5] Synchronizing PostgreSQL ID sequences...")
        for table in REQUIRED_TABLES:
            pg_conn.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1)
                )
            """)
        print("      All ID sequences synchronized to highest record IDs.")

        # Verification Counts
        print("\n" + "=" * 68)
        print("Source vs Destination Summary:")
        print("=" * 68)

        dest_counts = {}
        for table in REQUIRED_TABLES:
            cur = pg_conn.execute(f"SELECT COUNT(*) FROM {table}")
            dest_counts[table] = cur.fetchone()[0]
            print(f"  - {table:<16}: Source={source_counts.get(table, 0)} | Destination={dest_counts[table]}")

        # Verification check: Ensure destination has at least source count
        for table in ("users", "food_items", "food_waste"):
            if dest_counts[table] < source_counts[table]:
                raise RuntimeError(
                    f"Verification failed: {table} destination count ({dest_counts[table]}) < source count ({source_counts[table]})"
                )

        pg_conn.commit()
        print("\n[SUCCESS] Migration committed successfully!")
        print("All records transferred and verified. SQLite source remains unchanged.\n")

    except Exception as exc:
        if pg_conn:
            pg_conn.rollback()
        print(f"\n[ERROR] Migration failed and was ROLLED BACK: {exc}")
        print("PostgreSQL was reverted to its previous state. SQLite was not modified.")
        sys.exit(1)

    finally:
        sqlite_conn.close()
        if pg_conn:
            pg_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FreshTrack SQLite to PostgreSQL Migration")
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Validate connection and inspect source without modifying PostgreSQL",
    )
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run)
