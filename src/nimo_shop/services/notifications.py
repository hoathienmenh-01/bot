from __future__ import annotations

from nimo_shop.db import Database


class NotificationService:
    """Queue Telegram notifications for the bot process to deliver.

    target_user_id = NULL means broadcast to every non-banned user.
    target_user_id set means send only to that buyer.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def queue_product_update(self, *, product_id: int, title: str, message: str) -> int:
        if not title.strip() or not message.strip():
            raise ValueError("notification title/message is required")
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO bot_notifications(kind, title, message, product_id, target_user_id)
                VALUES('product_update', ?, ?, ?, NULL)
                """,
                (title.strip(), message.strip(), product_id),
            )
            return int(cur.lastrowid)

    def queue_user_message(self, *, user_id: int, kind: str, title: str, message: str, product_id: int | None = None) -> int:
        if not title.strip() or not message.strip():
            raise ValueError("notification title/message is required")
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO bot_notifications(kind, title, message, product_id, target_user_id)
                VALUES(?,?,?,?,?)
                """,
                ((kind or "user_message").strip(), title.strip(), message.strip(), product_id, user_id),
            )
            return int(cur.lastrowid)

    def pending(self, limit: int = 10) -> list[dict]:
        with self.db.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM bot_notifications WHERE status='pending' ORDER BY id LIMIT ?",
                    (limit,),
                )
            ]

    def mark_sent(self, notification_id: int, sent_count: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE bot_notifications SET status='sent', sent_count=?, sent_at=CURRENT_TIMESTAMP, error='' WHERE id=?",
                (sent_count, notification_id),
            )

    def mark_failed(self, notification_id: int, error: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE bot_notifications SET status='failed', error=? WHERE id=?",
                (error[:500], notification_id),
            )

    def recipients(self, notification: dict | None = None, limit: int = 5000) -> list[str]:
        target_user_id = int((notification or {}).get("target_user_id") or 0)
        with self.db.connect() as conn:
            if target_user_id:
                row = conn.execute("SELECT telegram_id FROM users WHERE id=? AND is_banned=0", (target_user_id,)).fetchone()
                return [str(row["telegram_id"])] if row else []
            return [
                str(r["telegram_id"])
                for r in conn.execute(
                    "SELECT telegram_id FROM users WHERE is_banned=0 ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            ]
