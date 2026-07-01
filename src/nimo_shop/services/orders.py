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
    return f"{prefix}{secrets.token_hex(8).upper()}"


class OrderService:
    def __init__(self, db: Database, order_expires_minutes: int = 20) -> None:
        self.db = db
        self.order_expires_minutes = order_expires_minutes

    def create_order(self, *, user_id: int, product_id: int, quantity: int = 1) -> dict:
        expires_at = iso(utcnow() + timedelta(minutes=self.order_expires_minutes))
        with self.db.transaction() as conn:
            return self._create_order_in_conn(
                conn,
                user_id=user_id,
                product_id=product_id,
                quantity=quantity,
                expires_at=expires_at,
            )

    @staticmethod
    def _create_order_in_conn(
        conn,
        *,
        user_id: int,
        product_id: int,
        quantity: int = 1,
        expires_at: str,
        public_code_value: str | None = None,
        payment_method: str | None = None,
        total_amount_minor: int | None = None,
        unit_amount_minor: int | None = None,
        preorder_id: int | None = None,
    ) -> dict:
        """Create an awaiting-payment order and reserve stock inside caller's transaction.

        Keeping this transaction-aware helper prevents commercial split-brain bugs
        where stock is reserved in one transaction but payment/delivery happens in
        another transaction that may crash before completion.
        """
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        buyer = conn.execute("SELECT is_banned FROM users WHERE id=?", (user_id,)).fetchone()
        if not buyer or int(buyer["is_banned"] or 0):
            raise PermissionError("user is banned or does not exist")
        product = conn.execute("SELECT * FROM products WHERE id=? AND is_active=1", (product_id,)).fetchone()
        if not product:
            raise ValueError("product not found or inactive")
        rows = conn.execute(
            "SELECT id FROM stock_items WHERE product_id=? AND status='available' ORDER BY id LIMIT ?",
            (product_id, quantity),
        ).fetchall()
        if len(rows) < quantity:
            raise OutOfStock("not enough stock")
        total = int(product["price_minor"]) * quantity if total_amount_minor is None else int(total_amount_minor)
        if total < 0:
            raise ValueError("total_amount_minor must be >= 0")
        unit = int(product["price_minor"]) if unit_amount_minor is None else int(unit_amount_minor)
        if unit < 0:
            raise ValueError("unit_amount_minor must be >= 0")
        code = public_code_value or public_code("ORD")
        cur = conn.execute(
            """
            INSERT INTO orders(public_code, user_id, product_id, quantity, currency, unit_amount_minor, total_amount_minor, status, payment_method, expires_at, preorder_id)
            VALUES(?,?,?,?,?,?,?,'awaiting_payment',?,?,?)
            """,
            (code, user_id, product_id, quantity, product["currency"], unit, total, payment_method, expires_at, preorder_id),
        )
        order_id = int(cur.lastrowid)
        updated = 0
        for item in rows:
            updated += conn.execute(
                """
                UPDATE stock_items
                   SET status='reserved', reserved_by_user_id=?, reserved_order_id=?, reserved_until=?
                 WHERE id=? AND status='available'
                """,
                (user_id, order_id, expires_at, int(item["id"])),
            ).rowcount
        if updated != quantity:
            raise OutOfStock("stock was reserved by another transaction")
        return OrderService._get_order_in_conn(conn, order_id)

    @staticmethod
    def _get_order_in_conn(conn, order_id: int) -> dict:
        order = conn.execute(
            """
            SELECT o.*, p.name AS product_name, p.warranty_text, p.stock_format, p.stock_format_labels, p.delivery_format
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

    def get_order_by_public_code(self, public_code: str, *, expected_user_id: int | None = None) -> dict:
        code = (public_code or "").strip().upper()
        if not code:
            raise ValueError("order code is required")
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT o.id
                  FROM orders o
                 WHERE UPPER(o.public_code)=?
                """,
                (code,),
            ).fetchone()
            if not row:
                raise ValueError("order not found")
            order = self._get_order_in_conn(conn, int(row["id"]))
            self._assert_owner(order, expected_user_id)
            return order

    def delivery_for_order(self, order_id: int, *, expected_user_id: int | None = None) -> list[dict]:
        with self.db.connect() as conn:
            order = self._get_order_in_conn(conn, order_id)
            self._assert_owner(order, expected_user_id)
            return self._delivery_in_conn(conn, order_id)

    @staticmethod
    def _assert_owner(order: dict, expected_user_id: int | None) -> None:
        if expected_user_id is not None and int(order["user_id"]) != int(expected_user_id):
            raise OrderOwnershipError("order does not belong to this user")

    def pay_with_wallet(self, order_id: int, *, expected_user_id: int | None = None) -> dict:
        with self.db.transaction() as conn:
            return self._pay_with_wallet_in_conn(conn, order_id, expected_user_id=expected_user_id)

    def purchase_with_wallet(self, *, user_id: int, product_id: int, quantity: int = 1) -> dict:
        """Atomically create, pay and deliver a wallet order.

        Buyer API uses this path so a crash cannot leave a paid purchase split
        across two independent transactions.
        """
        expires_at = iso(utcnow() + timedelta(minutes=self.order_expires_minutes))
        with self.db.transaction() as conn:
            order = self._create_order_in_conn(
                conn,
                user_id=user_id,
                product_id=product_id,
                quantity=quantity,
                expires_at=expires_at,
            )
            return self._pay_with_wallet_in_conn(conn, int(order["id"]), expected_user_id=user_id)

    @staticmethod
    def _pay_with_wallet_in_conn(conn, order_id: int, *, expected_user_id: int | None = None) -> dict:
        order = OrderService._get_order_in_conn(conn, order_id)
        OrderService._assert_owner(order, expected_user_id)
        if order["status"] == "delivered":
            return {"order": order, "delivery": OrderService._delivery_in_conn(conn, order_id)}
        if order["status"] != "awaiting_payment":
            raise OrderStateError(f"cannot pay order in state {order['status']}")
        if OrderService._is_expired(order):
            OrderService._cancel_in_conn(conn, order_id, "expired")
            raise OrderStateError("order expired")
        total_minor = int(order["total_amount_minor"])
        if total_minor > 0:
            WalletService.debit_in_conn(
                conn,
                user_id=int(order["user_id"]),
                currency=order["currency"],
                amount_minor=total_minor,
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
                (order["currency"], total_minor, order["public_code"], f"cash-sale-wallet:{order['public_code']}"),
            )
        else:
            # Free/test products must not attempt a zero wallet debit.
            conn.execute(
                """
                INSERT OR IGNORE INTO cash_ledger(event_type, provider, direction, currency, amount_minor, reference_type, reference_id, idempotency_key, note)
                VALUES('sale', 'free', 'internal', ?, 0, 'order', ?, ?, 'Free/zero-price order')
                """,
                (order["currency"], order["public_code"], f"cash-sale-free:{order['public_code']}"),
            )
        delivery = OrderService._mark_paid_and_deliver_in_conn(conn, order_id, "wallet")
        return {"order": OrderService._get_order_in_conn(conn, order_id), "delivery": delivery}

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
        preorder_id = order_dict.get("preorder_id")
        if preorder_id:
            # A preorder becomes fulfilled only after the linked remaining-payment
            # order has actually been delivered.
            conn.execute(
                "UPDATE preorders SET status='fulfilled', fulfilled_at=? WHERE id=? AND status!='fulfilled'",
                (now, int(preorder_id)),
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
        # Do not mark preorder fulfilled/cancelled here. If a remaining-payment
        # order expires, the preorder stays active and the deposit remains tracked
        # so admin can retry fulfillment or refund it explicitly.

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

    def sweep_expired_details(self) -> list[dict]:
        now = iso(utcnow())
        with self.db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT o.*, p.name AS product_name, u.telegram_id
                  FROM orders o
                  JOIN products p ON p.id=o.product_id
                  JOIN users u ON u.id=o.user_id
                 WHERE o.status='awaiting_payment' AND o.expires_at < ?
                 ORDER BY o.id ASC
                """,
                (now,),
            ).fetchall()
            expired = [dict(row) for row in rows]
            for row in expired:
                self._cancel_in_conn(conn, int(row["id"]), "expired")
            return expired

    def sweep_expired(self) -> int:
        return len(self.sweep_expired_details())

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
