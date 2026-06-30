from __future__ import annotations

from datetime import datetime, timezone

from nimo_shop.db import Database
from nimo_shop.services.orders import public_code, iso
from nimo_shop.services.wallet import WalletService


class PreorderStateError(Exception):
    pass


class PreorderOwnershipError(Exception):
    pass


def _now_iso() -> str:
    return iso(datetime.now(timezone.utc))


class PreorderService:
    def __init__(self, db: Database, deposit_percent: int = 10) -> None:
        self.db = db
        self.deposit_percent = max(0, min(100, int(deposit_percent)))

    def create_preorder(self, *, user_id: int, product_id: int, quantity: int = 1) -> dict:
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        code = public_code("PRE")
        with self.db.transaction() as conn:
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

    def cancel_preorder(self, preorder_id: int, *, expected_user_id: int | None = None) -> None:
        with self.db.transaction() as conn:
            preorder = self._get_preorder_in_conn(conn, preorder_id)
            self._assert_owner(preorder, expected_user_id)
            if preorder["status"] in {"fulfilled", "refunded"}:
                raise PreorderStateError(f"cannot cancel preorder in state {preorder['status']}")
            conn.execute("UPDATE preorders SET status='cancelled' WHERE id=?", (preorder_id,))

    def mark_fulfilled(self, preorder_id: int) -> None:
        now = _now_iso()
        with self.db.transaction() as conn:
            conn.execute("UPDATE preorders SET status='fulfilled', fulfilled_at=? WHERE id=?", (now, preorder_id))

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
