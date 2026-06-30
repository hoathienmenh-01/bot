from __future__ import annotations

import secrets

from nimo_shop.db import Database
from nimo_shop.bot.i18n import normalize_lang, SUPPORTED_LANGUAGES


class UserService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_or_create(self, telegram_id: int | str, username: str | None = None, full_name: str | None = None) -> int:
        tg = str(telegram_id)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM users WHERE telegram_id=?", (tg,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE users SET username=COALESCE(?, username), full_name=COALESCE(?, full_name) WHERE id=?",
                    (username, full_name, row["id"]),
                )
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO users(telegram_id, username, full_name) VALUES(?,?,?)",
                (tg, username, full_name),
            )
            return int(cur.lastrowid)

    def get_profile(self, telegram_id: int | str) -> dict | None:
        with self.db.connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (str(telegram_id),)).fetchone()
            if not user:
                return None
            balances = conn.execute(
                "SELECT currency, balance_minor FROM wallet_balances WHERE user_id=? ORDER BY currency",
                (user["id"],),
            ).fetchall()
            orders = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(total_amount_minor),0) AS total FROM orders WHERE user_id=? AND status IN ('paid','delivered')",
                (user["id"],),
            ).fetchone()
            return {
                "user": dict(user),
                "balances": {r["currency"]: int(r["balance_minor"]) for r in balances},
                "order_count": int(orders["c"]),
                "total_spent_minor": int(orders["total"]),
            }

    def get_language(self, user_id: int) -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT language FROM users WHERE id=?", (user_id,)).fetchone()
            return normalize_lang(row["language"] if row else "vi")

    def set_language(self, user_id: int, language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("unsupported language")
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET language=? WHERE id=?", (language, user_id))

    @staticmethod
    def new_api_key() -> str:
        return "tgb_" + secrets.token_hex(32)

    def ensure_api_key(self, user_id: int) -> str:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT api_key FROM users WHERE id=?", (user_id,)).fetchone()
            if row and row["api_key"]:
                return str(row["api_key"])
            key = self.new_api_key()
            conn.execute("UPDATE users SET api_key=?, api_key_created_at=CURRENT_TIMESTAMP WHERE id=?", (key, user_id))
            return key

    def rotate_api_key(self, user_id: int) -> str:
        key = self.new_api_key()
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET api_key=?, api_key_created_at=CURRENT_TIMESTAMP WHERE id=?", (key, user_id))
        return key

    def find_by_api_key(self, api_key: str) -> dict | None:
        key = (api_key or "").strip()
        if not key:
            return None
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE api_key=? AND is_banned=0", (key,)).fetchone()
            return dict(row) if row else None
