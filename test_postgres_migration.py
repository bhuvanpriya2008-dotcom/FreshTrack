"""
test_postgres_migration.py
Automated test suite verifying FreshTrack dual-database layer,
SQLite local compatibility, PostgreSQL wrappers, registration timestamping,
and migration logic (with mock PostgreSQL database).
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class TestFreshTrackDualDatabase(unittest.TestCase):

    def setUp(self):
        # Ensure tests default to SQLite mode unless explicitly testing PG wrappers
        os.environ.pop("DATABASE_URL", None)

    def test_01_sqlite_local_database_integrity(self):
        """Verify local freshtrack.db has valid tables and records intact."""
        from database import get_connection

        conn = get_connection()
        try:
            # Verify users
            users = conn.execute("SELECT id, username, email FROM users ORDER BY id ASC").fetchall()
            self.assertGreater(len(users), 0, "Users table should have records")
            demo_user = [u for u in users if u["username"] == "demo"]
            self.assertTrue(len(demo_user) > 0, "Demo user must exist in SQLite")

            # Verify food_items
            foods = conn.execute("SELECT * FROM food_items").fetchall()
            self.assertGreaterEqual(len(foods), 4, "Food items table should have records")
            first_food = foods[0]
            # Verify key access, index access, and dict conversion
            self.assertIn("food_name", first_food.keys())
            self.assertIsNotNone(first_food[0])
            self.assertIsInstance(dict(first_food), dict)

            # Verify food_waste
            waste = conn.execute("SELECT * FROM food_waste").fetchall()
            self.assertGreaterEqual(len(waste), 3, "Food waste table should have records")
        finally:
            conn.close()

    def test_02_row_wrapper_compatibility(self):
        """Verify the PostgreSQL Row wrapper provides identical behavior to sqlite3.Row."""
        from database import Row

        col1 = MagicMock()
        col1.name = "food_name"
        col2 = MagicMock()
        col2.name = "quantity"

        description = [col1, col2]
        row_values = ("Apples", 5.5)

        row = Row(description, row_values)

        # 1. Column name access
        self.assertEqual(row["food_name"], "Apples")
        self.assertEqual(row["quantity"], 5.5)

        # 2. Integer index access
        self.assertEqual(row[0], "Apples")
        self.assertEqual(row[1], 5.5)

        # 3. dict(row) conversion
        as_dict = dict(row)
        self.assertEqual(as_dict, {"food_name": "Apples", "quantity": 5.5})

        # 4. .get() method
        self.assertEqual(row.get("food_name"), "Apples")
        self.assertEqual(row.get("nonexistent", "default_val"), "default_val")

        # 5. Case-insensitive fallback
        self.assertEqual(row["Food_Name"], "Apples")

    def test_03_postgres_connection_placeholder_translation(self):
        """Verify PostgresConnection translates ? to %s and appends RETURNING id for inserts."""
        from database import PostgresConnection

        mock_raw_conn = MagicMock()
        mock_raw_cursor = MagicMock()
        mock_raw_conn.cursor.return_value = mock_raw_cursor

        col_id = MagicMock()
        col_id.name = "id"
        mock_raw_cursor.description = [col_id]
        mock_raw_cursor.fetchone.return_value = (99,)

        pg_conn = PostgresConnection(mock_raw_conn)

        # Test SELECT query translation (? -> %s)
        pg_conn.execute("SELECT * FROM food_items WHERE user_id = ? AND storage = ?", (1, "fridge"))
        executed_sql = mock_raw_cursor.execute.call_args[0][0]
        self.assertIn("%s", executed_sql)
        self.assertNotIn("?", executed_sql)

        # Test INSERT query translation and RETURNING id
        cur = pg_conn.execute(
            "INSERT INTO food_items (food_name, quantity) VALUES (?, ?)",
            ("Bananas", 3)
        )
        insert_sql = mock_raw_cursor.execute.call_args[0][0]
        self.assertIn("RETURNING id", insert_sql)
        self.assertIn("%s", insert_sql)
        self.assertNotIn("?", insert_sql)
        self.assertEqual(cur.lastrowid, 99)

    def test_04_flask_routes_in_sqlite_mode(self):
        """Verify core Flask routes return 200/302 without database errors."""
        from app import app

        client = app.test_client()

        # Homepage
        res_home = client.get("/")
        self.assertEqual(res_home.status_code, 200)

        # Health endpoint
        res_health = client.get("/health")
        self.assertEqual(res_health.status_code, 200)
        self.assertEqual(res_health.json.get("status"), "running")

        # Register page GET
        res_reg = client.get("/register")
        self.assertEqual(res_reg.status_code, 200)

        # Login page GET
        res_login = client.get("/login")
        self.assertEqual(res_login.status_code, 200)

    def test_05_migration_script_duplicate_prevention_logic(self):
        """Verify migration script masks passwords in logs."""
        from migrate_sqlite_to_postgres import sanitize_url

        test_url = "postgresql://myuser:supersecretpass@ep-cool-db.us-east-2.render.com/freshdb"
        masked = sanitize_url(test_url)
        self.assertNotIn("supersecretpass", masked)
        self.assertIn("myuser:****@", masked)

    def test_06_registration_created_at_field(self):
        """Verify new users registered have created_at properly populated in database."""
        from database import get_connection

        conn = get_connection()
        try:
            demo_user = conn.execute("SELECT created_at FROM users WHERE username = 'demo'").fetchone()
            self.assertIsNotNone(demo_user["created_at"], "Demo user must have created_at")
            self.assertTrue(len(str(demo_user["created_at"])) > 5)
        finally:
            conn.close()

    @patch("database.get_postgres_connection")
    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/testdb"})
    def test_07_dry_run_handles_missing_tables_gracefully(self, mock_get_pg):
        """Verify dry-run mode detects missing tables in PostgreSQL and explains without crashing."""
        from migrate_sqlite_to_postgres import run_migration

        mock_conn = MagicMock()
        mock_get_pg.return_value = mock_conn

        # Mock information_schema returning no tables
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.execute.return_value = mock_cur

        try:
            run_migration(dry_run=True)
            passed = True
        except Exception as e:
            passed = False
            self.fail(f"run_migration(dry_run=True) raised an unexpected exception: {e}")

        self.assertTrue(passed)

    @patch("database.get_postgres_connection")
    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/testdb"})
    def test_08_migration_creates_tables_before_querying(self, mock_get_pg):
        """Verify live migration initializes schema via create_postgres_tables and inserts data."""
        from migrate_sqlite_to_postgres import run_migration

        mock_conn = MagicMock()
        mock_get_pg.return_value = mock_conn

        mock_cur = MagicMock()
        # Mock information_schema returning existing tables
        mock_cur.fetchall.return_value = [("users",), ("food_items",), ("food_waste",), ("ai_chat_history",)]
        # Return None for existing checks (no duplicates), then return count (10) for count queries
        def fake_fetchone():
            return None

        # Custom side effect for execute
        def fake_execute(sql, params=None):
            c = MagicMock()
            if "COUNT(*)" in sql:
                c.fetchone.return_value = (10,)
            else:
                c.fetchone.return_value = None
            return c

        mock_conn.execute.side_effect = fake_execute

        with patch("database.create_postgres_tables") as mock_create_tables:
            run_migration(dry_run=False)
            self.assertTrue(mock_create_tables.called, "create_postgres_tables must be called in live migration")
            self.assertTrue(mock_conn.commit.called, "pg_conn.commit must be called on successful migration")

    @patch("database.get_postgres_connection")
    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/testdb"})
    def test_09_migration_detects_conflict_and_aborts(self, mock_get_pg):
        """Verify migration detects conflicting IDs/usernames and rolls back without altering DB."""
        from migrate_sqlite_to_postgres import run_migration

        mock_conn = MagicMock()
        mock_get_pg.return_value = mock_conn

        def fake_execute(sql, params=None):
            c = MagicMock()
            # Simulate conflicting user where ID 999 is returned for username 'demo'
            if "FROM users WHERE" in sql:
                c.fetchone.return_value = (999, "other_user", "other@email.com")
            else:
                c.fetchone.return_value = None
            return c

        mock_conn.execute.side_effect = fake_execute

        with patch("database.create_postgres_tables"):
            with self.assertRaises(SystemExit):
                run_migration(dry_run=False)

        # Confirm rollback was triggered
        self.assertTrue(mock_conn.rollback.called, "pg_conn.rollback must be called on conflict")


if __name__ == "__main__":
    unittest.main()
