from __future__ import annotations

from nimo_shop.db import Database
from nimo_shop.money import normalize_currency


class CatalogService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_category(self, name: str, sort_order: int = 100) -> int:
        if not name.strip():
            raise ValueError("category name is required")
        with self.db.transaction() as conn:
            cur = conn.execute("INSERT INTO categories(name, sort_order) VALUES(?,?)", (name.strip(), sort_order))
            return int(cur.lastrowid)

    def add_product(
        self,
        *,
        category_id: int | None,
        name: str,
        description: str,
        currency: str,
        price_minor: int,
        warranty_text: str = "",
        cost_minor: int = 0,
    ) -> int:
        if not name.strip():
            raise ValueError("product name is required")
        if price_minor < 0 or cost_minor < 0:
            raise ValueError("price/cost cannot be negative")
        cur = normalize_currency(currency)
        with self.db.transaction() as conn:
            c = conn.execute(
                """
                INSERT INTO products(category_id, name, description, currency, price_minor, warranty_text, cost_minor)
                VALUES(?,?,?,?,?,?,?)
                """,
                (category_id, name.strip(), description.strip(), cur, price_minor, warranty_text.strip(), cost_minor),
            )
            return int(c.lastrowid)

    def add_stock(self, product_id: int, contents: list[str]) -> int:
        clean = list(dict.fromkeys(x.strip() for x in contents if x and x.strip()))
        if not clean:
            raise ValueError("stock content is required")
        with self.db.transaction() as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO stock_items(product_id, content, status) VALUES(?,?, 'available')",
                [(product_id, item) for item in clean],
            )
            return conn.total_changes - before

    def list_categories(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM categories WHERE is_active=1 ORDER BY sort_order, id")]

    def list_products(self, category_id: int | None = None) -> list[dict]:
        sql = """
            SELECT p.*, COUNT(CASE WHEN s.status='available' THEN 1 END) AS available_stock
            FROM products p
            LEFT JOIN stock_items s ON s.product_id=p.id
            WHERE p.is_active=1
        """
        params: list[object] = []
        if category_id is not None:
            sql += " AND p.category_id=?"
            params.append(category_id)
        sql += " GROUP BY p.id ORDER BY p.id DESC"
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def stock_summary(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT p.id AS product_id, p.name,
                       SUM(CASE WHEN s.status='available' THEN 1 ELSE 0 END) AS available,
                       SUM(CASE WHEN s.status='reserved' THEN 1 ELSE 0 END) AS reserved,
                       SUM(CASE WHEN s.status='sold' THEN 1 ELSE 0 END) AS sold
                FROM products p LEFT JOIN stock_items s ON s.product_id=p.id
                GROUP BY p.id ORDER BY p.id DESC
                """
            )]
