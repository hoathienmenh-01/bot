from __future__ import annotations

from nimo_shop.db import Database


class NotificationService:
    """Queue and send bot-facing admin notifications.

    Web Admin and Telegram bot usually run in separate processes. Web actions
    therefore create rows in bot_notifications; the bot process polls/sends
    those rows when it is online. This avoids trying to send Telegram messages
    directly from a web request.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def queue_product_update(self, *, product_id: int, title: str, message: str) -> int:
        if not title.strip() or not message.strip():
            raise ValueError("notification title/message is required")
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO bot_notifications(kind, title, message, product_id)
                VALUES('product_update', ?, ?, ?)
                """,
                (title.strip(), message.strip(), product_id),
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

    def recipients(self, limit: int = 5000) -> list[str]:
        with self.db.connect() as conn:
            return [
                str(r["telegram_id"])
                for r in conn.execute(
                    "SELECT telegram_id FROM users WHERE is_banned=0 ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            ]
