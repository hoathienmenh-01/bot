from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from nimo_shop.db import Database
from nimo_shop.services.wallet import WalletService


class OutOfStock(Exception):
    pass


class OrderStateError(Exception):
    pass


class OrderOwnershipError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def public_code(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4).upper()}"


class OrderService:
    def __init__(self, db: Database, order_expires_minutes: int = 20) -> None:
        self.db = db
        self.order_expires_minutes = order_expires_minutes

    def create_order(self, *, user_id: int, product_id: int, quantity: int = 1) -> dict:
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        expires_at = iso(utcnow() + timedelta(minutes=self.order_expires_minutes))
        code = public_code("ORD")
        with self.db.transaction() as conn:
            product = conn.execute("SELECT * FROM products WHERE id=? AND is_active=1", (product_id,)).fetchone()
            if not product:
                raise ValueError("product not found or inactive")
            rows = conn.execute(
                "SELECT id FROM stock_items WHERE product_id=? AND status='available' ORDER BY id LIMIT ?",
                (product_id, quantity),
            ).fetchall()
            if len(rows) < quantity:
                raise OutOfStock("not enough stock")
            total = int(product["price_minor"]) * quantity
            cur = conn.execute(
                """
                INSERT INTO orders(public_code, user_id, product_id, quantity, currency, unit_amount_minor, total_amount_minor, status, expires_at)
                VALUES(?,?,?,?,?,?,?,'awaiting_payment',?)
                """,
                (code, user_id, product_id, quantity, product["currency"], product["price_minor"], total, expires_at),
            )
            order_id = int(cur.lastrowid)
            ids = [int(r["id"]) for r in rows]
            updated = 0
            for sid in ids:
                updated += conn.execute(
                    """
                    UPDATE stock_items
                       SET status='reserved', reserved_by_user_id=?, reserved_order_id=?, reserved_until=?
                     WHERE id=? AND status='available'
                    """,
                    (user_id, order_id, expires_at, sid),
                ).rowcount
            if updated != quantity:
                raise OutOfStock("stock was reserved by another transaction")
            return self._get_order_in_conn(conn, order_id)

    @staticmethod
    def _get_order_in_conn(conn, order_id: int) -> dict:
        order = conn.execute(
            """
            SELECT o.*, p.name AS product_name, p.warranty_text
              FROM orders o JOIN products p ON p.id=o.product_id
             WHERE o.id=?
            """,
            (order_id,),
        ).fetchone()
        if not order:
            raise ValueError("order not found")
        return dict(order)

    @staticmethod
    def _is_expired(order: dict) -> bool:
        return str(order["expires_at"]) < iso(utcnow())

    def get_order(self, order_id: int) -> dict:
        with self.db.connect() as conn:
            return self._get_order_in_conn(conn, order_id)

    @staticmethod
    def _assert_owner(order: dict, expected_user_id: int | None) -> None:
        if expected_user_id is not None and int(order["user_id"]) != int(expected_user_id):
            raise OrderOwnershipError("order does not belong to this user")

    def pay_with_wallet(self, order_id: int, *, expected_user_id: int | None = None) -> dict:
        with self.db.transaction() as conn:
            order = self._get_order_in_conn(conn, order_id)
            self._assert_owner(order, expected_user_id)
            if order["status"] == "delivered":
                return {"order": order, "delivery": self._delivery_in_conn(conn, order_id)}
            if order["status"] != "awaiting_payment":
                raise OrderStateError(f"cannot pay order in state {order['status']}")
            if self._is_expired(order):
                self._cancel_in_conn(conn, order_id, "expired")
                raise OrderStateError("order expired")
            WalletService.debit_in_conn(
                conn,
                user_id=int(order["user_id"]),
                currency=order["currency"],
                amount_minor=int(order["total_amount_minor"]),
                reference_type="order",
                reference_id=order["public_code"],
                idempotency_key=f"wallet-pay:{order['public_code']}",
                metadata={"order_id": order_id},
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO cash_ledger(event_type, provider, direction, currency, amount_minor, reference_type, reference_id, idempotency_key, note)
                VALUES('sale', 'wallet', 'internal', ?, ?, 'order', ?, ?, 'Paid from user wallet')
                """,
                (order["currency"], order["total_amount_minor"], order["public_code"], f"cash-sale-wallet:{order['public_code']}"),
            )
            delivery = self._mark_paid_and_deliver_in_conn(conn, order_id, "wallet")
            return {"order": self._get_order_in_conn(conn, order_id), "delivery": delivery}

    @staticmethod
    def _mark_paid_and_deliver_in_conn(conn, order_id: int, payment_method: str) -> list[dict]:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise ValueError("order not found")
        order_dict = dict(order)
        if order["status"] == "delivered":
            return OrderService._delivery_in_conn(conn, order_id)
        if order["status"] != "awaiting_payment":
            raise OrderStateError(f"cannot deliver order in state {order['status']}")
        if OrderService._is_expired(order_dict):
            OrderService._cancel_in_conn(conn, order_id, "expired")
            raise OrderStateError("order expired")
        stock = conn.execute(
            "SELECT id, content FROM stock_items WHERE reserved_order_id=? AND status='reserved' ORDER BY id",
            (order_id,),
        ).fetchall()
        if len(stock) != int(order["quantity"]):
            raise RuntimeError("reserved stock count mismatch")
        now = iso(utcnow())
        for item in stock:
            conn.execute(
                """
                UPDATE stock_items
                   SET status='sold', sold_order_id=?, sold_at=?, reserved_by_user_id=NULL, reserved_order_id=NULL, reserved_until=NULL
                 WHERE id=? AND status='reserved'
                """,
                (order_id, now, item["id"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO deliveries(order_id, stock_item_id, delivered_content) VALUES(?,?,?)",
                (order_id, item["id"], item["content"]),
            )
        conn.execute(
            "UPDATE orders SET status='delivered', payment_method=?, paid_at=COALESCE(paid_at, ?), delivered_at=? WHERE id=? AND status='awaiting_payment'",
            (payment_method, now, now, order_id),
        )
        return OrderService._delivery_in_conn(conn, order_id)

    @staticmethod
    def _delivery_in_conn(conn, order_id: int) -> list[dict]:
        return [dict(r) for r in conn.execute("SELECT * FROM deliveries WHERE order_id=? ORDER BY id", (order_id,))]

    def cancel_order(self, order_id: int, reason: str = "cancelled", *, expected_user_id: int | None = None) -> None:
        with self.db.transaction() as conn:
            order = self._get_order_in_conn(conn, order_id)
            self._assert_owner(order, expected_user_id)
            self._cancel_in_conn(conn, order_id, reason)

    @staticmethod
    def _cancel_in_conn(conn, order_id: int, reason: str = "cancelled") -> None:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order or order["status"] != "awaiting_payment":
            return
        conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
        conn.execute(
            """
            UPDATE stock_items
               SET status='available', reserved_by_user_id=NULL, reserved_order_id=NULL, reserved_until=NULL
             WHERE reserved_order_id=? AND status='reserved'
            """,
            (order_id,),
        )

    def refund_to_wallet(self, order_id: int, reason: str = "admin_refund") -> dict:
        """Refund a delivered/paid order to the buyer wallet exactly once.

        This does not unsell delivered stock; it records a financial refund and
        marks the order as refunded, which keeps audit history stable.
        """
        with self.db.transaction() as conn:
            order = self._get_order_in_conn(conn, order_id)
            if order["status"] == "refunded":
                return {"order": order, "balance_after_minor": WalletService._balance(conn, int(order["user_id"]), order["currency"])}
            if order["status"] not in {"paid", "delivered"}:
                raise OrderStateError(f"cannot refund order in state {order['status']}")
            balance = WalletService.credit_in_conn(
                conn,
                user_id=int(order["user_id"]),
                currency=order["currency"],
                amount_minor=int(order["total_amount_minor"]),
                reference_type="order_refund",
                reference_id=order["public_code"],
                idempotency_key=f"wallet-refund:{order['public_code']}",
                metadata={"reason": reason, "order_id": order_id},
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO cash_ledger(event_type, provider, direction, currency, amount_minor, reference_type, reference_id, idempotency_key, note)
                VALUES('refund', 'wallet', 'internal', ?, ?, 'order', ?, ?, ?)
                """,
                (order["currency"], order["total_amount_minor"], order["public_code"], f"cash-refund-wallet:{order['public_code']}", reason),
            )
            conn.execute("UPDATE orders SET status='refunded' WHERE id=?", (order_id,))
            return {"order": self._get_order_in_conn(conn, order_id), "balance_after_minor": balance}

    def sweep_expired(self) -> int:
        now = iso(utcnow())
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT id FROM orders WHERE status='awaiting_payment' AND expires_at < ?", (now,)).fetchall()
            for row in rows:
                self._cancel_in_conn(conn, int(row["id"]), "expired")
            return len(rows)

    def order_history(self, user_id: int, limit: int = 20) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT o.*, p.name AS product_name
                  FROM orders o JOIN products p ON p.id=o.product_id
                 WHERE o.user_id=?
                 ORDER BY o.id DESC LIMIT ?
                """,
                (user_id, limit),
            )]
