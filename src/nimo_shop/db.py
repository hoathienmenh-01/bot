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
    api_key TEXT UNIQUE,
    api_key_created_at TEXT,
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
    category_icon TEXT NOT NULL DEFAULT '📁',
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
    FOREIGN KEY(reserved_by_user_id) REFERENCES users(id) ON DELETE SET NULL
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
    preorder_id INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_orders_user_status ON orders(user_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_expires ON orders(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_orders_preorder_status ON orders(preorder_id, status);
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

CREATE TABLE IF NOT EXISTS preorders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_code TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    currency TEXT NOT NULL,
    unit_amount_minor INTEGER NOT NULL CHECK(unit_amount_minor >= 0),
    total_amount_minor INTEGER NOT NULL CHECK(total_amount_minor >= 0),
    deposit_percent INTEGER NOT NULL DEFAULT 10 CHECK(deposit_percent >= 0 AND deposit_percent <= 100),
    deposit_amount_minor INTEGER NOT NULL DEFAULT 0 CHECK(deposit_amount_minor >= 0),
    status TEXT NOT NULL DEFAULT 'awaiting_deposit' CHECK(status IN ('awaiting_deposit','active','fulfilled','cancelled','refunded')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at TEXT,
    fulfilled_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_preorders_user_status ON preorders(user_id, status);
CREATE INDEX IF NOT EXISTS idx_preorders_product_status ON preorders(product_id, status);

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
    target_user_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed')),
    sent_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL,
    FOREIGN KEY(target_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_notifications_status ON bot_notifications(status, id);

CREATE TABLE IF NOT EXISTS bank_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    bank_name TEXT NOT NULL DEFAULT '',
    bank_bin TEXT NOT NULL,
    account_no TEXT NOT NULL,
    account_name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'sepay' CHECK(provider IN ('sepay','pay2s','casso','custom','manual')),
    api_key TEXT NOT NULL DEFAULT '',
    api_secret TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    poll_seconds INTEGER NOT NULL DEFAULT 30,
    is_default INTEGER NOT NULL DEFAULT 0,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bank_accounts_enabled ON bank_accounts(is_enabled, is_default, id);

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
            # Existing SQLite databases may miss columns that are referenced by
            # indexes in SCHEMA. Add those columns before executescript runs so
            # CREATE INDEX IF NOT EXISTS does not fail during commercial upgrades.
            self._pre_schema_migrate(conn)
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _pre_schema_migrate(conn: sqlite3.Connection) -> None:
        if Database._table_exists(conn, "orders"):
            order_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(orders)")}
            if "preorder_id" not in order_cols:
                conn.execute("ALTER TABLE orders ADD COLUMN preorder_id INTEGER")

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        # Lightweight migrations for existing SQLite databases. CREATE TABLE
        # does not add new columns to an already-created table, so add only
        # the missing columns needed by newer web-admin versions.
        user_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(users)")}
        if "api_key" not in user_cols:
            # SQLite cannot add a UNIQUE column with ALTER TABLE on an existing
            # database. Add it as a plain nullable column, then enforce
            # uniqueness with a partial unique index below.
            conn.execute("ALTER TABLE users ADD COLUMN api_key TEXT")
        if "api_key_created_at" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN api_key_created_at TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_key_unique ON users(api_key) WHERE api_key IS NOT NULL")


        notif_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(bot_notifications)")}
        if "target_user_id" not in notif_cols:
            conn.execute("ALTER TABLE bot_notifications ADD COLUMN target_user_id INTEGER")
        if "metadata_json" not in notif_cols:
            conn.execute("ALTER TABLE bot_notifications ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")

        category_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(categories)")}
        if "category_icon" not in category_cols:
            conn.execute("ALTER TABLE categories ADD COLUMN category_icon TEXT NOT NULL DEFAULT '📁'")

        # v2.6 allows optional duplicate inventory rows. Older databases had a
        # UNIQUE(product_id, content) table constraint, which cannot be dropped
        # with ALTER TABLE, so recreate the table once while preserving IDs and
        # delivery references.
        stock_sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='stock_items'").fetchone()
        stock_sql = str(stock_sql_row[0] if stock_sql_row else "")
        if "UNIQUE(product_id, content)" in stock_sql:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_items_v26 (
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
                    FOREIGN KEY(reserved_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
            """)
            conn.execute("""
                INSERT INTO stock_items_v26(id, product_id, content, status, reserved_by_user_id, reserved_order_id, reserved_until, sold_order_id, sold_at, created_at)
                SELECT id, product_id, content, status, reserved_by_user_id, reserved_order_id, reserved_until, sold_order_id, sold_at, created_at FROM stock_items
            """)
            conn.execute("DROP TABLE stock_items")
            conn.execute("ALTER TABLE stock_items_v26 RENAME TO stock_items")
            conn.execute("PRAGMA foreign_keys=ON")



        bank_sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bank_accounts'").fetchone()
        bank_sql = str(bank_sql_row[0] if bank_sql_row else "")
        if "CHECK(provider IN ('sepay','casso','custom','manual'))" in bank_sql and "pay2s" not in bank_sql:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bank_accounts_v2811 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    bank_name TEXT NOT NULL DEFAULT '',
                    bank_bin TEXT NOT NULL,
                    account_no TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'sepay' CHECK(provider IN ('sepay','pay2s','casso','custom','manual')),
                    api_key TEXT NOT NULL DEFAULT '',
                    api_secret TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL DEFAULT '',
                    poll_seconds INTEGER NOT NULL DEFAULT 30,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                INSERT INTO bank_accounts_v2811(id, label, bank_name, bank_bin, account_no, account_name, provider, api_key, api_secret, base_url, poll_seconds, is_default, is_enabled, notes, created_at, updated_at)
                SELECT id, label, bank_name, bank_bin, account_no, account_name, provider, api_key, api_secret, base_url, poll_seconds, is_default, is_enabled, notes, created_at, updated_at FROM bank_accounts
            """)
            conn.execute("DROP TABLE bank_accounts")
            conn.execute("ALTER TABLE bank_accounts_v2811 RENAME TO bank_accounts")
            conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bank_accounts_enabled ON bank_accounts(is_enabled, is_default, id)")

        order_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(orders)")}
        if "preorder_id" not in order_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN preorder_id INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_preorder_status ON orders(preorder_id, status)")

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
