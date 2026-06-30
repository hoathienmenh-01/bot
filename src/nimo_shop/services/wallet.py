from __future__ import annotations

from nimo_shop.db import Database, dumps
from nimo_shop.money import normalize_currency


class InsufficientFunds(Exception):
    pass


class WalletService:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _balance(conn, user_id: int, currency: str) -> int:
        row = conn.execute(
            "SELECT balance_minor FROM wallet_balances WHERE user_id=? AND currency=?",
            (user_id, currency),
        ).fetchone()
        return int(row["balance_minor"]) if row else 0

    @staticmethod
    def _ensure_wallet(conn, user_id: int, currency: str) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO wallet_balances(user_id, currency, balance_minor) VALUES(?,?,0)",
            (user_id, currency),
        )

    @staticmethod
    def credit_in_conn(
        conn,
        *,
        user_id: int,
        currency: str,
        amount_minor: int,
        reference_type: str,
        reference_id: str,
        idempotency_key: str,
        metadata: dict | None = None,
    ) -> int:
        currency = normalize_currency(currency)
        if amount_minor <= 0:
            raise ValueError("credit amount must be > 0")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        existing = conn.execute("SELECT balance_after_minor FROM ledger_entries WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            return int(existing["balance_after_minor"] or 0)
        WalletService._ensure_wallet(conn, user_id, currency)
        current = WalletService._balance(conn, user_id, currency)
        new_balance = current + amount_minor
        conn.execute(
            "UPDATE wallet_balances SET balance_minor=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND currency=?",
            (new_balance, user_id, currency),
        )
        conn.execute(
            """
            INSERT INTO ledger_entries(user_id, direction, currency, amount_minor, balance_after_minor, reference_type, reference_id, idempotency_key, metadata_json)
            VALUES(?, 'credit', ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, currency, amount_minor, new_balance, reference_type, reference_id, idempotency_key, dumps(metadata)),
        )
        return new_balance

    @staticmethod
    def debit_in_conn(
        conn,
        *,
        user_id: int,
        currency: str,
        amount_minor: int,
        reference_type: str,
        reference_id: str,
        idempotency_key: str,
        metadata: dict | None = None,
    ) -> int:
        currency = normalize_currency(currency)
        if amount_minor <= 0:
            raise ValueError("debit amount must be > 0")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        existing = conn.execute("SELECT balance_after_minor FROM ledger_entries WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            return int(existing["balance_after_minor"] or 0)
        WalletService._ensure_wallet(conn, user_id, currency)
        current = WalletService._balance(conn, user_id, currency)
        if current < amount_minor:
            raise InsufficientFunds(f"balance {current} < required {amount_minor}")
        new_balance = current - amount_minor
        conn.execute(
            "UPDATE wallet_balances SET balance_minor=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND currency=?",
            (new_balance, user_id, currency),
        )
        conn.execute(
            """
            INSERT INTO ledger_entries(user_id, direction, currency, amount_minor, balance_after_minor, reference_type, reference_id, idempotency_key, metadata_json)
            VALUES(?, 'debit', ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, currency, amount_minor, new_balance, reference_type, reference_id, idempotency_key, dumps(metadata)),
        )
        return new_balance

    def credit(self, user_id: int, currency: str, amount_minor: int, *, reason: str, idempotency_key: str) -> int:
        with self.db.transaction() as conn:
            return self.credit_in_conn(
                conn,
                user_id=user_id,
                currency=currency,
                amount_minor=amount_minor,
                reference_type="manual_credit",
                reference_id=reason,
                idempotency_key=idempotency_key,
            )

    def debit(self, user_id: int, currency: str, amount_minor: int, *, reason: str, idempotency_key: str) -> int:
        with self.db.transaction() as conn:
            return self.debit_in_conn(
                conn,
                user_id=user_id,
                currency=currency,
                amount_minor=amount_minor,
                reference_type="manual_debit",
                reference_id=reason,
                idempotency_key=idempotency_key,
            )

    def get_balances(self, user_id: int) -> dict[str, int]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT currency, balance_minor FROM wallet_balances WHERE user_id=?", (user_id,)).fetchall()
            return {r["currency"]: int(r["balance_minor"]) for r in rows}
