from __future__ import annotations

import os
import secrets
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from nimo_shop.db import Database, dumps, loads
from nimo_shop.money import fmt_money, normalize_currency, to_minor
from nimo_shop.services.audit import AuditService
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.finance import FinanceService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.notifications import NotificationService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.wallet import WalletService
from nimo_shop.web.security import hash_password, verify_password

WEB_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS admin_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    is_secret INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(admin_id) REFERENCES admin_accounts(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS managed_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    bot_type TEXT NOT NULL DEFAULT 'shop',
    token TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    admin_contact TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_managed_bots_enabled ON managed_bots(is_enabled, is_primary, id);
"""

DEFAULT_SETTING_KEYS: dict[str, tuple[str, int]] = {
    "SHOP_NAME": ("NIMO SHOP PREMIUM", 0),
    "SUPPORT_CONTACT": ("", 0),
    "ADMIN_IDS": ("", 0),
    "BOT_TOKEN": ("", 1),
    "DATABASE_PATH": ("data/shop.db", 0),
    "DEPOSIT_EXPIRES_MINUTES": ("15", 0),
    "ORDER_EXPIRES_MINUTES": ("20", 0),
    "BANK_ENABLED": ("false", 0),
    "SEPAY_API_KEY": ("", 1),
    "SEPAY_POLL_SECONDS": ("30", 0),
    "BANK_BIN": ("", 0),
    "BANK_ACCOUNT": ("", 1),
    "BANK_OWNER": ("", 0),
    "BANK_NAME": ("", 0),
    "BINANCE_PAY_ENABLED": ("false", 0),
    "BINANCE_PAY_API_KEY": ("", 1),
    "BINANCE_PAY_SECRET_KEY": ("", 1),
    "BINANCE_PAY_BASE_URL": ("https://bpay.binanceapi.com", 0),
    "BINANCE_PAY_RETURN_URL": ("", 0),
    "BINANCE_PAY_WEBHOOK_URL": ("", 0),
    "WEB_ADMIN_USERNAME": ("admin", 0),
    "WEB_ADMIN_PASSWORD": ("", 1),
    "WEB_SESSION_SECRET": ("", 1),
    "WEB_HOST": ("0.0.0.0", 0),
    "WEB_PORT": ("8080", 0),
    "WEB_DEFAULT_LANGUAGE": ("vi", 0),
    "WEB_DEFAULT_THEME": ("light", 0),
}


def _env_bool(value: str) -> str:
    return "true" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "false"


class AdminWebService:
    def __init__(self, db: Database, *, project_root: str | Path | None = None) -> None:
        self.db = db
        self.project_root = Path(project_root or os.getcwd())

    def init(self, *, bootstrap_username: str = "admin", bootstrap_password: str | None = None) -> None:
        self.db.init()
        # executescript may issue implicit commits in sqlite, so schema creation
        # is intentionally outside Database.transaction(). Data bootstrap below
        # remains atomic.
        with self.db.connect() as conn:
            conn.executescript(WEB_SCHEMA)
        with self.db.transaction() as conn:
            for key, (value, is_secret) in DEFAULT_SETTING_KEYS.items():
                env_value = os.getenv(key)
                conn.execute(
                    "INSERT OR IGNORE INTO app_settings(key, value, is_secret) VALUES(?,?,?)",
                    (key, env_value if env_value is not None else value, is_secret),
                )
            row = conn.execute("SELECT COUNT(*) AS c FROM admin_accounts WHERE is_active=1").fetchone()
            if int(row["c"]) == 0:
                password = bootstrap_password or os.getenv("WEB_ADMIN_PASSWORD") or "admin12345"
                conn.execute(
                    "INSERT INTO admin_accounts(username, password_hash, role) VALUES(?,?, 'owner')",
                    (bootstrap_username or os.getenv("WEB_ADMIN_USERNAME") or "admin", hash_password(password)),
                )

    def authenticate(self, username: str, password: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM admin_accounts WHERE username=? AND is_active=1", (username.strip(),)).fetchone()
            if row and verify_password(password, row["password_hash"]):
                return dict(row)
        return None

    def log(self, admin_id: int | None, action: str, target_type: str = "", target_id: str = "", metadata: dict | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO admin_audit_logs(admin_id, action, target_type, target_id, metadata_json) VALUES(?,?,?,?,?)",
                (admin_id, action, target_type, target_id, dumps(metadata)),
            )

    def dashboard(self) -> dict:
        return {
            "finance": FinanceService(self.db).summary(),
            "audit": AuditService(self.db).run(),
            "stock": CatalogService(self.db).stock_summary(),
            "recent_orders": self.list_orders(limit=8),
            "recent_payments": self.list_payment_events(limit=8),
            "counts": self.counts(),
        }

    def counts(self) -> dict[str, int]:
        with self.db.connect() as conn:
            keys = {
                "users": "SELECT COUNT(*) FROM users",
                "products": "SELECT COUNT(*) FROM products WHERE is_active=1",
                "available_stock": "SELECT COUNT(*) FROM stock_items WHERE status='available'",
                "pending_orders": "SELECT COUNT(*) FROM orders WHERE status='awaiting_payment'",
                "delivered_orders": "SELECT COUNT(*) FROM orders WHERE status='delivered'",
                "unmatched_payments": "SELECT COUNT(*) FROM external_payment_events WHERE status='unmatched'",
            }
            return {key: int(conn.execute(sql).fetchone()[0]) for key, sql in keys.items()}

    def list_categories(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM categories ORDER BY sort_order, id")]

    def create_category(self, name: str, sort_order: int = 100, *, admin_id: int | None = None) -> int:
        category_id = CatalogService(self.db).add_category(name, sort_order)
        self.log(admin_id, "category.create", "category", str(category_id), {"name": name})
        return category_id

    def update_category(self, category_id: int, *, name: str, sort_order: int, is_active: bool, admin_id: int | None = None) -> None:
        if not name.strip():
            raise ValueError("category name is required")
        with self.db.transaction() as conn:
            row = conn.execute("UPDATE categories SET name=?, sort_order=?, is_active=? WHERE id=?", (name.strip(), sort_order, 1 if is_active else 0, category_id)).rowcount
            if row == 0:
                raise ValueError("category not found")
        self.log(admin_id, "category.update", "category", str(category_id), {"name": name, "active": is_active})

    def list_products(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT p.*, c.name AS category_name,
                       COALESCE(SUM(CASE WHEN s.status='available' THEN 1 ELSE 0 END),0) AS available_stock,
                       COALESCE(SUM(CASE WHEN s.status='reserved' THEN 1 ELSE 0 END),0) AS reserved_stock,
                       COALESCE(SUM(CASE WHEN s.status='sold' THEN 1 ELSE 0 END),0) AS sold_stock
                  FROM products p
                  LEFT JOIN categories c ON c.id=p.category_id
                  LEFT JOIN stock_items s ON s.product_id=p.id
                 GROUP BY p.id
                 ORDER BY p.id DESC
                """
            )]

    def get_product(self, product_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, c.name AS category_name,
                       COALESCE(SUM(CASE WHEN s.status='available' THEN 1 ELSE 0 END),0) AS available_stock,
                       COALESCE(SUM(CASE WHEN s.status='reserved' THEN 1 ELSE 0 END),0) AS reserved_stock,
                       COALESCE(SUM(CASE WHEN s.status='sold' THEN 1 ELSE 0 END),0) AS sold_stock
                  FROM products p
                  LEFT JOIN categories c ON c.id=p.category_id
                  LEFT JOIN stock_items s ON s.product_id=p.id
                 WHERE p.id=?
                 GROUP BY p.id
                """,
                (product_id,),
            ).fetchone()
        if not row:
            raise ValueError("Không tìm thấy sản phẩm")
        return dict(row)

    def create_product(self, data: dict[str, Any], *, admin_id: int | None = None) -> int:
        price_minor = to_minor(data["price"], data.get("currency", "VND"))
        cost_minor = to_minor(data.get("cost", "0"), data.get("currency", "VND"))
        product_id = CatalogService(self.db).add_product(
            category_id=int(data["category_id"]) if str(data.get("category_id") or "").strip() else None,
            name=str(data["name"]),
            description=str(data.get("description") or ""),
            currency=str(data.get("currency") or "VND"),
            price_minor=price_minor,
            cost_minor=cost_minor,
            warranty_text=str(data.get("warranty_text") or ""),
        )
        self.log(admin_id, "product.create", "product", str(product_id), {"name": data.get("name")})
        return product_id

    def update_product(self, product_id: int, data: dict[str, Any], *, admin_id: int | None = None) -> None:
        cur = normalize_currency(str(data.get("currency") or "VND"))
        price_minor = to_minor(data["price"], cur)
        cost_minor = to_minor(data.get("cost", "0"), cur)
        category_id = int(data["category_id"]) if str(data.get("category_id") or "").strip() else None
        if not str(data.get("name") or "").strip():
            raise ValueError("product name is required")
        with self.db.transaction() as conn:
            updated = conn.execute(
                """
                UPDATE products
                   SET category_id=?, name=?, description=?, currency=?, price_minor=?, cost_minor=?, warranty_text=?, is_active=?
                 WHERE id=?
                """,
                (
                    category_id,
                    str(data["name"]).strip(),
                    str(data.get("description") or "").strip(),
                    cur,
                    price_minor,
                    cost_minor,
                    str(data.get("warranty_text") or "").strip(),
                    1 if str(data.get("is_active", "1")).lower() in {"1", "true", "on", "yes"} else 0,
                    product_id,
                ),
            ).rowcount
            if updated == 0:
                raise ValueError("product not found")
        self.log(admin_id, "product.update", "product", str(product_id), {"name": data.get("name")})
        if str(data.get("notify_users") or "").lower() in {"1", "true", "on", "yes"}:
            product = self.get_product(product_id)
            title = f"🛍️ Cập nhật sản phẩm: {product['name']}"
            message = (
                f"🛍️ <b>Cập nhật sản phẩm</b>\n\n"
                f"Sản phẩm: <b>{product['name']}</b>\n"
                f"Giá hiện tại: <b>{fmt_money(int(product['price_minor']), product['currency'])}</b>\n"
                "Vào bot và bấm 🛒 Mua ngay để xem chi tiết mới nhất."
            )
            NotificationService(self.db).queue_product_update(product_id=product_id, title=title, message=message)

    def delete_product(self, product_id: int, *, admin_id: int | None = None) -> str:
        """Safely delete a product.

        If the product has no order history, it is removed together with its
        unsold stock. If it already has sales/history, the product is hidden
        instead of being physically deleted so old orders and reports stay
        auditable. Products with active reservations/pending orders are rejected
        to avoid losing stock or breaking an in-progress checkout.
        """
        with self.db.transaction() as conn:
            product = conn.execute("SELECT id, name FROM products WHERE id=?", (product_id,)).fetchone()
            if not product:
                raise ValueError("Không tìm thấy sản phẩm")
            active_refs = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM orders WHERE product_id=? AND status IN ('awaiting_payment','paid')) +
                    (SELECT COUNT(*) FROM stock_items WHERE product_id=? AND status='reserved')
                """,
                (product_id, product_id),
            ).fetchone()[0]
            if int(active_refs or 0) > 0:
                raise ValueError("Không thể xóa sản phẩm đang có đơn chờ thanh toán hoặc hàng đang được giữ")

            history_refs = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM orders WHERE product_id=?) +
                    (SELECT COUNT(*) FROM stock_items WHERE product_id=? AND status='sold')
                """,
                (product_id, product_id),
            ).fetchone()[0]
            if int(history_refs or 0) > 0:
                conn.execute("UPDATE products SET is_active=0 WHERE id=?", (product_id,))
                outcome = "deactivated"
            else:
                conn.execute("DELETE FROM stock_items WHERE product_id=?", (product_id,))
                conn.execute("DELETE FROM products WHERE id=?", (product_id,))
                outcome = "deleted"
        self.log(admin_id, f"product.{outcome}", "product", str(product_id), {"name": product["name"]})
        return outcome

    def add_stock(self, product_id: int, raw_lines: str, *, admin_id: int | None = None) -> int:
        lines = [line.strip() for line in raw_lines.splitlines() if line.strip()]
        inserted = CatalogService(self.db).add_stock(product_id, lines)
        self.log(admin_id, "stock.import", "product", str(product_id), {"submitted": len(lines), "inserted": inserted})
        return inserted

    def list_stock_items(self, product_id: int | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        sql = """
            SELECT s.*, p.name AS product_name
              FROM stock_items s JOIN products p ON p.id=s.product_id
             WHERE 1=1
        """
        params: list[Any] = []
        if product_id:
            sql += " AND s.product_id=?"
            params.append(product_id)
        if status:
            sql += " AND s.status=?"
            params.append(status)
        sql += " ORDER BY s.id DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def list_orders(self, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = """
            SELECT o.*, u.telegram_id, u.username, p.name AS product_name
              FROM orders o
              JOIN users u ON u.id=o.user_id
              JOIN products p ON p.id=o.product_id
             WHERE 1=1
        """
        params: list[Any] = []
        if status:
            sql += " AND o.status=?"
            params.append(status)
        sql += " ORDER BY o.id DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def cancel_order(self, order_id: int, *, admin_id: int | None = None) -> None:
        OrderService(self.db).cancel_order(order_id, reason="admin_web_cancel")
        self.log(admin_id, "order.cancel", "order", str(order_id), {})

    def refund_order(self, order_id: int, *, admin_id: int | None = None) -> dict:
        result = OrderService(self.db).refund_to_wallet(order_id, reason="admin_web_refund")
        self.log(admin_id, "order.refund", "order", str(order_id), {})
        return result

    def list_users(self, limit: int = 200) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT u.*, COUNT(o.id) AS order_count, COALESCE(SUM(CASE WHEN o.status IN ('delivered','refunded') THEN o.total_amount_minor ELSE 0 END),0) AS spent_minor
                  FROM users u LEFT JOIN orders o ON o.user_id=u.id
                 GROUP BY u.id ORDER BY u.id DESC LIMIT ?
                """,
                (limit,),
            )]

    def list_wallets(self, limit: int = 200) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT wb.*, u.telegram_id, u.username, u.full_name
                  FROM wallet_balances wb JOIN users u ON u.id=wb.user_id
                 ORDER BY wb.updated_at DESC LIMIT ?
                """,
                (limit,),
            )]

    def resolve_user_ref(self, user_ref: str, *, create_if_numeric_telegram: bool = False) -> int:
        """Resolve admin input to an internal users.id.

        Admins usually paste Telegram ID from the bot, not the internal DB ID.
        Accept internal ID, Telegram ID, @username/username. For credit actions,
        a numeric Telegram ID can be created if the user has not started the bot
        yet, preventing raw FOREIGN KEY failures while still keeping DB valid.
        """
        ref = str(user_ref or "").strip()
        if not ref:
            raise ValueError("Vui lòng nhập User: Telegram ID, username hoặc ID nội bộ")
        username = ref[1:] if ref.startswith("@") else ref
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM users WHERE telegram_id=?", (ref,)).fetchone()
            if row:
                return int(row["id"])
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if row:
                return int(row["id"])
            if ref.isdigit():
                # Fall back to internal DB id only after trying Telegram ID.
                row = conn.execute("SELECT id FROM users WHERE id=?", (int(ref),)).fetchone()
                if row:
                    return int(row["id"])
                if create_if_numeric_telegram:
                    cur = conn.execute("INSERT INTO users(telegram_id, username, full_name) VALUES(?,?,?)", (ref, None, None))
                    return int(cur.lastrowid)
        raise ValueError("Không tìm thấy người dùng. Hãy nhập Telegram ID đúng hoặc yêu cầu khách bấm /start trước.")

    def manual_wallet_adjust(self, *, user_ref: str, direction: str, currency: str, amount: str, reason: str, admin_id: int | None = None) -> int:
        amount_minor = to_minor(amount, currency)
        user_id = self.resolve_user_ref(user_ref, create_if_numeric_telegram=(direction == "credit"))
        key = f"web-wallet-{direction}:{user_id}:{currency}:{amount_minor}:{reason}:{secrets.token_hex(4)}"
        service = WalletService(self.db)
        if direction == "credit":
            balance = service.credit(user_id, currency, amount_minor, reason=reason, idempotency_key=key)
        elif direction == "debit":
            balance = service.debit(user_id, currency, amount_minor, reason=reason, idempotency_key=key)
        else:
            raise ValueError("Loại điều chỉnh ví không hợp lệ")
        self.log(admin_id, f"wallet.{direction}", "user", str(user_id), {"currency": currency, "amount_minor": amount_minor, "reason": reason, "input": user_ref})
        return balance

    def list_payment_intents(self, limit: int = 100) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT pi.*, u.telegram_id, u.username, o.public_code AS order_code
                  FROM payment_intents pi
                  JOIN users u ON u.id=pi.user_id
                  LEFT JOIN orders o ON o.id=pi.order_id
                 ORDER BY pi.id DESC LIMIT ?
                """,
                (limit,),
            )]

    def list_payment_events(self, limit: int = 100) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM external_payment_events ORDER BY id DESC LIMIT ?", (limit,))]

    def confirm_payment(self, *, payment_code: str, tx_id: str, amount: str, currency: str, provider: str, admin_id: int | None = None) -> dict:
        result = PaymentService(self.db).confirm_provider_transaction(
            provider=provider,
            provider_tx_id=tx_id,
            amount_minor=to_minor(amount, currency),
            currency=currency,
            description=f"manual web confirm {payment_code}",
            raw={"source": "web_admin", "admin_id": admin_id},
        )
        self.log(admin_id, "payment.confirm", "payment_code", payment_code, {"provider": provider, "tx_id": tx_id, "amount": amount, "currency": currency, "result": result.get("status")})
        return result

    def get_settings(self) -> dict[str, dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM app_settings ORDER BY key").fetchall()
        return {r["key"]: {"value": r["value"], "is_secret": bool(r["is_secret"]), "updated_at": r["updated_at"]} for r in rows}

    def update_settings(self, values: dict[str, str], *, admin_id: int | None = None, write_env: bool = False) -> None:
        # Blank secret fields mean "keep the previously saved value". This is
        # important for the premium settings UI, where secrets are not rendered
        # back into the form for safety.
        changed_keys: list[str] = []
        with self.db.transaction() as conn:
            current = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings")}
            for key, value in values.items():
                if key not in DEFAULT_SETTING_KEYS:
                    continue
                is_secret = DEFAULT_SETTING_KEYS[key][1]
                if key.endswith("ENABLED"):
                    value = _env_bool(value)
                if is_secret and str(value).strip() == "" and current.get(key):
                    continue
                conn.execute(
                    """
                    INSERT INTO app_settings(key, value, is_secret, updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, is_secret=excluded.is_secret, updated_at=CURRENT_TIMESTAMP
                    """,
                    (key, str(value), is_secret),
                )
                changed_keys.append(key)

            # Apply web login changes immediately; otherwise users save the new
            # password in Settings but still cannot log in with it until manual DB
            # changes. Empty password means keep the current password.
            if admin_id:
                username = str(values.get("WEB_ADMIN_USERNAME") or "").strip()
                password = str(values.get("WEB_ADMIN_PASSWORD") or "").strip()
                if username:
                    conn.execute("UPDATE admin_accounts SET username=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (username, admin_id))
                if password:
                    conn.execute("UPDATE admin_accounts SET password_hash=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (hash_password(password), admin_id))
        if write_env:
            self.write_env_file()
        self.log(admin_id, "settings.update", "settings", "", {"keys": sorted(changed_keys), "write_env": write_env})

    def write_env_file(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.project_root / ".env"
        settings = self.get_settings()
        lines = ["# Generated by NIMO Web Admin. Review secrets before sharing.\n"]
        for key in DEFAULT_SETTING_KEYS:
            value = settings.get(key, {"value": DEFAULT_SETTING_KEYS[key][0]})["value"]
            escaped = str(value).replace("\n", "\\n")
            lines.append(f"{key}={escaped}\n")
        target.write_text("".join(lines), encoding="utf-8")
        return target

    def search_products(self, query: str, limit: int = 50) -> list[dict]:
        return CatalogService(self.db).search_products(query, limit=limit)

    def list_notifications(self, limit: int = 100) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM bot_notifications ORDER BY id DESC LIMIT ?", (limit,))]

    def create_notification(self, *, title: str, message: str, product_id: int | None = None, admin_id: int | None = None) -> int:
        if not title.strip() or not message.strip():
            raise ValueError("Vui lòng nhập tiêu đề và nội dung thông báo")
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO bot_notifications(kind, title, message, product_id) VALUES(?,?,?,?)",
                ("broadcast", title.strip(), message.strip(), product_id),
            )
            notification_id = int(cur.lastrowid)
        self.log(admin_id, "notification.create", "notification", str(notification_id), {"title": title, "product_id": product_id})
        return notification_id

    def list_managed_bots(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM managed_bots ORDER BY is_primary DESC, id DESC")]

    def create_managed_bot(self, data: dict[str, str], *, admin_id: int | None = None) -> int:
        name = str(data.get("name") or "").strip()
        token = str(data.get("token") or "").strip()
        if not name:
            raise ValueError("Vui lòng nhập tên bot")
        if not token:
            raise ValueError("Vui lòng nhập token bot")
        is_primary = str(data.get("is_primary") or "").lower() in {"1", "true", "on", "yes"}
        with self.db.transaction() as conn:
            if is_primary:
                conn.execute("UPDATE managed_bots SET is_primary=0")
            cur = conn.execute(
                """
                INSERT INTO managed_bots(name, bot_type, token, username, admin_contact, is_primary, is_enabled, notes)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    name,
                    str(data.get("bot_type") or "shop").strip() or "shop",
                    token,
                    str(data.get("username") or "").strip().lstrip("@"),
                    str(data.get("admin_contact") or "").strip(),
                    1 if is_primary else 0,
                    1 if str(data.get("is_enabled") or "on").lower() in {"1", "true", "on", "yes"} else 0,
                    str(data.get("notes") or "").strip(),
                ),
            )
            bot_id = int(cur.lastrowid)
        if is_primary:
            self.update_settings({"BOT_TOKEN": token}, admin_id=admin_id, write_env=True)
        self.log(admin_id, "bot.create", "managed_bot", str(bot_id), {"name": name, "primary": is_primary})
        return bot_id

    def update_managed_bot(self, bot_id: int, data: dict[str, str], *, admin_id: int | None = None) -> None:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Vui lòng nhập tên bot")
        token = str(data.get("token") or "").strip()
        is_primary = str(data.get("is_primary") or "").lower() in {"1", "true", "on", "yes"}
        with self.db.transaction() as conn:
            old = conn.execute("SELECT * FROM managed_bots WHERE id=?", (bot_id,)).fetchone()
            if not old:
                raise ValueError("Không tìm thấy bot")
            final_token = token or str(old["token"])
            if is_primary:
                conn.execute("UPDATE managed_bots SET is_primary=0 WHERE id<>?", (bot_id,))
            conn.execute(
                """
                UPDATE managed_bots
                   SET name=?, bot_type=?, token=?, username=?, admin_contact=?, is_primary=?, is_enabled=?, notes=?, updated_at=CURRENT_TIMESTAMP
                 WHERE id=?
                """,
                (
                    name,
                    str(data.get("bot_type") or "shop").strip() or "shop",
                    final_token,
                    str(data.get("username") or "").strip().lstrip("@"),
                    str(data.get("admin_contact") or "").strip(),
                    1 if is_primary else 0,
                    1 if str(data.get("is_enabled") or "").lower() in {"1", "true", "on", "yes"} else 0,
                    str(data.get("notes") or "").strip(),
                    bot_id,
                ),
            )
        if is_primary:
            self.update_settings({"BOT_TOKEN": final_token}, admin_id=admin_id, write_env=True)
        self.log(admin_id, "bot.update", "managed_bot", str(bot_id), {"name": name, "primary": is_primary})

    def delete_managed_bot(self, bot_id: int, *, admin_id: int | None = None) -> None:
        with self.db.transaction() as conn:
            row = conn.execute("DELETE FROM managed_bots WHERE id=?", (bot_id,)).rowcount
            if row == 0:
                raise ValueError("Không tìm thấy bot")
        self.log(admin_id, "bot.delete", "managed_bot", str(bot_id), {})

    def list_backups(self) -> list[dict]:
        backup_dir = self.project_root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        rows: list[dict] = []
        for file in sorted(backup_dir.glob("nimo-backup-*.zip"), reverse=True):
            rows.append({"name": file.name, "path": str(file), "size": file.stat().st_size, "created_at": datetime.fromtimestamp(file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
        return rows

    def create_backup(self, *, include_env: bool = True, admin_id: int | None = None) -> Path:
        backup_dir = self.project_root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"nimo-backup-{stamp}.zip"
        db_path = Path(self.db.path)
        if not db_path.exists():
            raise ValueError(f"Không tìm thấy database: {db_path}")
        tmp_db = backup_dir / f"shop-{stamp}.db"
        # Use sqlite backup API instead of copying a hot WAL database directly.
        import sqlite3
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(tmp_db))
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, "data/shop.db")
            env_path = self.project_root / ".env"
            if include_env and env_path.exists():
                zf.write(env_path, ".env")
            zf.writestr("RESTORE_GUIDE.txt", "Giải nén file này vào thư mục dự án hoặc dùng trang Backup/Restore của Web Admin. File chính: data/shop.db. Không chia sẻ backup nếu có .env vì chứa token/API key.\n")
        tmp_db.unlink(missing_ok=True)
        self.log(admin_id, "backup.create", "backup", target.name, {"include_env": include_env, "size": target.stat().st_size})
        return target

    def restore_backup(self, backup_path: str, *, admin_id: int | None = None) -> None:
        path = Path(backup_path).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists() or path.suffix.lower() != ".zip":
            raise ValueError("File backup không tồn tại hoặc không phải .zip")
        db_path = Path(self.db.path)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "data/shop.db" not in names:
                raise ValueError("Backup không chứa data/shop.db")
            safety = self.create_backup(include_env=True, admin_id=admin_id)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = db_path.with_suffix(".restore-tmp")
            with zf.open("data/shop.db") as src, tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            tmp.replace(db_path)
            if ".env" in names:
                with zf.open(".env") as src, (self.project_root / ".env").open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        self.log(admin_id, "backup.restore", "backup", str(path), {"safety_backup": str(safety)})

    def audit(self) -> list[dict]:
        return [{"code": issue.code, "message": issue.message} for issue in AuditService(self.db).run()]

    def audit_logs(self, limit: int = 100) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT l.*, a.username AS admin_username
                  FROM admin_audit_logs l LEFT JOIN admin_accounts a ON a.id=l.admin_id
                 ORDER BY l.id DESC LIMIT ?
                """,
                (limit,),
            )]
