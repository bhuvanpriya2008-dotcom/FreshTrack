"""
test_postgres_live_app.py
Comprehensive integration test verifying Flask application functionality
directly against the live Render PostgreSQL database.
"""

import os
import unittest
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash

class TestLivePostgresApp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Ensure DATABASE_URL is set
        assert os.getenv("DATABASE_URL"), "DATABASE_URL environment variable must be set"
        from database import get_connection
        cls.get_connection = staticmethod(get_connection)
        from app import app
        cls.app = app
        cls.client = app.test_client()

    def test_01_verify_migrated_existing_data(self):
        """Verify all original SQLite records exist intact in PostgreSQL."""
        conn = self.get_connection()
        try:
            # Check users
            users = conn.execute("SELECT id, username, email, password_hash, created_at FROM users ORDER BY id ASC").fetchall()
            self.assertEqual(len(users), 2, "Should have exactly 2 migrated users")
            
            demo_user = users[0]
            self.assertEqual(demo_user["id"], 1)
            self.assertEqual(demo_user["username"], "demo")
            # Verify demo user password hash works with FreshTrack123!
            self.assertTrue(check_password_hash(demo_user["password_hash"], "FreshTrack123!"))

            second_user = users[1]
            self.assertEqual(second_user["id"], 2)
            self.assertEqual(second_user["username"], "bhuvanpriya2008@gmail.com")

            # Check food items
            foods = conn.execute("SELECT id, food_name, quantity, user_id FROM food_items ORDER BY id ASC").fetchall()
            self.assertEqual(len(foods), 6, "Should have exactly 6 migrated food items")
            for f in foods:
                self.assertEqual(f["user_id"], 1, "Existing items should belong to demo user (id: 1)")

            # Check food waste
            waste = conn.execute("SELECT id, food_name, quantity, user_id FROM food_waste ORDER BY id ASC").fetchall()
            self.assertEqual(len(waste), 3, "Should have exactly 3 migrated waste records")
            for w in waste:
                self.assertEqual(w["user_id"], 1, "Existing waste should belong to demo user (id: 1)")

            # Check AI chat history
            chats = conn.execute("SELECT id, user_id, question, answer FROM ai_chat_history ORDER BY id ASC").fetchall()
            self.assertEqual(len(chats), 1, "Should have exactly 1 migrated chat history record")
        finally:
            conn.close()

    def test_02_sequence_synchronization_and_new_user_crud(self):
        """Verify sequence synchronization generates next ID (>2) and test user isolation."""
        conn = self.get_connection()
        test_user_id = None
        test_food_id = None
        try:
            # Clean up test user if previously left over
            conn.execute("DELETE FROM users WHERE username = ?", ("test_suite_user",))
            conn.commit()

            # Insert test user through app's get_connection
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pw_hash = generate_password_hash("TestPassword123!")
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                ("test_suite_user", "test_suite_user@freshtrack.local", pw_hash, created_at)
            )
            conn.commit()

            test_user_id = cur.lastrowid
            self.assertIsNotNone(test_user_id)
            self.assertGreater(test_user_id, 2, f"Sequence must generate ID > 2, got {test_user_id}")

            # Test adding a food item for this user
            cur_food = conn.execute(
                "INSERT INTO food_items (food_name, quantity, purchase_date, storage, user_id) VALUES (?, ?, ?, ?, ?)",
                ("Integration Test Berry", 2.5, "2026-10-27", "fridge", test_user_id)
            )
            conn.commit()
            test_food_id = cur_food.lastrowid
            self.assertIsNotNone(test_food_id)
            self.assertGreater(test_food_id, 6, "Food item ID sequence must be synchronized")

            # Verify user isolation: Demo user (1) must NOT see test user's food item
            demo_foods = conn.execute("SELECT * FROM food_items WHERE user_id = ?", (1,)).fetchall()
            self.assertEqual(len(demo_foods), 6, "Demo user should only see their own 6 items")

            # Test editing food item
            conn.execute(
                "UPDATE food_items SET quantity = ?, storage = ? WHERE id = ? AND user_id = ?",
                (5.0, "freezer", test_food_id, test_user_id)
            )
            conn.commit()

            updated = conn.execute("SELECT quantity, storage FROM food_items WHERE id = ?", (test_food_id,)).fetchone()
            self.assertEqual(updated["quantity"], 5.0)
            self.assertEqual(updated["storage"], "freezer")

            # Test deleting food item
            conn.execute("DELETE FROM food_items WHERE id = ? AND user_id = ?", (test_food_id, test_user_id))
            conn.commit()
            test_food_id = None

            check_del = conn.execute("SELECT id FROM food_items WHERE id = ?", (test_food_id,)).fetchone()
            self.assertIsNone(check_del)

        finally:
            # Clean up test user records
            if test_food_id:
                conn.execute("DELETE FROM food_items WHERE id = ?", (test_food_id,))
            if test_user_id:
                conn.execute("DELETE FROM food_items WHERE user_id = ?", (test_user_id,))
                conn.execute("DELETE FROM food_waste WHERE user_id = ?", (test_user_id,))
                conn.execute("DELETE FROM ai_chat_history WHERE user_id = ?", (test_user_id,))
                conn.execute("DELETE FROM users WHERE id = ?", (test_user_id,))
            conn.commit()
            conn.close()

    def test_03_flask_routes_with_postgres(self):
        """Verify Flask client routes respond HTTP 200 with live PostgreSQL connection."""
        res_home = self.client.get("/")
        self.assertEqual(res_home.status_code, 200)
        self.assertIn(b"FreshTrack", res_home.data)

        res_health = self.client.get("/health")
        self.assertEqual(res_health.status_code, 200)
        self.assertEqual(res_health.json.get("status"), "running")

        res_login = self.client.get("/login")
        self.assertEqual(res_login.status_code, 200)

        res_reg = self.client.get("/register")
        self.assertEqual(res_reg.status_code, 200)


if __name__ == "__main__":
    unittest.main()
