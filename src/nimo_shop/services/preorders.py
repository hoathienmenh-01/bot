from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nimo_shop.db import Database
from nimo_shop.services.orders import OrderService, public_code, iso
from nimo_shop.services.wallet import WalletService


class PreorderStateError(Exception):
    pass


class PreorderOwnershipError(Exception):
    pass


def _now_iso() -> str:
    return iso(datetime.now(timezone.utc))


class PreorderService:
    def __init__(self, db: Database, deposit_percent: int = 10, order_expires_minutes: int = 15) -> None:
        self.db = db
        self.deposit_percent = max(0, min(100, int(deposit_percent)))
        self.order_expires_minutes = max(1, int(order_expires_minutes))

    def create_preorder(self, *, user_id: int, product_id: int, quantity: int = 1) -> dict:
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        code = public_code("PRE")
        with self.db.transaction() as conn:
            buyer = conn.execute("SELECT is_banned FROM users WHERE id=?", (user_id,)).fetchone()
            if not buyer or int(buyer["is_banned"] or 0):
                raise PermissionError("user is banned or does not exist")
            product = conn.execute("SELECT * FROM products WHERE id=? AND is_active=1", (product_id,)).fetchone()
            if not product:
                raise ValueError("product not found or inactive")
            total = int(product["price_minor"]) * int(quantity)
            deposit = (total * self.deposit_percent + 99) // 100 if self.deposit_percent > 0 else 0
            cur = conn.execute(
                """
                INSERT INTO preorders(public_code, user_id, product_id, quantity, currency, unit_amount_minor, total_amount_minor, deposit_percent, deposit_amount_minor, status)
                VALUES(?,?,?,?,?,?,?,?,?,'awaiting_deposit')
                """,
                (code, user_id, product_id, quantity, product["currency"], product["price_minor"], total, self.deposit_percent, deposit),
            )
            return self._get_preorder_in_conn(conn, int(cur.lastrowid))

    @staticmethod
    def _get_preorder_in_conn(conn, preorder_id: int) -> dict:
        row = conn.execute(
            """
            SELECT pr.*, p.name AS product_name, p.warranty_text, u.telegram_id, u.username
              FROM preorders pr
              JOIN products p ON p.id=pr.product_id
              JOIN users u ON u.id=pr.user_id
             WHERE pr.id=?
            """,
            (preorder_id,),
        ).fetchone()
        if not row:
            raise ValueError("preorder not found")
        return dict(row)

    @staticmethod
    def _assert_owner(preorder: dict, expected_user_id: int | None) -> None:
        if expected_user_id is not None and int(preorder["user_id"]) != int(expected_user_id):
            raise PreorderOwnershipError("preorder does not belong to this user")

    def get_preorder(self, preorder_id: int, *, expected_user_id: int | None = None) -> dict:
        with self.db.connect() as conn:
            preorder = self._get_preorder_in_conn(conn, preorder_id)
            self._assert_owner(preorder, expected_user_id)
            return preorder

    def pay_deposit_with_wallet(self, preorder_id: int, *, expected_user_id: int | None = None) -> dict:
        with self.db.transaction() as conn:
            preorder = self._get_preorder_in_conn(conn, preorder_id)
            self._assert_owner(preorder, expected_user_id)
            if preorder["status"] == "active":
                return preorder
            if preorder["status"] != "awaiting_deposit":
                raise PreorderStateError(f"cannot pay preorder in state {preorder['status']}")
            deposit = int(preorder["deposit_amount_minor"] or 0)
            if deposit > 0:
                WalletService.debit_in_conn(
                    conn,
                    user_id=int(preorder["user_id"]),
                    currency=preorder["currency"],
                    amount_minor=deposit,
                    reference_type="preorder_deposit",
                    reference_id=preorder["public_code"],
                    idempotency_key=f"wallet-preorder-deposit:{preorder['public_code']}",
                    metadata={"preorder_id": preorder_id, "deposit_percent": int(preorder["deposit_percent"])},
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cash_ledger(event_type, provider, direction, currency, amount_minor, reference_type, reference_id, idempotency_key, note)
                    VALUES('preorder_deposit', 'wallet', 'internal', ?, ?, 'preorder', ?, ?, 'Paid preorder deposit from user wallet')
                    """,
                    (preorder["currency"], deposit, preorder["public_code"], f"cash-preorder-wallet:{preorder['public_code']}"),
                )
            now = _now_iso()
            conn.execute("UPDATE preorders SET status='active', paid_at=? WHERE id=? AND status='awaiting_deposit'", (now, preorder_id))
            return self._get_preorder_in_conn(conn, preorder_id)

    def cancel_preorder(self, preorder_id: int, *, expected_user_id: int | None = None) -> dict:
        with self.db.transaction() as conn:
            preorder = self._get_preorder_in_conn(conn, preorder_id)
            self._assert_owner(preorder, expected_user_id)
            if preorder["status"] in {"fulfilled", "refunded"}:
                raise PreorderStateError(f"cannot cancel preorder in state {preorder['status']}")
            deposit = int(preorder["deposit_amount_minor"] or 0)
            if preorder["status"] == "active" and deposit > 0:
                balance = WalletService.credit_in_conn(
                    conn,
                    user_id=int(preorder["user_id"]),
                    currency=preorder["currency"],
                    amount_minor=deposit,
                    reference_type="preorder_refund",
                    reference_id=preorder["public_code"],
                    idempotency_key=f"wallet-preorder-refund:{preorder['public_code']}",
                    metadata={"preorder_id": preorder_id, "reason": "cancel_preorder"},
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cash_ledger(event_type, provider, direction, currency, amount_minor, reference_type, reference_id, idempotency_key, note)
                    VALUES('preorder_refund', 'wallet', 'internal', ?, ?, 'preorder', ?, ?, 'Refund preorder deposit to wallet')
                    """,
                    (preorder["currency"], deposit, preorder["public_code"], f"cash-preorder-refund:{preorder['public_code']}"),
                )
                conn.execute("UPDATE preorders SET status='refunded' WHERE id=?", (preorder_id,))
                return {"preorder": self._get_preorder_in_conn(conn, preorder_id), "balance_after_minor": balance, "refunded_minor": deposit}
            conn.execute("UPDATE preorders SET status='cancelled' WHERE id=?", (preorder_id,))
            return {"preorder": self._get_preorder_in_conn(conn, preorder_id), "balance_after_minor": None, "refunded_minor": 0}

    def mark_fulfilled(self, preorder_id: int) -> dict:
        # Commercial safety: never mark a preorder fulfilled without reserving
        # stock and creating/delivering the corresponding order.
        return self.create_payment_order_for_preorder(preorder_id)

    @staticmethod
    def _create_order_from_preorder_in_conn(conn, pr: dict, *, order_expires_minutes: int) -> dict | None:
        qty = int(pr["quantity"])
        stock = conn.execute(
            "SELECT id FROM stock_items WHERE product_id=? AND status='available' ORDER BY id LIMIT ?",
            (int(pr["product_id"]), qty),
        ).fetchall()
        if len(stock) < qty:
            return None
        now = datetime.now(timezone.utc)
        expires_at = iso(now + timedelta(minutes=order_expires_minutes))
        order_code = public_code("ORD")
        remaining = max(0, int(pr["total_amount_minor"]) - int(pr.get("deposit_amount_minor") or 0))
        unit_remaining = (remaining + qty - 1) // qty if qty > 0 else remaining
        cur = conn.execute(
            """
            INSERT INTO orders(public_code, user_id, product_id, quantity, currency, unit_amount_minor, total_amount_minor, status, payment_method, expires_at)
            VALUES(?,?,?,?,?,?,?,'awaiting_payment','preorder_remaining',?)
            """,
            (order_code, int(pr["user_id"]), int(pr["product_id"]), qty, pr["currency"], unit_remaining, remaining, expires_at),
        )
        order_id = int(cur.lastrowid)
        updated = 0
        for item in stock:
            updated += conn.execute(
                """
                UPDATE stock_items
                   SET status='reserved', reserved_by_user_id=?, reserved_order_id=?, reserved_until=?
                 WHERE id=? AND status='available'
                """,
                (int(pr["user_id"]), order_id, expires_at, int(item["id"])),
            ).rowcount
        if updated != qty:
            raise RuntimeError("stock was reserved by another transaction")
        if remaining == 0:
            OrderService._mark_paid_and_deliver_in_conn(conn, order_id, "preorder_deposit")
        conn.execute("UPDATE preorders SET status='fulfilled', fulfilled_at=? WHERE id=?", (_now_iso(), int(pr["id"])))
        order = conn.execute(
            """
            SELECT o.*, p.name AS product_name, u.telegram_id
              FROM orders o
              JOIN products p ON p.id=o.product_id
              JOIN users u ON u.id=o.user_id
             WHERE o.id=?
            """,
            (order_id,),
        ).fetchone()
        return {**dict(order), "preorder_code": pr["public_code"], "deposit_amount_minor": int(pr.get("deposit_amount_minor") or 0)}

    def create_payment_order_for_preorder(self, preorder_id: int) -> dict:
        with self.db.transaction() as conn:
            pr = self._get_preorder_in_conn(conn, preorder_id)
            if pr["status"] != "active":
                raise PreorderStateError(f"cannot fulfill preorder in state {pr['status']}")
            order = self._create_order_from_preorder_in_conn(conn, pr, order_expires_minutes=self.order_expires_minutes)
            if order is None:
                raise PreorderStateError("not enough stock to fulfill preorder")
            return order

    def create_payment_orders_for_available_stock(self, product_id: int | None = None) -> list[dict]:
        """Reserve newly added stock for active preorders in FIFO order.

        A preorder only pays the deposit. When stock arrives, create a normal
        order for the remaining amount and reserve the stock for that buyer.
        If deposit already covers 100%, create and deliver a zero-remaining
        order immediately.
        """
        created: list[dict] = []
        with self.db.transaction() as conn:
            sql = """
                SELECT pr.*, p.name AS product_name
                  FROM preorders pr
                  JOIN products p ON p.id=pr.product_id
                 WHERE pr.status='active'
            """
            params: list[object] = []
            if product_id is not None:
                sql += " AND pr.product_id=?"
                params.append(product_id)
            sql += " ORDER BY pr.id ASC"
            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                order = self._create_order_from_preorder_in_conn(conn, dict(row), order_expires_minutes=self.order_expires_minutes)
                if order is not None:
                    created.append(order)
        return created

    def list_preorders(self, *, status: str | None = None, limit: int = 200) -> list[dict]:
        sql = """
            SELECT pr.*, p.name AS product_name, u.telegram_id, u.username
              FROM preorders pr
              JOIN products p ON p.id=pr.product_id
              JOIN users u ON u.id=pr.user_id
        """
        params: list[object] = []
        if status:
            sql += " WHERE pr.status=?"
            params.append(status)
        sql += " ORDER BY pr.id DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params)]
