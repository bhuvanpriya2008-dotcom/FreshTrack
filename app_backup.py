
from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import date, datetime
from pathlib import Path

app = Flask(__name__)
app.secret_key = "freshtrack-local-demo-key"

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "freshtrack.db"

# Approximate storage estimates in days.
# These are planning estimates, not food-safety guarantees.
FOOD_DATA = {
    "apple": {"room": 7, "fridge": 28, "freezer": 240},
    "banana": {"room": 5, "fridge": 7, "freezer": 60},
    "bread": {"room": 4, "fridge": 7, "freezer": 90},
    "milk": {"room": None, "fridge": 5, "freezer": 90},
    "carrot": {"room": None, "fridge": 21, "freezer": 240},
    "tomato": {"room": 5, "fridge": 7, "freezer": 120},
    "egg": {"room": None, "fridge": 28, "freezer": None},
    "potato": {"room": 30, "fridge": None, "freezer": 240},
    "onion": {"room": 30, "fridge": 30, "freezer": 180},
    "orange": {"room": 7, "fridge": 21, "freezer": 120},
    "grapes": {"room": 3, "fridge": 10, "freezer": 120},
    "strawberry": {"room": None, "fridge": 5, "freezer": 240},
    "mango": {"room": 5, "fridge": 7, "freezer": 180},
    "watermelon": {"room": 7, "fridge": 14, "freezer": 120},
    "cucumber": {"room": 5, "fridge": 7, "freezer": 120},
    "spinach": {"room": None, "fridge": 5, "freezer": 300},
    "broccoli": {"room": None, "fridge": 7, "freezer": 300},
    "cauliflower": {"room": None, "fridge": 7, "freezer": 300},
    "rice": {"room": 180, "fridge": None, "freezer": 180},
    "cooked rice": {"room": None, "fridge": 4, "freezer": 30},
    "chicken": {"room": None, "fridge": 2, "freezer": 270},
    "fish": {"room": None, "fridge": 2, "freezer": 180},
    "meat": {"room": None, "fridge": 3, "freezer": 180},
    "cheese": {"room": None, "fridge": 21, "freezer": 180},
    "yogurt": {"room": None, "fridge": 7, "freezer": 60},
    "butter": {"room": None, "fridge": 60, "freezer": 180},
    "lemon": {"room": 7, "fridge": 28, "freezer": 120},
    "papaya": {"room": 5, "fridge": 7, "freezer": 180},
    "pineapple": {"room": 3, "fridge": 5, "freezer": 180},
    "beans": {"room": None, "fridge": 7, "freezer": 240},
    "peas": {"room": None, "fridge": 5, "freezer": 240},
    "capsicum": {"room": 3, "fridge": 10, "freezer": 180},
    "garlic": {"room": 30, "fridge": 30, "freezer": 180},
    "ginger": {"room": 14, "fridge": 30, "freezer": 180},
}


def get_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_database():
    """Verify the original tables exist without changing the database."""
    conn = get_connection()

    try:
        tables = conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
        """).fetchall()

        table_names = {row["name"] for row in tables}

        for table in ("food_items", "food_waste"):
            if table not in table_names:
                raise RuntimeError(
                    f"Required table '{table}' is missing from {DATABASE}"
                )
    finally:
        conn.close()



def get_freshness(food_name, purchase_date, storage):
    food_key = food_name.strip().lower()
    storage_key = storage.strip().lower()

    food_info = FOOD_DATA.get(food_key)

    if not food_info or storage_key not in food_info:
        return "Storage guidance unavailable", "unknown", None

    estimated_days = food_info[storage_key]

    if estimated_days is None:
        if food_key == "milk" and storage_key == "room":
            return (
            "Safety warning: Milk is stored at room temperature. "
            "Check how long it has been unrefrigerated and follow "
            "the product's food-safety instructions.",
            "warning",
            0
        )
        return "No estimate available", "unknown", None

    try:
        purchased = datetime.strptime(
            purchase_date, "%Y-%m-%d"
        ).date()
    except (ValueError, TypeError):
        return "Invalid purchase date", "unknown", None

    days_passed = (date.today() - purchased).days
    remaining_days = estimated_days - days_passed

    if remaining_days <= 0:
        text = "Estimated storage duration has passed"
        status = "expired"
    elif remaining_days <= 3:
        text = f"Planning estimate: {remaining_days} day(s) remaining"
        status = "warning"
    else:
        text = f"Planning estimate: {remaining_days} day(s) remaining"
        status = "info"

    return text, status, remaining_days


def get_food_image(food_name):
    images = {
        "apple": "https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6",
        "banana": "https://images.unsplash.com/photo-1571771894821-ce9b6c11b08e",
        "carrot": "https://images.unsplash.com/photo-1445282768818-728615cc910a",
        "milk": "https://images.unsplash.com/photo-1563636619-e9143da7973b",
        "tomato": "https://images.unsplash.com/photo-1546094096-0df4bcaaa337",
        "bread": "https://images.unsplash.com/photo-1509440159596-0249088772ff",
        "orange": "https://images.unsplash.com/photo-1547514701-42782101795e",
        "mango": "https://images.unsplash.com/photo-1553279768-865429fa0078",
    }
    return images.get(food_name.strip().lower(), "")


@app.route("/")
def home():
    # Existing records shown here belong to demo user ID 1.
    user_id = 1
    conn = get_connection()

    try:
        foods = conn.execute(
            """
            SELECT *
            FROM food_items
            WHERE user_id = ?
            ORDER BY purchase_date ASC, id ASC
            """,
            (user_id,)
        ).fetchall()

        waste_records = conn.execute(
            """
            SELECT *
            FROM food_waste
            WHERE user_id = ?
            ORDER BY waste_date DESC, id DESC
            """,
            (user_id,)
        ).fetchall()

        total_waste = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)
            FROM food_waste
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()[0]

        waste_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM food_waste
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    food_details = []
    expiry_alerts = []

    for food in foods:
        freshness_text, freshness_status, remaining_days = get_freshness(
            food["food_name"],
            food["purchase_date"],
            food["storage"]
        )

        detail = {
            "id": food["id"],
            "name": food["food_name"],
            "quantity": food["quantity"],
            "purchase_date": food["purchase_date"],
            "storage": food["storage"],
            "freshness_text": freshness_text,
            "freshness_status": freshness_status,
            "remaining_days": remaining_days,
            "image_url": get_food_image(food["food_name"])
        }

        food_details.append(detail)

        if remaining_days is not None and remaining_days <= 3:
            expiry_alerts.append(detail)

    needs_attention_count = len(expiry_alerts)

    expiring_soon_count = sum(
        1 for food in food_details
        if food["freshness_status"] == "warning"
    )

    expired_count = sum(
        1 for food in food_details
        if food["freshness_status"] == "expired"
    )

    return render_template(
        "index.html",
        foods=foods,
        food_details=food_details,
        expiry_alerts=expiry_alerts,
        needs_attention_count=needs_attention_count,
        expiring_soon_count=expiring_soon_count,
        expired_count=expired_count,
        waste_records=waste_records,
        total_waste=total_waste,
        waste_count=waste_count,
        today=date.today().isoformat(),
        food_data=FOOD_DATA
    )


@app.route("/add_food", methods=["POST"])
def add_food():
    food_name = request.form.get("food_name", "").strip()
    quantity_text = request.form.get("quantity", "").strip()
    purchase_date = request.form.get("purchase_date", "").strip()
    storage = request.form.get("storage", "").strip().lower()

    if not food_name or not quantity_text or not purchase_date:
        flash("Please complete all food details.", "error")
        return redirect(url_for("home"))

    try:
        quantity = float(quantity_text)
        if quantity <= 0:
            raise ValueError
        datetime.strptime(purchase_date, "%Y-%m-%d")
    except ValueError:
        flash("Enter a valid quantity and purchase date.", "error")
        return redirect(url_for("home"))

    if storage not in ("room", "fridge", "freezer"):
        flash("Please select a valid storage type.", "error")
        return redirect(url_for("home"))

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO food_items
                (food_name, quantity, purchase_date, storage, user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (food_name, quantity, purchase_date, storage, 1)
        )
        conn.commit()
    finally:
        conn.close()

    flash("Food item added successfully.", "success")
    return redirect(url_for("home"))


@app.route("/delete_food/<int:food_id>", methods=["POST"])
def delete_food(food_id):
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM food_items WHERE id = ? AND user_id = ?",
            (food_id, 1)
        )
        conn.commit()
    finally:
        conn.close()

    flash("Food item deleted.", "success")
    return redirect(url_for("home"))


@app.route("/log_waste", methods=["POST"])
def log_waste():
    food_name = request.form.get("food_name", "").strip()
    quantity_text = request.form.get("quantity", "").strip()
    reason = request.form.get("reason", "").strip()
    waste_date = request.form.get("waste_date", "").strip()

    if not food_name or not quantity_text or not reason or not waste_date:
        flash("Please complete all waste record fields.", "error")
        return redirect(url_for("home"))

    try:
        quantity = float(quantity_text)
        if quantity <= 0:
            raise ValueError
        datetime.strptime(waste_date, "%Y-%m-%d")
    except ValueError:
        flash("Enter a valid quantity and waste date.", "error")
        return redirect(url_for("home"))

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO food_waste
                (food_name, quantity, waste_date, reason, user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (food_name, quantity, waste_date, reason, 1)
        )
        conn.commit()
    finally:
        conn.close()

    flash("Waste record saved successfully.", "success")
    return redirect(url_for("home"))


@app.route("/delete_waste/<int:waste_id>", methods=["POST"])
def delete_waste(waste_id):
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM food_waste WHERE id = ? AND user_id = ?",
            (waste_id, 1)
        )
        conn.commit()
    finally:
        conn.close()

    flash("Waste record deleted.", "success")
    return redirect(url_for("home"))


@app.route("/health")
def health():
    return {
        "status": "running",
        "application": "FreshTrack"
    }


create_database()

if __name__ == "__main__":
    app.run(debug=True)
