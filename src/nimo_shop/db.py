from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = r"""
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id TEXT NOT NULL UNIQUE,
    username TEXT,
    full_name TEXT,
    language TEXT NOT NULL DEFAULT 'vi',
    is_banned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wallet_balances (
    user_id INTEGER NOT NULL,
    currency TEXT NOT NULL,
    balance_minor INTEGER NOT NULL DEFAULT 0 CHECK(balance_minor >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, currency),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    direction TEXT NOT NULL CHECK(direction IN ('credit','debit')),
    currency TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
    balance_after_minor INTEGER,
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 100,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'VND',
    price_minor INTEGER NOT NULL CHECK(price_minor >= 0),
    warranty_text TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    cost_minor INTEGER NOT NULL DEFAULT 0,
    stock_format TEXT NOT NULL DEFAULT 'auto',
    stock_format_labels TEXT NOT NULL DEFAULT '',
    stock_format_example TEXT NOT NULL DEFAULT '',
    delivery_format TEXT NOT NULL DEFAULT 'auto',
    product_icon TEXT NOT NULL DEFAULT '',
    product_custom_emoji_id TEXT NOT NULL DEFAULT '',
    product_image_path TEXT NOT NULL DEFAULT '',
    product_image_file_id TEXT NOT NULL DEFAULT '',
    product_short_description TEXT NOT NULL DEFAULT '',
    product_long_description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('available','reserved','sold')) DEFAULT 'available',
    reserved_by_user_id INTEGER,
    reserved_order_id INTEGER,
    reserved_until TEXT,
    sold_order_id INTEGER,
    sold_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY(reserved_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE(product_id, content)
);
CREATE INDEX IF NOT EXISTS idx_stock_available ON stock_items(product_id, status);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_code TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    currency TEXT NOT NULL,
    unit_amount_minor INTEGER NOT NULL CHECK(unit_amount_minor >= 0),
    total_amount_minor INTEGER NOT NULL CHECK(total_amount_minor >= 0),
    status TEXT NOT NULL CHECK(status IN ('awaiting_payment','paid','delivered','cancelled','refunded')),
    payment_method TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at TEXT,
    delivered_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_orders_user_status ON orders(user_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_expires ON orders(status, expires_at);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    stock_item_id INTEGER NOT NULL,
    delivered_content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(order_id, stock_item_id),
    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY(stock_item_id) REFERENCES stock_items(id)
);
CREATE TABLE IF NOT EXISTS payment_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_code TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    order_id INTEGER,
    provider TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    status TEXT NOT NULL CHECK(status IN ('pending','confirmed','expired','rejected')) DEFAULT 'pending',
    provider_ref TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_payment_intent_code ON payment_intents(public_code, status);
CREATE INDEX IF NOT EXISTS idx_payment_intent_order ON payment_intents(order_id, status);
CREATE TABLE IF NOT EXISTS external_payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_tx_id TEXT NOT NULL,
    payment_code TEXT,
    currency TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
    status TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, provider_tx_id)
);
CREATE TABLE IF NOT EXISTS cash_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('in','out','internal')),
    currency TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
    fee_minor INTEGER NOT NULL DEFAULT 0 CHECK(fee_minor >= 0),
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    order_id INTEGER,
    subject TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS bot_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    product_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed')),
    sent_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_notifications_status ON bot_notifications(status, id);

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


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):  # noqa: ANN001
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        # Lightweight migrations for existing SQLite databases. CREATE TABLE
        # does not add new columns to an already-created table, so add only
        # the missing columns needed by newer web-admin versions.
        product_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(products)")}
        migrations = {
            "stock_format": "ALTER TABLE products ADD COLUMN stock_format TEXT NOT NULL DEFAULT 'auto'",
            "stock_format_labels": "ALTER TABLE products ADD COLUMN stock_format_labels TEXT NOT NULL DEFAULT ''",
            "stock_format_example": "ALTER TABLE products ADD COLUMN stock_format_example TEXT NOT NULL DEFAULT ''",
            "delivery_format": "ALTER TABLE products ADD COLUMN delivery_format TEXT NOT NULL DEFAULT 'auto'",
            "product_icon": "ALTER TABLE products ADD COLUMN product_icon TEXT NOT NULL DEFAULT ''",
            "product_custom_emoji_id": "ALTER TABLE products ADD COLUMN product_custom_emoji_id TEXT NOT NULL DEFAULT ''",
            "product_image_path": "ALTER TABLE products ADD COLUMN product_image_path TEXT NOT NULL DEFAULT ''",
            "product_image_file_id": "ALTER TABLE products ADD COLUMN product_image_file_id TEXT NOT NULL DEFAULT ''",
            "product_short_description": "ALTER TABLE products ADD COLUMN product_short_description TEXT NOT NULL DEFAULT ''",
            "product_long_description": "ALTER TABLE products ADD COLUMN product_long_description TEXT NOT NULL DEFAULT ''",
        }
        for col, sql in migrations.items():
            if col not in product_cols:
                conn.execute(sql)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def dumps(data: dict | list | None) -> str:
    return json.dumps(data or {}, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None) -> dict:
    if not value:
        return {}
    return json.loads(value)
