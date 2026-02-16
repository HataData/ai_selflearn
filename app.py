import os
import sqlite3
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.config["DATABASE"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shopping_list.db")


def get_db():
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shopping_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS category (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            quantity TEXT,
            notes TEXT,
            price REAL,
            checked INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (list_id) REFERENCES shopping_list(id),
            FOREIGN KEY (category_id) REFERENCES category(id)
        );
    """)
    cursor = conn.execute("SELECT COUNT(*) FROM category")
    if cursor.fetchone()[0] == 0:
        for name in ("Produce", "Dairy", "Bakery", "Frozen", "Other"):
            conn.execute("INSERT INTO category (name) VALUES (?)", (name,))
    cursor = conn.execute("SELECT COUNT(*) FROM shopping_list")
    if cursor.fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO shopping_list (name, slug) VALUES (?, ?)",
            ("Saturday", "saturday"),
        )
    conn.commit()
    conn.close()


_db_initialized = False


@app.before_request
def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


@app.route("/")
def index():
    return render_template("index.html")


# --- List API ---


@app.route("/api/lists", methods=["GET"])
def api_lists_get():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, slug FROM shopping_list ORDER BY id"
    ).fetchall()
    conn.close()
    return jsonify([{"id": r["id"], "name": r["name"], "slug": r["slug"]} for r in rows])


@app.route("/api/lists", methods=["POST"])
def api_lists_post():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    slug = name.lower().replace(" ", "-").replace("'", "")
    if not slug:
        slug = "list"
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO shopping_list (name, slug) VALUES (?, ?)",
            (name, slug),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, name, slug FROM shopping_list WHERE id = last_insert_rowid()"
        ).fetchone()
        conn.close()
        return jsonify({"id": row["id"], "name": row["name"], "slug": row["slug"]}), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "list with that name/slug already exists"}), 409


# --- Category API ---


@app.route("/api/categories", methods=["GET"])
def api_categories_get():
    conn = get_db()
    rows = conn.execute("SELECT id, name FROM category ORDER BY name").fetchall()
    conn.close()
    return jsonify([{"id": r["id"], "name": r["name"]} for r in rows])


# --- Item API ---


def _list_id_from_slug(slug):
    conn = get_db()
    row = conn.execute("SELECT id FROM shopping_list WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    return row["id"] if row else None


@app.route("/api/lists/<slug>/items", methods=["GET"])
def api_list_items_get(slug):
    list_id = _list_id_from_slug(slug)
    if list_id is None:
        return jsonify({"error": "list not found"}), 404
    conn = get_db()
    total_row = conn.execute(
        "SELECT COALESCE(SUM(price), 0) AS total FROM item WHERE list_id = ?",
        (list_id,),
    ).fetchone()
    total_spend = float(total_row["total"])
    rows = conn.execute(
        """SELECT i.id, i.list_id, i.category_id, i.name, i.quantity, i.notes,
                  i.price, i.checked, i.sort_order, c.name AS category_name
           FROM item i
           JOIN category c ON c.id = i.category_id
           WHERE i.list_id = ?
           ORDER BY i.sort_order, i.id""",
        (list_id,),
    ).fetchall()
    conn.close()
    items = [
        {
            "id": r["id"],
            "list_id": r["list_id"],
            "category_id": r["category_id"],
            "category_name": r["category_name"],
            "name": r["name"],
            "quantity": r["quantity"] or "",
            "notes": r["notes"] or "",
            "price": float(r["price"]) if r["price"] is not None else None,
            "checked": bool(r["checked"]),
            "sort_order": r["sort_order"],
        }
        for r in rows
    ]
    return jsonify({"items": items, "total_spend": round(total_spend, 2)})


@app.route("/api/lists/<slug>/items", methods=["POST"])
def api_list_items_post(slug):
    list_id = _list_id_from_slug(slug)
    if list_id is None:
        return jsonify({"error": "list not found"}), 404
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    category_id = data.get("category_id")
    if category_id is None:
        return jsonify({"error": "category_id is required"}), 400
    quantity = (data.get("quantity") or "").strip()
    notes = (data.get("notes") or "").strip()
    price = data.get("price")
    if price is not None:
        try:
            price = float(price)
            if price < 0:
                price = None
        except (TypeError, ValueError):
            price = None
    conn = get_db()
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next FROM item WHERE list_id = ?",
        (list_id,),
    ).fetchone()
    sort_order = max_order["next"]
    conn.execute(
        """INSERT INTO item (list_id, category_id, name, quantity, notes, price, checked, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
        (list_id, category_id, name, quantity or None, notes or None, price, sort_order),
    )
    conn.commit()
    row = conn.execute(
        "SELECT i.id, i.category_id, i.name, i.quantity, i.notes, i.price, i.checked, i.sort_order, c.name AS category_name "
        "FROM item i JOIN category c ON c.id = i.category_id WHERE i.id = last_insert_rowid()"
    ).fetchone()
    conn.close()
    return (
        jsonify(
            {
                "id": row["id"],
                "list_id": list_id,
                "category_id": row["category_id"],
                "category_name": row["category_name"],
                "name": row["name"],
                "quantity": row["quantity"] or "",
                "notes": row["notes"] or "",
                "price": float(row["price"]) if row["price"] is not None else None,
                "checked": False,
                "sort_order": row["sort_order"],
            }
        ),
        201,
    )


@app.route("/api/items/<int:item_id>", methods=["PATCH"])
def api_item_patch(item_id):
    data = request.get_json() or {}
    conn = get_db()
    row = conn.execute("SELECT id, list_id, category_id, name, quantity, notes, price, checked, sort_order FROM item WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "item not found"}), 404
    updates = []
    params = []
    for key in ("name", "quantity", "notes", "category_id"):
        if key in data:
            val = data[key]
            if key == "name":
                val = (val or "").strip() or row["name"]
            elif key in ("quantity", "notes"):
                val = (val or "").strip() if val is not None else row[key]
            updates.append(f"{key} = ?")
            params.append(val)
    if "price" in data:
        p = data["price"]
        if p is None:
            params.append(None)
        else:
            try:
                params.append(float(p) if float(p) >= 0 else None)
            except (TypeError, ValueError):
                params.append(row["price"])
        updates.append("price = ?")
    if "checked" in data:
        updates.append("checked = ?")
        params.append(1 if data["checked"] else 0)
    if not updates:
        conn.close()
        return jsonify({"error": "no fields to update"}), 400
    params.append(item_id)
    conn.execute(f"UPDATE item SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    row = conn.execute(
        "SELECT i.id, i.list_id, i.category_id, i.name, i.quantity, i.notes, i.price, i.checked, i.sort_order, c.name AS category_name "
        "FROM item i JOIN category c ON c.id = i.category_id WHERE i.id = ?",
        (item_id,),
    ).fetchone()
    conn.close()
    return jsonify(
        {
            "id": row["id"],
            "list_id": row["list_id"],
            "category_id": row["category_id"],
            "category_name": row["category_name"],
            "name": row["name"],
            "quantity": row["quantity"] or "",
            "notes": row["notes"] or "",
            "price": float(row["price"]) if row["price"] is not None else None,
            "checked": bool(row["checked"]),
            "sort_order": row["sort_order"],
        }
    )


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def api_item_delete(item_id):
    conn = get_db()
    cur = conn.execute("DELETE FROM item WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"error": "item not found"}), 404
    return jsonify({"ok": True}), 200


@app.route("/api/lists/<slug>/items/clear-completed", methods=["POST"])
def api_list_clear_completed(slug):
    list_id = _list_id_from_slug(slug)
    if list_id is None:
        return jsonify({"error": "list not found"}), 404
    conn = get_db()
    conn.execute("DELETE FROM item WHERE list_id = ? AND checked = 1", (list_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
