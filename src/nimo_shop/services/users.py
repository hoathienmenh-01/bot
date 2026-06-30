from __future__ import annotations

from nimo_shop.db import Database


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

    def set_language(self, user_id: int, language: str) -> None:
        if language not in {"vi", "en", "zh"}:
            raise ValueError("unsupported language")
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET language=? WHERE id=?", (language, user_id))
