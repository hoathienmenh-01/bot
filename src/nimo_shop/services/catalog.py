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
        stock_format: str = "auto",
        stock_format_labels: str = "",
        stock_format_example: str = "",
        delivery_format: str = "auto",
        product_icon: str = "",
        product_custom_emoji_id: str = "",
        product_image_path: str = "",
        product_image_file_id: str = "",
        product_short_description: str = "",
        product_long_description: str = "",
    ) -> int:
        if not name.strip():
            raise ValueError("product name is required")
        if price_minor < 0 or cost_minor < 0:
            raise ValueError("price/cost cannot be negative")
        cur = normalize_currency(currency)
        with self.db.transaction() as conn:
            c = conn.execute(
                """
                INSERT INTO products(category_id, name, description, currency, price_minor, warranty_text, cost_minor, stock_format, stock_format_labels, stock_format_example, delivery_format, product_icon, product_custom_emoji_id, product_image_path, product_image_file_id, product_short_description, product_long_description)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (category_id, name.strip(), description.strip(), cur, price_minor, warranty_text.strip(), cost_minor, stock_format.strip() or "auto", stock_format_labels.strip(), stock_format_example.strip(), delivery_format.strip() or "auto", product_icon.strip(), product_custom_emoji_id.strip(), product_image_path.strip(), product_image_file_id.strip(), product_short_description.strip(), product_long_description.strip()),
            )
            return int(c.lastrowid)

    def add_stock(self, product_id: int, contents: list[str]) -> int:
        # Stock is money-sensitive: do not silently skip duplicate keys/accounts.
        # If admin pasted a duplicate line, fail fast with a clear error so they
        # can fix the inventory input instead of assuming every line was imported.
        clean = [x.strip() for x in contents if x and x.strip()]
        if not clean:
            raise ValueError("stock content is required")
        seen: set[str] = set()
        duplicates_in_input: list[str] = []
        for item in clean:
            if item in seen and item not in duplicates_in_input:
                duplicates_in_input.append(item)
            seen.add(item)
        if duplicates_in_input:
            sample = ", ".join(duplicates_in_input[:3])
            raise ValueError(f"Dòng nhập kho bị trùng trong cùng sản phẩm: {sample}. Hãy xóa/sửa dòng trùng rồi nhập lại.")
        with self.db.transaction() as conn:
            existing = [
                str(r["content"])
                for r in conn.execute(
                    "SELECT content FROM stock_items WHERE product_id=? AND content IN (%s)" % ",".join("?" for _ in clean),
                    [product_id, *clean],
                ).fetchall()
            ] if clean else []
            if existing:
                sample = ", ".join(existing[:3])
                raise ValueError(f"Kho đã có dòng trùng trong sản phẩm này: {sample}. Không nhập bỏ qua im lặng để tránh bán trùng key/tài khoản.")
            before = conn.total_changes
            conn.executemany(
                "INSERT INTO stock_items(product_id, content, status) VALUES(?,?, 'available')",
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

    def search_products(self, query: str, *, limit: int = 20) -> list[dict]:
        q = (query or "").strip()
        if not q:
            return []
        like = f"%{q.lower()}%"
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT p.*, c.name AS category_name,
                       COUNT(CASE WHEN s.status='available' THEN 1 END) AS available_stock
                  FROM products p
                  LEFT JOIN categories c ON c.id=p.category_id
                  LEFT JOIN stock_items s ON s.product_id=p.id
                 WHERE p.is_active=1
                   AND (LOWER(p.name) LIKE ? OR LOWER(p.description) LIKE ? OR LOWER(COALESCE(c.name,'')) LIKE ?)
                 GROUP BY p.id
                 ORDER BY
                   CASE WHEN LOWER(p.name) LIKE ? THEN 0 ELSE 1 END,
                   p.id DESC
                 LIMIT ?
                """,
                (like, like, like, like, limit),
            )]

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
