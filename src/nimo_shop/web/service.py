from __future__ import annotations

import csv
import io
import os
import re
import secrets
import shutil
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nimo_shop.db import Database, dumps, loads
from nimo_shop.money import fmt_money, normalize_currency, to_minor
from nimo_shop.services.bank_accounts import BankAccountService
from nimo_shop.services.audit import AuditService
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.finance import FinanceService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.notifications import NotificationService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.preorders import PreorderService
from nimo_shop.services.wallet import WalletService
from nimo_shop.web.security import hash_password, verify_password


STOCK_FORMATS: dict[str, dict[str, object]] = {
    "auto": {
        "name": "Tự nhận diện",
        "labels": ["Tài khoản"],
        "example": "Mỗi dòng một tài khoản/key. Hệ thống tự nhận email|pass, email / pass, uid|pass|cookie|token.",
    },
    "raw": {
        "name": "Mỗi dòng là một hàng giao",
        "labels": ["Dữ liệu"],
        "example": "KEY-ABC-123 hoặc link/file/license bất kỳ. Bot giữ nguyên từng dòng.",
    },
    "email_pass_pipe": {
        "name": "Email | Mật khẩu",
        "labels": ["Email", "Mật khẩu"],
        "example": "user@example.com|password123",
    },
    "email_pass_slash": {
        "name": "Email / Mật khẩu",
        "labels": ["Email", "Mật khẩu"],
        "example": "user@example.com / password123",
    },
    "email_pass_2fa_pipe": {
        "name": "Email | Mật khẩu | 2FA/Recovery",
        "labels": ["Email", "Mật khẩu", "2FA/Recovery"],
        "example": "user@example.com|password123|YSQIL2FCYEOW6S6Q...",
    },
    "uid_pass_cookie_token": {
        "name": "UID | Mật khẩu | Cookie | Token",
        "labels": ["UID", "Mật khẩu", "Cookie", "Token"],
        "example": "6159...|pass|c_user=...;xs=...|EAAA...",
    },
    "pipe": {
        "name": "Dữ liệu phân tách bằng dấu |",
        "labels": ["Cột 1", "Cột 2", "Cột 3", "Cột 4"],
        "example": "cot1|cot2|cot3|cot4",
    },
    "csv": {
        "name": "CSV/Excel nhiều cột",
        "labels": ["Cột 1", "Cột 2", "Cột 3", "Cột 4"],
        "example": "email,password,2fa hoặc uid,password,cookie,token",
    },
}

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

CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed')),
    discount_value INTEGER NOT NULL CHECK(discount_value >= 0),
    currency TEXT NOT NULL DEFAULT 'VND',
    max_uses INTEGER NOT NULL DEFAULT 0,
    used_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS delivery_download_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    user_id INTEGER,
    source TEXT NOT NULL DEFAULT 'bot',
    filename TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE SET NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS low_stock_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    threshold INTEGER NOT NULL,
    available INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS buyer_api_idempotency (
    user_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    response_json TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, idempotency_key),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS admin_login_attempts (
    attempt_key TEXT PRIMARY KEY,
    failed_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

"""

DEFAULT_SETTING_KEYS: dict[str, tuple[str, int]] = {
    "SHOP_NAME": ("NIMO SHOP PREMIUM", 0),
    "SUPPORT_CONTACT": ("", 0),
    "ADMIN_IDS": ("", 0),
    "BOT_TOKEN": ("", 1),
    "DATABASE_PATH": ("data/shop.db", 0),
    "DEPOSIT_EXPIRES_MINUTES": ("15", 0),
    "ORDER_EXPIRES_MINUTES": ("15", 0),
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
    "BINANCE_PAY_ID": ("", 0),
    "BINANCE_PAY_NOTE": ("", 0),
    "USDT_BEP20_ADDRESS": ("", 0),
    "USDT_BEP20_TOLERANCE": ("0.02", 0),
    "USDT_NETWORK": ("BEP20", 0),
    "WEB_ADMIN_USERNAME": ("admin", 0),
    "WEB_ADMIN_PASSWORD": ("", 1),
    "WEB_SESSION_SECRET": ("", 1),
    "WEB_HOST": ("0.0.0.0", 0),
    "WEB_PORT": ("8080", 0),
    "WEB_DEFAULT_LANGUAGE": ("vi", 0),
    "WEB_DEFAULT_THEME": ("light", 0),
    # Delivery display policy for digital goods.
    # auto: inline small orders, file for large/long orders.
    # file_only: always send a TXT file and only show a short summary in chat.
    # inline_and_file: show inline when small and also attach a TXT file.
    "DELIVERY_OUTPUT_MODE": ("auto", 0),
    "DELIVERY_FILE_THRESHOLD": ("20", 0),
    "LOW_STOCK_THRESHOLD": ("5", 0),
    "PREORDER_DEPOSIT_PERCENT": ("10", 0),
    "STOCK_DUPLICATE_POLICY": ("allow", 0),
    "API_PUBLIC_BASE_URL": ("http://127.0.0.1:8080", 0),
    "WEBHOOK_SHARED_SECRET": ("", 1),
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
                password = bootstrap_password or os.getenv("WEB_ADMIN_PASSWORD")
                if not password or password == "admin12345":
                    raise ValueError("WEB_ADMIN_PASSWORD/--password is required and must not be the old default admin12345")
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

    @staticmethod
    def _login_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def login_blocked(self, attempt_key: str) -> tuple[bool, str]:
        now = self._login_now()
        with self.db.connect() as conn:
            row = conn.execute("SELECT locked_until FROM admin_login_attempts WHERE attempt_key=?", (attempt_key,)).fetchone()
        locked_until = str(row["locked_until"] or "") if row else ""
        return (bool(locked_until and locked_until > now), locked_until)

    def record_login_failure(self, attempt_key: str, *, max_failures: int = 5, lock_minutes: int = 15) -> None:
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        now = now_dt.isoformat()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT failed_count, locked_until FROM admin_login_attempts WHERE attempt_key=?", (attempt_key,)).fetchone()
            failed = int(row["failed_count"] or 0) + 1 if row else 1
            locked_until = None
            if failed >= max_failures:
                locked_until = (now_dt + timedelta(minutes=lock_minutes)).isoformat()
            conn.execute(
                """
                INSERT INTO admin_login_attempts(attempt_key, failed_count, locked_until, updated_at) VALUES(?,?,?,?)
                ON CONFLICT(attempt_key) DO UPDATE SET failed_count=excluded.failed_count, locked_until=excluded.locked_until, updated_at=excluded.updated_at
                """,
                (attempt_key, failed, locked_until, now),
            )

    def clear_login_failures(self, attempt_key: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM admin_login_attempts WHERE attempt_key=?", (attempt_key,))

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
            return [dict(r) for r in conn.execute(
                """
                SELECT c.*,
                       COALESCE(SUM(CASE WHEN p.is_active=1 AND s.status='available' THEN 1 ELSE 0 END),0) AS available_stock,
                       COALESCE(COUNT(DISTINCT CASE WHEN p.is_active=1 THEN p.id END),0) AS active_products
                  FROM categories c
                  LEFT JOIN products p ON p.category_id=c.id
                  LEFT JOIN stock_items s ON s.product_id=p.id
                 GROUP BY c.id
                 ORDER BY c.sort_order, c.id
                """
            )]

    def create_category(self, name: str, sort_order: int = 100, category_icon: str = "📁", *, admin_id: int | None = None) -> int:
        category_id = CatalogService(self.db).add_category(name, sort_order, category_icon=category_icon)
        self.log(admin_id, "category.create", "category", str(category_id), {"name": name})
        return category_id

    def update_category(self, category_id: int, *, name: str, sort_order: int, is_active: bool, category_icon: str = "📁", admin_id: int | None = None) -> None:
        if not name.strip():
            raise ValueError("category name is required")
        icon = (category_icon or "📁").strip() or "📁"
        with self.db.transaction() as conn:
            row = conn.execute("UPDATE categories SET name=?, category_icon=?, sort_order=?, is_active=? WHERE id=?", (name.strip(), icon, sort_order, 1 if is_active else 0, category_id)).rowcount
            if row == 0:
                raise ValueError("category not found")
        self.log(admin_id, "category.update", "category", str(category_id), {"name": name, "active": is_active, "icon": icon})

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
            stock_format=str(data.get("stock_format") or "auto"),
            stock_format_labels=str(data.get("stock_format_labels") or ""),
            stock_format_example=str(data.get("stock_format_example") or ""),
            delivery_format=str(data.get("delivery_format") or "auto"),
            product_icon=str(data.get("product_icon") or ""),
            product_custom_emoji_id=str(data.get("product_custom_emoji_id") or ""),
            product_short_description=str(data.get("product_short_description") or ""),
            product_long_description=str(data.get("product_long_description") or ""),
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
                   SET category_id=?, name=?, description=?, currency=?, price_minor=?, cost_minor=?, warranty_text=?, is_active=?,
                       stock_format=?, stock_format_labels=?, stock_format_example=?, delivery_format=?,
                       product_icon=?, product_custom_emoji_id=?, product_short_description=?, product_long_description=?
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
                    self._normalize_stock_mode(str(data.get("stock_format") or "auto")),
                    str(data.get("stock_format_labels") or "").strip(),
                    str(data.get("stock_format_example") or "").strip(),
                    str(data.get("delivery_format") or "auto").strip() or "auto",
                    str(data.get("product_icon") or "").strip(),
                    str(data.get("product_custom_emoji_id") or "").strip(),
                    str(data.get("product_short_description") or "").strip(),
                    str(data.get("product_long_description") or "").strip(),
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

    PRODUCT_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
    PRODUCT_IMAGE_MAX_BYTES = 5 * 1024 * 1024

    @staticmethod
    def _detect_image_ext(filename: str, data: bytes) -> str:
        ext = Path(filename or "").suffix.lower()
        if ext == ".jpeg":
            ext = ".jpg"
        if ext not in AdminWebService.PRODUCT_IMAGE_EXTS:
            # Fall back to magic bytes when browser did not send a useful name.
            if data.startswith(b"\xff\xd8\xff"):
                return ".jpg"
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                return ".png"
            if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
                return ".webp"
            raise ValueError("Ảnh sản phẩm chỉ hỗ trợ JPG, PNG hoặc WebP")
        signatures = {
            ".jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
            ".png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
            ".webp": lambda b: b.startswith(b"RIFF") and b[8:12] == b"WEBP",
        }
        if not signatures[ext](data):
            raise ValueError("Nội dung file không khớp định dạng ảnh đã chọn")
        return ext

    def save_product_image(self, product_id: int, *, filename: str, data: bytes, admin_id: int | None = None) -> str:
        if not data:
            raise ValueError("File ảnh trống")
        if len(data) > self.PRODUCT_IMAGE_MAX_BYTES:
            raise ValueError("Ảnh sản phẩm quá lớn. Giới hạn 5MB")
        # Validate the product before writing to disk; otherwise a bad product_id
        # can leave orphan files in media/products.
        self.get_product(product_id)
        ext = self._detect_image_ext(filename, data)
        media_dir = self.project_root / "media" / "products"
        media_dir.mkdir(parents=True, exist_ok=True)
        path = media_dir / f"product_{product_id}{ext}"
        tmp_path = media_dir / f".product_{product_id}.{secrets.token_hex(6)}{ext}.tmp"
        rel = path.relative_to(self.project_root).as_posix()
        try:
            tmp_path.write_bytes(data)
            # Remove previous local images for the same product so backup stays clean.
            for old in media_dir.glob(f"product_{product_id}.*"):
                if old != path:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            tmp_path.replace(path)
            with self.db.transaction() as conn:
                row = conn.execute(
                    "UPDATE products SET product_image_path=?, product_image_file_id='' WHERE id=?",
                    (rel, product_id),
                ).rowcount
                if row == 0:
                    raise ValueError("Không tìm thấy sản phẩm")
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        self.log(admin_id, "product.image.save", "product", str(product_id), {"path": rel, "size": len(data)})
        return rel

    def clear_product_image(self, product_id: int, *, admin_id: int | None = None) -> None:
        product = self.get_product(product_id)
        rel = str(product.get("product_image_path") or "")
        if rel:
            path = (self.project_root / rel).resolve()
            media_root = (self.project_root / "media" / "products").resolve()
            try:
                if path.is_relative_to(media_root) and path.exists() and path.is_file():
                    path.unlink()
            except OSError:
                pass
        with self.db.transaction() as conn:
            conn.execute("UPDATE products SET product_image_path='', product_image_file_id='' WHERE id=?", (product_id,))
        self.log(admin_id, "product.image.clear", "product", str(product_id), {})

    def update_product_image_file_id(self, product_id: int, file_id: str) -> None:
        if not file_id:
            return
        with self.db.transaction() as conn:
            conn.execute("UPDATE products SET product_image_file_id=? WHERE id=?", (file_id, product_id))

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

    @staticmethod
    def stock_format_options() -> dict[str, dict[str, object]]:
        return STOCK_FORMATS

    @staticmethod
    def stock_format_labels(stock_format: str = "auto", custom_labels: str = "") -> list[str]:
        if custom_labels.strip():
            labels = [x.strip() for x in re.split(r"[|,;/\n]+", custom_labels) if x.strip()]
            if labels:
                return labels
        return list(STOCK_FORMATS.get(stock_format or "auto", STOCK_FORMATS["auto"]).get("labels", ["Dữ liệu"]))

    @staticmethod
    def _normalize_stock_mode(mode: str) -> str:
        mode = (mode or "auto").strip().lower()
        aliases = {
            "product": "product",
            "email/pass": "email_pass_slash",
            "email_pass": "email_pass_pipe",
            "uid_pass_cookie_token": "uid_pass_cookie_token",
            "uid|pass|cookie|token": "uid_pass_cookie_token",
        }
        return aliases.get(mode, mode if mode in STOCK_FORMATS else "auto")

    def product_stock_format(self, product_id: int, fallback: str = "auto") -> dict[str, object]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT stock_format, stock_format_labels, stock_format_example, delivery_format FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            return {"stock_format": fallback, "stock_format_labels": "", "stock_format_example": "", "delivery_format": "auto"}
        return dict(row)

    def parse_stock_text(self, raw_text: str, *, parser_mode: str = "auto", custom_labels: str = "") -> dict[str, object]:
        """Parse stock data pasted or uploaded by admin.

        This parser is intentionally product-aware but conservative. It normalizes
        common credential formats into one stock line per delivered item. The
        original secret values are preserved inside the normalized line; previews
        and logs are masked.

        Supported examples:
        - email@example.com|password|2FA
        - email@example.com / password
        - UID|password|cookie|token
        - raw key/license/link, one item per line
        """
        text = (raw_text or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            raise ValueError("Không tìm thấy dòng dữ liệu kho trong file/nội dung nhập")

        mode = self._normalize_stock_mode(parser_mode)
        detected = "raw"
        normalized: list[str] = []

        def looks_email(value: str) -> bool:
            return "@" in value and "." in value.split("@")[-1]

        def slash_to_pipe(line: str) -> str | None:
            # Prefer explicit spaced slash. Also tolerate tab-like spaces around slash.
            parts = [x.strip() for x in re.split(r"\s+/\s+", line, maxsplit=1) if x.strip()]
            if len(parts) == 2 and (looks_email(parts[0]) or len(parts[0]) >= 3):
                return "|".join(parts)
            return None

        if mode == "raw":
            detected = "raw"
            normalized = lines
        elif mode == "email_pass_slash":
            detected = "email_pass_slash"
            normalized = []
            for line in lines:
                value = slash_to_pipe(line)
                if not value:
                    raise ValueError(f"Dòng không đúng dạng Email / Mật khẩu: {self.mask_stock_line(line, 'raw')}")
                normalized.append(value)
        elif mode in {"email_pass_pipe", "email_pass_2fa_pipe", "uid_pass_cookie_token", "pipe"}:
            expected_min = {"email_pass_pipe": 2, "email_pass_2fa_pipe": 3, "uid_pass_cookie_token": 4, "pipe": 2}[mode]
            detected = mode
            normalized = []
            for line in lines:
                parts = [x.strip() for x in line.split("|")]
                if len(parts) < expected_min or any(not x for x in parts[:expected_min]):
                    raise ValueError(f"Dòng không đúng định dạng {STOCK_FORMATS[mode]['name']}: {self.mask_stock_line(line, 'raw')}")
                normalized.append("|".join(parts))
        elif mode == "csv":
            reader = csv.reader(io.StringIO(text))
            rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
            if not rows:
                raise ValueError("CSV không có dòng dữ liệu")
            header_tokens = {"username", "user", "email", "password", "pass", "cookie", "token", "uid", "account", "2fa", "recovery"}
            start = 1 if any(c.lower() in header_tokens for c in rows[0]) else 0
            detected = "csv"
            normalized = ["|".join(row) for row in rows[start:] if any(row)]
        else:
            # Auto detection: keep line-based semantics; choose the safest
            # normalizer based on the majority format in the file.
            slash_rows = [slash_to_pipe(line) for line in lines]
            valid_slash = [x for x in slash_rows if x]
            pipe_rows = [line for line in lines if line.count("|") >= 1]
            pipe4_rows = [line for line in lines if line.count("|") >= 3]
            sample = "\n".join(lines[:5])
            if pipe4_rows and len(pipe4_rows) >= max(1, int(len(lines) * 0.7)):
                detected = "uid_pass_cookie_token"
                normalized = pipe4_rows
            elif pipe_rows and len(pipe_rows) >= max(1, int(len(lines) * 0.7)):
                # Email|pass|2fa and email|pass both stay pipe lines; labels can
                # be controlled by product stock_format.
                detected = "pipe_account"
                normalized = pipe_rows
            elif valid_slash and len(valid_slash) >= max(1, int(len(lines) * 0.7)):
                detected = "email_pass_slash"
                normalized = valid_slash
            elif "," in sample and any(h in sample.lower() for h in ["username", "password", "account", "email", "token", "cookie"]):
                reader = csv.reader(io.StringIO(text))
                rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
                header_tokens = {"username", "user", "email", "password", "pass", "cookie", "token", "uid", "account", "2fa", "recovery"}
                start = 1 if rows and any(c.lower() in header_tokens for c in rows[0]) else 0
                detected = "csv"
                normalized = ["|".join(row) for row in rows[start:] if any(row)]
            else:
                detected = "raw"
                normalized = lines

        seen: set[str] = set()
        duplicates: list[str] = []
        for line in normalized:
            if line in seen and line not in duplicates:
                duplicates.append(line)
            seen.add(line)
        preview = [self.mask_stock_line(line, detected, custom_labels=custom_labels) for line in normalized[:5]]
        return {
            "detected": detected,
            "lines": normalized,
            "count": len(normalized),
            "duplicates": duplicates,
            "preview": preview,
        }

    def mask_stock_line(self, line: str, detected: str = "raw", *, custom_labels: str = "") -> str:
        def mask(value: str, keep: int = 4) -> str:
            value = value.strip()
            if len(value) <= keep * 2:
                return value[:2] + "***" if value else ""
            return value[:keep] + "…" + value[-keep:]

        if "|" not in line:
            return mask(line, 6)
        parts = line.split("|")
        labels = self.stock_format_labels(detected, custom_labels)
        if detected == "pipe_account":
            labels = ["Tài khoản", "Mật khẩu", "Cột 3", "Cột 4"]
        masked: list[str] = []
        for idx, part in enumerate(parts):
            label = labels[idx] if idx < len(labels) else f"Cột {idx + 1}"
            masked.append(f"{label}: {mask(part, 4)}")
        return " | ".join(masked)

    def extract_stock_text_from_upload(self, filename: str, data: bytes) -> str:
        name = (filename or "").lower()
        if not data:
            raise ValueError("File tải lên đang trống")
        if name.endswith(".docx"):
            # Minimal DOCX text extraction with stdlib: read document XML and
            # join text nodes by paragraph/table order. This lets admins upload
            # Word files without installing heavy dependencies on Termux.
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    xml = zf.read("word/document.xml")
            except Exception as exc:
                raise ValueError("Không đọc được file .docx. Hãy lưu lại đúng định dạng Word .docx hoặc dùng .txt/.csv") from exc
            root = ET.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs: list[str] = []
            for para in root.findall(".//w:p", ns):
                txt = "".join(t.text or "" for t in para.findall(".//w:t", ns)).strip()
                if txt:
                    paragraphs.append(txt)
            return "\n".join(paragraphs)
        for enc in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def _resolve_stock_parser(self, product_id: int, parser_mode: str = "auto") -> tuple[str, str]:
        mode = self._normalize_stock_mode(parser_mode)
        labels = ""
        if mode == "product":
            profile = self.product_stock_format(product_id)
            mode = self._normalize_stock_mode(str(profile.get("stock_format") or "auto"))
            labels = str(profile.get("stock_format_labels") or "")
        return mode, labels

    def stock_duplicate_policy(self) -> str:
        settings = self.get_settings()
        value = str(settings.get("STOCK_DUPLICATE_POLICY", {"value": os.getenv("STOCK_DUPLICATE_POLICY", "allow")}).get("value") or "allow").strip().lower()
        return value if value in {"allow", "skip", "reject"} else "allow"

    def order_expires_minutes(self) -> int:
        settings = self.get_settings()
        raw = settings.get("ORDER_EXPIRES_MINUTES", {"value": os.getenv("ORDER_EXPIRES_MINUTES", "15")}).get("value")
        try:
            return max(1, int(raw or 15))
        except (TypeError, ValueError):
            return 15

    def add_stock(self, product_id: int, raw_lines: str, *, admin_id: int | None = None, parser_mode: str = "product") -> int:
        parser_mode, custom_labels = self._resolve_stock_parser(product_id, parser_mode)
        parsed = self.parse_stock_text(raw_lines, parser_mode=parser_mode, custom_labels=custom_labels)
        lines = parsed["lines"]
        policy = self.stock_duplicate_policy()
        duplicates = parsed.get("duplicates") or []
        if duplicates and policy == "reject":
            sample = ", ".join(self.mask_stock_line(str(x), str(parsed.get("detected") or "raw"), custom_labels=custom_labels) for x in list(duplicates)[:3])
            raise ValueError(f"Dữ liệu nhập có dòng trùng. Hãy xóa/sửa dòng trùng rồi nhập lại. Ví dụ: {sample}")
        inserted = CatalogService(self.db).add_stock(product_id, list(lines), duplicate_policy=policy)
        if inserted > 0:
            product = self.get_product(product_id) or {"name": f"#{product_id}", "price_minor": 0, "currency": "VND"}
            NotificationService(self.db).queue_product_update(
                product_id=product_id,
                title="Hàng mới đã về",
                message=(
                    f"📦 <b>Hàng mới đã về</b>\n\n"
                    f"Sản phẩm: <b>{str(product.get('name') or product_id)}</b>\n"
                    f"Số lượng vừa nhập: <b>{inserted}</b>\n\n"
                    "Bấm /start để vào shop và mua ngay."
                ),
            )
            created_orders = PreorderService(self.db, order_expires_minutes=self.order_expires_minutes()).create_payment_orders_for_available_stock(product_id)
            notifier = NotificationService(self.db)
            for order in created_orders:
                remaining = int(order.get("total_amount_minor") or 0)
                if str(order.get("status")) == "delivered" or remaining == 0:
                    msg = (
                        f"✅ <b>Sản phẩm đặt trước đã được giao</b>\n\n"
                        f"Mã đặt trước: <code>{order.get('preorder_code')}</code>\n"
                        f"Đơn hàng: <code>{order.get('public_code')}</code>\n"
                        f"Sản phẩm: <b>{order.get('product_name')}</b>\n"
                        f"Số lượng: <b>{int(order.get('quantity') or 0)}</b>\n"
                        "Bạn có thể vào Lịch sử mua để xem/tải lại thông tin hàng."
                    )
                    notifier.queue_user_message(user_id=int(order["user_id"]), kind="preorder_delivered", title="Đặt trước đã giao hàng", message=msg, product_id=product_id)
                else:
                    msg = (
                        f"📦 <b>Sản phẩm đặt trước đã có hàng</b>\n\n"
                        f"Mã đặt trước: <code>{order.get('preorder_code')}</code>\n"
                        f"Đơn thanh toán phần còn lại: <code>{order.get('public_code')}</code>\n"
                        f"Sản phẩm: <b>{order.get('product_name')}</b>\n"
                        f"Số lượng: <b>{int(order.get('quantity') or 0)}</b>\n"
                        f"Đã cọc: <b>{fmt_money(int(order.get('deposit_amount_minor') or 0), order.get('currency') or 'VND')}</b>\n"
                        f"Còn cần thanh toán: <b>{fmt_money(remaining, order.get('currency') or 'VND')}</b>\n\n"
                        "Vào Lịch sử mua hoặc liên hệ admin nếu cần hỗ trợ."
                    )
                    notifier.queue_user_message(user_id=int(order["user_id"]), kind="preorder_ready", title="Đặt trước đã có hàng", message=msg, product_id=product_id)
        self.log(admin_id, "stock.import", "product", str(product_id), {"submitted": int(parsed["count"]), "inserted": inserted, "detected": parsed.get("detected"), "parser_mode": parser_mode, "duplicate_policy": policy})
        return inserted

    def add_stock_upload(self, product_id: int, *, filename: str, data: bytes, raw_text: str = "", parser_mode: str = "product", admin_id: int | None = None) -> dict[str, object]:
        text = raw_text or self.extract_stock_text_from_upload(filename, data)
        parser_mode, custom_labels = self._resolve_stock_parser(product_id, parser_mode)
        parsed = self.parse_stock_text(text, parser_mode=parser_mode, custom_labels=custom_labels)
        inserted = self.add_stock(product_id, "\n".join(parsed["lines"]), parser_mode="raw", admin_id=admin_id)
        parsed["inserted"] = inserted
        parsed["filename"] = filename
        parsed["parser_mode"] = parser_mode
        return parsed

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

    def _queue_order_notice(self, order_id: int, *, kind: str, title: str, message: str) -> None:
        try:
            with self.db.connect() as conn:
                row = conn.execute(
                    """
                    SELECT o.*, p.name AS product_name
                      FROM orders o JOIN products p ON p.id=o.product_id
                     WHERE o.id=?
                    """,
                    (order_id,),
                ).fetchone()
            if row:
                NotificationService(self.db).queue_user_message(
                    user_id=int(row["user_id"]),
                    kind=kind,
                    title=title,
                    message=message.format(**dict(row)),
                    product_id=int(row["product_id"]),
                )
        except Exception as exc:
            # Admin action must not fail only because Telegram notification queue failed,
            # but the failure must be auditable in a commercial shop.
            try:
                self.log(None, "notification.queue_failed", "order", str(order_id), {"kind": kind, "error": str(exc)})
            except Exception:
                pass

    def cancel_order(self, order_id: int, *, admin_id: int | None = None) -> None:
        self._queue_order_notice(order_id, kind="order_cancelled", title="Đơn hàng đã hủy", message="❌ <b>Đơn hàng {public_code} đã bị hủy bởi admin</b>\n\nSản phẩm: <b>{product_name}</b>\nNếu cần hỗ trợ, vui lòng liên hệ shop.")
        OrderService(self.db).cancel_order(order_id, reason="admin_web_cancel")
        self.log(admin_id, "order.cancel", "order", str(order_id), {})

    def refund_order(self, order_id: int, *, admin_id: int | None = None) -> dict:
        result = OrderService(self.db).refund_to_wallet(order_id, reason="admin_web_refund")
        self._queue_order_notice(order_id, kind="order_refunded", title="Đơn hàng đã hoàn tiền", message="💰 <b>Đơn hàng {public_code} đã được hoàn tiền</b>\n\nSản phẩm: <b>{product_name}</b>\nTiền đã hoàn vào ví của bạn.")
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
        """Resolve admin input to users.id without guessing money targets.

        Safe formats:
        - tg:123456789 or plain 123456789 => Telegram ID
        - id:12 => internal database user id
        - @username / username => username

        Plain numeric input is intentionally treated as Telegram ID only. Falling
        back from a missing Telegram ID to internal id can credit/debit the wrong
        customer when those numbers collide.
        """
        ref = str(user_ref or "").strip()
        if not ref:
            raise ValueError("Vui lòng nhập User: tg:<telegram_id>, @username hoặc id:<id nội bộ>")
        lower = ref.lower()
        with self.db.transaction() as conn:
            if lower.startswith("id:"):
                raw_id = ref.split(":", 1)[1].strip()
                if not raw_id.isdigit():
                    raise ValueError("ID nội bộ không hợp lệ. Ví dụ đúng: id:12")
                row = conn.execute("SELECT id FROM users WHERE id=?", (int(raw_id),)).fetchone()
                if row:
                    return int(row["id"])
                raise ValueError("Không tìm thấy user theo ID nội bộ")

            if lower.startswith("tg:"):
                ref = ref.split(":", 1)[1].strip()
                if not ref.isdigit():
                    raise ValueError("Telegram ID không hợp lệ. Ví dụ đúng: tg:123456789")

            username = ref[1:] if ref.startswith("@") else ref
            row = conn.execute("SELECT id FROM users WHERE telegram_id=?", (ref,)).fetchone()
            if row:
                return int(row["id"])
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if row:
                return int(row["id"])
            if ref.isdigit() and create_if_numeric_telegram:
                cur = conn.execute("INSERT INTO users(telegram_id, username, full_name) VALUES(?,?,?)", (ref, None, None))
                return int(cur.lastrowid)
        raise ValueError("Không tìm thấy người dùng. Hãy nhập tg:<Telegram ID>, @username hoặc id:<ID nội bộ>.")

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
        if result.get("intent", {}).get("order_id"):
            oid = int(result["intent"]["order_id"])
            status = str(result.get("status") or "")
            if status == "order_delivered":
                self._queue_order_notice(oid, kind="order_delivered", title="Đơn hàng đã giao", message="✅ <b>Đơn hàng {public_code} đã được xác nhận thanh toán và giao hàng</b>\n\nSản phẩm: <b>{product_name}</b>\nVào bot để xem/tải lại thông tin hàng nếu cần.")
            else:
                self._queue_order_notice(oid, kind="payment_update", title="Cập nhật thanh toán", message="💳 <b>Thanh toán đơn {public_code} đã được cập nhật</b>\n\nSản phẩm: <b>{product_name}</b>\nTrạng thái mới: " + status)
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

    def list_bank_accounts(self, *, include_disabled: bool = True) -> list[dict]:
        return BankAccountService(self.db).list_accounts(include_disabled=include_disabled)

    def create_bank_account(self, data: dict[str, str], *, admin_id: int | None = None) -> int:
        account_id = BankAccountService(self.db).create(data, admin_id=admin_id)
        self.log(admin_id, "bank_account.create", "bank_account", str(account_id), {"label": data.get("label"), "provider": data.get("provider")})
        return account_id

    def update_bank_account(self, account_id: int, data: dict[str, str], *, admin_id: int | None = None) -> None:
        BankAccountService(self.db).update(account_id, data, admin_id=admin_id)
        self.log(admin_id, "bank_account.update", "bank_account", str(account_id), {"label": data.get("label"), "provider": data.get("provider")})

    def delete_bank_account(self, account_id: int, *, admin_id: int | None = None) -> None:
        BankAccountService(self.db).delete(account_id, admin_id=admin_id)
        self.log(admin_id, "bank_account.delete", "bank_account", str(account_id), {})

    def set_default_bank_account(self, account_id: int, *, admin_id: int | None = None) -> None:
        BankAccountService(self.db).set_default(account_id, admin_id=admin_id)
        self.log(admin_id, "bank_account.default", "bank_account", str(account_id), {})

    def list_preorders(self, status: str | None = None, limit: int = 200) -> list[dict]:
        return PreorderService(self.db).list_preorders(status=status, limit=limit)

    def cancel_preorder(self, preorder_id: int, *, admin_id: int | None = None) -> dict:
        result = PreorderService(self.db, order_expires_minutes=self.order_expires_minutes()).cancel_preorder(preorder_id)
        preorder = result.get("preorder") or {}
        if preorder.get("user_id"):
            refunded = int(result.get("refunded_minor") or 0)
            if refunded > 0:
                msg = f"💰 <b>Đặt trước {preorder.get('public_code')} đã hủy và hoàn cọc</b>\n\nSố tiền hoàn vào ví: <b>{fmt_money(refunded, preorder.get('currency') or 'VND')}</b>."
                kind = "preorder_refunded"
                title = "Đặt trước đã hoàn cọc"
            else:
                msg = f"❌ <b>Đặt trước {preorder.get('public_code')} đã bị hủy</b>."
                kind = "preorder_cancelled"
                title = "Đặt trước đã hủy"
            NotificationService(self.db).queue_user_message(user_id=int(preorder["user_id"]), kind=kind, title=title, message=msg, product_id=int(preorder["product_id"]))
        self.log(admin_id, "preorder.cancel", "preorder", str(preorder_id), {"refunded_minor": result.get("refunded_minor")})
        return result

    def fulfill_preorder(self, preorder_id: int, *, admin_id: int | None = None) -> dict:
        order = PreorderService(self.db, order_expires_minutes=self.order_expires_minutes()).mark_fulfilled(preorder_id)
        remaining = int(order.get("total_amount_minor") or 0)
        if str(order.get("status")) == "delivered" or remaining == 0:
            msg = f"✅ <b>Đặt trước {order.get('preorder_code')} đã được giao</b>\n\nĐơn hàng: <code>{order.get('public_code')}</code>. Vào Lịch sử mua để xem/tải lại hàng."
            kind = "preorder_delivered"
            title = "Đặt trước đã giao hàng"
        else:
            msg = f"📦 <b>Đặt trước {order.get('preorder_code')} đã có hàng</b>\n\nĐơn thanh toán phần còn lại: <code>{order.get('public_code')}</code>\nCòn cần thanh toán: <b>{fmt_money(remaining, order.get('currency') or 'VND')}</b>."
            kind = "preorder_ready"
            title = "Đặt trước đã có hàng"
        NotificationService(self.db).queue_user_message(user_id=int(order["user_id"]), kind=kind, title=title, message=msg, product_id=int(order["product_id"]))
        self.log(admin_id, "preorder.fulfill", "preorder", str(preorder_id), {"order_id": order.get("id"), "order_status": order.get("status")})
        return order

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

    def create_backup(self, *, include_env: bool = False, admin_id: int | None = None) -> Path:
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
            media_dir = self.project_root / "media" / "products"
            if media_dir.exists():
                for media_file in media_dir.rglob("*"):
                    if media_file.is_file():
                        zf.write(media_file, media_file.relative_to(self.project_root).as_posix())
            zf.writestr("RESTORE_GUIDE.txt", "Giải nén file này vào thư mục dự án hoặc dùng trang Backup/Restore của Web Admin. File chính: data/shop.db. Backup cũng chứa media/products nếu bạn đã upload ảnh sản phẩm. Không chia sẻ backup nếu có .env vì chứa token/API key.\n")
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
            safety = self.create_backup(include_env=False, admin_id=admin_id)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = db_path.with_suffix(".restore-tmp")
            with zf.open("data/shop.db") as src, tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            tmp.replace(db_path)
            if ".env" in names:
                with zf.open(".env") as src, (self.project_root / ".env").open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            media_root = (self.project_root / "media" / "products").resolve()
            for name in names:
                if name.startswith("media/products/") and not name.endswith("/"):
                    target = (self.project_root / name).resolve()
                    if not target.is_relative_to(media_root):
                        raise ValueError(f"Backup chứa đường dẫn media không an toàn: {name}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        self.log(admin_id, "backup.restore", "backup", str(path), {"safety_backup": str(safety)})


    # ------------------------------------------------------------------
    # v2.1 operations: status, imports, exports, reconciliation, roles,
    # coupons, delivery logs, webhooks and low-stock monitoring.

    def system_status(self) -> dict[str, object]:
        raw_settings = self.get_settings()
        settings = {k: str(v.get("value", "")) for k, v in raw_settings.items()}
        def token_ok(token: str) -> bool:
            return bool(re.match(r"^\d{6,}:AA[A-Za-z0-9_-]{20,}$", (token or "").strip()))
        db_ok = True
        db_error = ""
        try:
            with self.db.connect() as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # pragma: no cover - defensive
            db_ok = False
            db_error = str(exc)
        low = self.low_stock_items(threshold=int(settings.get("LOW_STOCK_THRESHOLD", "5") or 5))
        return {
            "database_ok": db_ok,
            "database_error": db_error,
            "bot_token_ok": token_ok(settings.get("BOT_TOKEN", "")),
            "bot_configured": bool((settings.get("BOT_TOKEN") or "").strip()),
            "sepay_configured": bool((settings.get("SEPAY_API_KEY") or "").strip()),
            "bank_enabled": str(settings.get("BANK_ENABLED", "false")).lower() == "true",
            "binance_enabled": str(settings.get("BINANCE_PAY_ENABLED", "false")).lower() == "true",
            "binance_configured": bool((settings.get("BINANCE_PAY_API_KEY") or "").strip() and (settings.get("BINANCE_PAY_SECRET_KEY") or "").strip()),
            "backup_dir": str(self.project_root / "backups"),
            "low_stock_count": len(low),
            "low_stock_items": low[:10],
        }

    def check_bot_token(self, token: str) -> dict[str, object]:
        token = (token or "").strip()
        if not token:
            return {"ok": False, "message": "Token trống"}
        if token.startswith("PASTE") or token.startswith("123456789:"):
            return {"ok": False, "message": "Token vẫn là mẫu, chưa phải token thật từ BotFather"}
        if not re.match(r"^\d{6,}:AA[A-Za-z0-9_-]{20,}$", token):
            return {"ok": False, "message": "Token sai định dạng. Token thường có dạng 123456789:AA..."}
        return {"ok": True, "message": "Token đúng định dạng. Muốn kiểm tra live, chạy bot hoặc dùng lệnh getMe."}

    def import_catalog_csv(self, csv_text: str, *, admin_id: int | None = None) -> dict[str, int]:
        """Import categories/products/stock from CSV text.

        Columns: category,name,price,currency,cost,description,warranty_text,stock
        stock can contain many values separated by |; for account lines that use |
        themselves, import stock with textarea/stock page instead.
        """
        if not (csv_text or "").strip():
            raise ValueError("Vui lòng dán nội dung CSV")
        reader = csv.DictReader(io.StringIO(csv_text.strip()))
        required = {"category", "name", "price"}
        if not reader.fieldnames or not required.issubset({h.strip() for h in reader.fieldnames}):
            raise ValueError("CSV cần có cột: category,name,price")
        cats: dict[str, int] = {}
        created_products = 0
        created_stock = 0
        for row in reader:
            category = (row.get("category") or "Khác").strip() or "Khác"
            if category not in cats:
                with self.db.connect() as conn:
                    found = conn.execute("SELECT id FROM categories WHERE LOWER(name)=LOWER(?)", (category,)).fetchone()
                cats[category] = int(found["id"]) if found else self.create_category(category, admin_id=admin_id)
            pid = self.create_product(
                {
                    "category_id": str(cats[category]),
                    "name": row.get("name") or "",
                    "currency": row.get("currency") or "VND",
                    "price": row.get("price") or "0",
                    "cost": row.get("cost") or "0",
                    "description": row.get("description") or "",
                    "warranty_text": row.get("warranty_text") or row.get("warranty") or "",
                },
                admin_id=admin_id,
            )
            created_products += 1
            stock_raw = (row.get("stock") or "").strip()
            if stock_raw:
                # CSV convenience: separate stock items with || or newline-escaped semicolon.
                parts = [x.strip() for x in stock_raw.replace("||", "\n").splitlines() if x.strip()]
                if len(parts) == 1 and ";" in stock_raw:
                    parts = [x.strip() for x in stock_raw.split(";") if x.strip()]
                created_stock += self.add_stock(pid, "\n".join(parts), admin_id=admin_id)
        self.log(admin_id, "catalog.import_csv", "catalog", "", {"products": created_products, "stock": created_stock})
        return {"products": created_products, "stock": created_stock, "categories": len(cats)}

    def _rows_to_csv(self, rows: list[dict]) -> bytes:
        out = io.StringIO()
        if rows:
            writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        else:
            out.write("empty\n")
        return out.getvalue().encode("utf-8-sig")

    def export_report(self, kind: str) -> tuple[str, bytes]:
        kind = (kind or "orders").strip().lower()
        with self.db.connect() as conn:
            if kind == "products":
                rows = [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY id")]
            elif kind == "stock":
                rows = [dict(r) for r in conn.execute("SELECT s.*, p.name AS product_name FROM stock_items s JOIN products p ON p.id=s.product_id ORDER BY s.id")]
            elif kind == "wallets":
                rows = self.list_wallets(limit=100000)
            elif kind == "finance":
                rows = [dict(r) for r in conn.execute("SELECT * FROM cash_ledger ORDER BY id")]
            elif kind == "users":
                rows = self.list_users(limit=100000)
            else:
                kind = "orders"
                rows = self.list_orders(limit=100000)
        return f"nimo-{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv", self._rows_to_csv(rows)

    def list_admin_accounts(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT id, username, role, is_active, created_at, updated_at FROM admin_accounts ORDER BY id")]

    def create_admin_account(self, *, username: str, password: str, role: str, admin_id: int | None = None) -> int:
        username = (username or "").strip()
        if not username or not password:
            raise ValueError("Vui lòng nhập username và mật khẩu")
        role = role if role in {"owner", "finance", "stock", "support", "viewer"} else "viewer"
        with self.db.transaction() as conn:
            cur = conn.execute("INSERT INTO admin_accounts(username,password_hash,role,is_active) VALUES(?,?,?,1)", (username, hash_password(password), role))
            aid = int(cur.lastrowid)
        self.log(admin_id, "admin.create", "admin", str(aid), {"username": username, "role": role})
        return aid

    def update_admin_account(self, account_id: int, *, role: str, is_active: bool, password: str = "", admin_id: int | None = None) -> None:
        role = role if role in {"owner", "finance", "stock", "support", "viewer"} else "viewer"
        with self.db.transaction() as conn:
            if password:
                row = conn.execute("UPDATE admin_accounts SET role=?, is_active=?, password_hash=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (role, 1 if is_active else 0, hash_password(password), account_id)).rowcount
            else:
                row = conn.execute("UPDATE admin_accounts SET role=?, is_active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (role, 1 if is_active else 0, account_id)).rowcount
            if row == 0:
                raise ValueError("Không tìm thấy admin")
        self.log(admin_id, "admin.update", "admin", str(account_id), {"role": role, "active": is_active})

    def list_reconciliation_events(self, status: str = "unmatched", limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM external_payment_events WHERE 1=1"
        params: list[object] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def mark_payment_event_reviewed(self, event_id: int, note: str = "", *, admin_id: int | None = None) -> None:
        with self.db.transaction() as conn:
            current = conn.execute("SELECT raw_json FROM external_payment_events WHERE id=?", (event_id,)).fetchone()
            if not current:
                raise ValueError("Không tìm thấy giao dịch")
            raw = loads(current["raw_json"])
            raw["admin_note"] = note
            row = conn.execute("UPDATE external_payment_events SET status='reviewed', raw_json=? WHERE id=?", (dumps(raw), event_id)).rowcount
            if row == 0:
                raise ValueError("Không tìm thấy giao dịch")
        self.log(admin_id, "payment.review", "external_payment_event", str(event_id), {"note": note})

    def create_coupon(self, data: dict[str, str], *, admin_id: int | None = None) -> int:
        code = (data.get("code") or "").strip().upper()
        if not code:
            raise ValueError("Vui lòng nhập mã coupon")
        dtype = data.get("discount_type") if data.get("discount_type") in {"percent", "fixed"} else "fixed"
        currency = normalize_currency(data.get("currency") or "VND")
        value = int(to_minor(data.get("discount_value") or "0", currency) if dtype == "fixed" else int(data.get("discount_value") or "0"))
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO coupons(code,discount_type,discount_value,currency,max_uses,is_active,expires_at,note) VALUES(?,?,?,?,?,?,?,?)",
                (code, dtype, value, currency, int(data.get("max_uses") or 0), 1 if str(data.get("is_active") or "on").lower() in {"on","1","true","yes"} else 0, (data.get("expires_at") or "").strip() or None, data.get("note") or ""),
            )
            cid = int(cur.lastrowid)
        self.log(admin_id, "coupon.create", "coupon", str(cid), {"code": code})
        return cid

    def list_coupons(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM coupons ORDER BY id DESC")]

    def update_coupon(self, coupon_id: int, data: dict[str, str], *, admin_id: int | None = None) -> None:
        dtype = data.get("discount_type") if data.get("discount_type") in {"percent", "fixed"} else "fixed"
        currency = normalize_currency(data.get("currency") or "VND")
        value = int(to_minor(data.get("discount_value") or "0", currency) if dtype == "fixed" else int(data.get("discount_value") or "0"))
        with self.db.transaction() as conn:
            row = conn.execute(
                "UPDATE coupons SET code=?, discount_type=?, discount_value=?, currency=?, max_uses=?, is_active=?, expires_at=?, note=? WHERE id=?",
                ((data.get("code") or "").strip().upper(), dtype, value, currency, int(data.get("max_uses") or 0), 1 if str(data.get("is_active") or "").lower() in {"on","1","true","yes"} else 0, (data.get("expires_at") or "").strip() or None, data.get("note") or "", coupon_id),
            ).rowcount
            if row == 0:
                raise ValueError("Không tìm thấy coupon")
        self.log(admin_id, "coupon.update", "coupon", str(coupon_id), {})

    def delete_coupon(self, coupon_id: int, *, admin_id: int | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM coupons WHERE id=?", (coupon_id,))
        self.log(admin_id, "coupon.delete", "coupon", str(coupon_id), {})

    def log_delivery_download(self, *, order_id: int | None, user_id: int | None, source: str, filename: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO delivery_download_logs(order_id,user_id,source,filename) VALUES(?,?,?,?)", (order_id, user_id, source, filename))

    def list_delivery_downloads(self, limit: int = 200) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT d.*, o.public_code, u.telegram_id, u.username
                  FROM delivery_download_logs d
                  LEFT JOIN orders o ON o.id=d.order_id
                  LEFT JOIN users u ON u.id=d.user_id
                 ORDER BY d.id DESC LIMIT ?
                """, (limit,))]

    def low_stock_items(self, threshold: int = 5) -> list[dict]:
        threshold = max(0, int(threshold))
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT p.id AS product_id, p.name, p.is_active,
                       COALESCE(SUM(CASE WHEN s.status='available' THEN 1 ELSE 0 END),0) AS available
                  FROM products p LEFT JOIN stock_items s ON s.product_id=p.id
                 WHERE p.is_active=1
                 GROUP BY p.id
                HAVING available <= ?
                 ORDER BY available ASC, p.id DESC
                """, (threshold,))]

    def queue_low_stock_notifications(self, threshold: int = 5, *, admin_id: int | None = None) -> int:
        items = self.low_stock_items(threshold)
        created = 0
        for item in items:
            title = f"⚠️ Sắp hết hàng: {item['name']}"
            message = f"⚠️ <b>Sắp hết hàng</b>\nSản phẩm: <b>{item['name']}</b>\nCòn: <b>{item['available']}</b> dòng. Vui lòng nhập thêm kho."
            NotificationService(self.db).queue_product_update(product_id=int(item["product_id"]), title=title, message=message)
            created += 1
        self.log(admin_id, "stock.low_stock_notify", "stock", "", {"threshold": threshold, "count": created})
        return created

    def ingest_webhook_event(self, *, provider: str, tx_id: str, amount: str, currency: str, description: str, raw: dict | None = None) -> dict:
        amount_minor = to_minor(amount, currency)
        try:
            return PaymentService(self.db).confirm_provider_transaction(provider=provider, provider_tx_id=tx_id, amount_minor=amount_minor, currency=currency, description=description, raw=raw or {})
        except Exception as exc:
            # Persist a reviewable event when no payment code is found or no intent matches.
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO external_payment_events(provider, provider_tx_id, payment_code, currency, amount_minor, status, raw_json) VALUES(?,?,?,?,?,'unmatched',?)",
                    (provider, tx_id, (raw or {}).get("payment_code") or "", normalize_currency(currency), amount_minor, dumps({"error": str(exc), **(raw or {})})),
                )
            return {"status": "unmatched", "error": str(exc)}

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
