from __future__ import annotations

import os

from nimo_shop.db import Database
from nimo_shop.money import normalize_currency


class CatalogService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_category(self, name: str, sort_order: int = 100, category_icon: str = "📁") -> int:
        if not name.strip():
            raise ValueError("category name is required")
        icon = (category_icon or "📁").strip() or "📁"
        with self.db.transaction() as conn:
            cur = conn.execute("INSERT INTO categories(name, category_icon, sort_order) VALUES(?,?,?)", (name.strip(), icon, sort_order))
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

    def add_stock(self, product_id: int, contents: list[str], *, duplicate_policy: str | None = None) -> int:
        """Add inventory rows for a product.

        duplicate_policy:
        - allow: import every non-empty line, including duplicates. Useful for
          shops where identical account templates/links are intentional.
        - skip: silently import only new rows for this product.
        - reject: fail if input or existing stock contains duplicate content.

        Default comes from STOCK_DUPLICATE_POLICY and is now `allow` because the
        admin may sell diverse goods whose raw lines can intentionally repeat.
        """
        clean = [x.strip() for x in contents if x and x.strip()]
        if not clean:
            raise ValueError("stock content is required")
        policy = (duplicate_policy or os.getenv("STOCK_DUPLICATE_POLICY", "allow")).strip().lower()
        if policy not in {"allow", "skip", "reject"}:
            policy = "allow"
        if policy in {"reject", "skip"}:
            seen: set[str] = set()
            duplicates_in_input: list[str] = []
            deduped: list[str] = []
            for item in clean:
                if item in seen:
                    if item not in duplicates_in_input:
                        duplicates_in_input.append(item)
                    continue
                seen.add(item)
                deduped.append(item)
            if policy == "reject" and duplicates_in_input:
                sample = ", ".join(duplicates_in_input[:3])
                raise ValueError(f"Dòng nhập kho bị trùng trong cùng sản phẩm: {sample}. Hãy xóa/sửa dòng trùng rồi nhập lại.")
            if policy == "skip":
                clean = deduped
                if not clean:
                    return 0
        with self.db.transaction() as conn:
            if policy in {"reject", "skip"}:
                existing = [
                    str(r["content"])
                    for r in conn.execute(
                        "SELECT content FROM stock_items WHERE product_id=? AND content IN (%s)" % ",".join("?" for _ in clean),
                        [product_id, *clean],
                    ).fetchall()
                ] if clean else []
                if policy == "reject" and existing:
                    sample = ", ".join(existing[:3])
                    raise ValueError(f"Kho đã có dòng trùng trong sản phẩm này: {sample}. Không nhập bỏ qua im lặng để tránh bán trùng key/tài khoản.")
                if policy == "skip" and existing:
                    existing_set = set(existing)
                    clean = [x for x in clean if x not in existing_set]
                    if not clean:
                        return 0
            before = conn.total_changes
            conn.executemany(
                "INSERT INTO stock_items(product_id, content, status) VALUES(?,?, 'available')",
                [(product_id, item) for item in clean],
            )
            return conn.total_changes - before

    def list_categories(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT c.*,
                       COALESCE(SUM(CASE WHEN p.is_active=1 AND s.status='available' THEN 1 ELSE 0 END),0) AS available_stock,
                       COALESCE(COUNT(DISTINCT CASE WHEN p.is_active=1 THEN p.id END),0) AS active_products
                  FROM categories c
                  LEFT JOIN products p ON p.category_id=c.id
                  LEFT JOIN stock_items s ON s.product_id=p.id
                 WHERE c.is_active=1
                 GROUP BY c.id
                 ORDER BY c.sort_order, c.id
                """
            )]

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
        sql += " GROUP BY p.id ORDER BY COALESCE(p.category_id, 999999), p.id ASC"
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
