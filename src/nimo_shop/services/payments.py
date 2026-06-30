from __future__ import annotations

import re
import secrets
from datetime import timedelta

from nimo_shop.db import Database, dumps
from nimo_shop.money import normalize_currency
from nimo_shop.services.orders import OrderService, iso, utcnow
from nimo_shop.services.wallet import WalletService


class PaymentMatchError(Exception):
    pass


def normalize_provider(provider: str) -> str:
    value = (provider or "").strip().lower()
    if not value:
        raise ValueError("provider is required")
    return value


def make_payment_code(prefix: str = "NAP") -> str:
    return f"{prefix}{secrets.token_hex(4).upper()}"


def extract_payment_code(text: str) -> str | None:
    match = re.search(r"\b(?:NAP|ORD)[A-F0-9]{8}\b", text.upper())
    return match.group(0) if match else None


class PaymentService:
    def __init__(self, db: Database, deposit_expires_minutes: int = 15) -> None:
        self.db = db
        self.deposit_expires_minutes = deposit_expires_minutes

    def create_wallet_topup_intent(self, *, user_id: int, provider: str, currency: str, amount_minor: int) -> dict:
        if amount_minor <= 0:
            raise ValueError("amount must be > 0")
        cur = normalize_currency(currency)
        provider = normalize_provider(provider)
        code = make_payment_code("NAP")
        expires = iso(utcnow() + timedelta(minutes=self.deposit_expires_minutes))
        with self.db.transaction() as conn:
            c = conn.execute(
                """
                INSERT INTO payment_intents(public_code, user_id, provider, currency, amount_minor, expires_at)
                VALUES(?,?,?,?,?,?)
                """,
                (code, user_id, provider, cur, amount_minor, expires),
            )
            return dict(conn.execute("SELECT * FROM payment_intents WHERE id=?", (c.lastrowid,)).fetchone())

    def create_order_payment_intent(self, *, order_id: int, provider: str, expected_user_id: int | None = None) -> dict:
        provider = normalize_provider(provider)
        code = make_payment_code("ORD")
        expires = iso(utcnow() + timedelta(minutes=self.deposit_expires_minutes))
        now = iso(utcnow())
        error: ValueError | None = None
        result: dict | None = None
        with self.db.transaction() as conn:
            order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                error = ValueError("order not found")
            elif expected_user_id is not None and int(order["user_id"]) != int(expected_user_id):
                error = PermissionError("order does not belong to this user")
            elif order["status"] != "awaiting_payment":
                error = ValueError("order is not awaiting payment")
            elif order["expires_at"] < now:
                OrderService._cancel_in_conn(conn, order_id, "expired")
                error = ValueError("order expired")
            else:
                c = conn.execute(
                    """
                    INSERT INTO payment_intents(public_code, user_id, order_id, provider, currency, amount_minor, expires_at)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (code, order["user_id"], order_id, provider, order["currency"], order["total_amount_minor"], expires),
                )
                result = dict(conn.execute("SELECT * FROM payment_intents WHERE id=?", (c.lastrowid,)).fetchone())
        if error:
            raise error
        assert result is not None
        return result

    def confirm_provider_transaction(
        self,
        *,
        provider: str,
        provider_tx_id: str,
        amount_minor: int,
        currency: str,
        description: str,
        raw: dict | None = None,
        fee_minor: int = 0,
    ) -> dict:
        """Apply an incoming bank/crypto transaction exactly once.

        Money-safety rule: once a real provider transaction is matched to an
        existing payment code, the system must either deliver the order or credit
        the buyer wallet. It must not silently drop underpayments, late payments,
        duplicate-code payments, or overpayments.
        """
        provider = normalize_provider(provider)
        provider_tx_id = (provider_tx_id or "").strip()
        if not provider_tx_id:
            raise ValueError("provider_tx_id is required")
        if amount_minor <= 0:
            raise ValueError("amount must be > 0")
        if fee_minor < 0:
            raise ValueError("fee cannot be negative")
        cur = normalize_currency(currency)
        payment_code = extract_payment_code(description)
        if not payment_code:
            raise PaymentMatchError("payment code not found in description")

        error: PaymentMatchError | None = None
        result: dict | None = None
        with self.db.transaction() as conn:
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO external_payment_events(provider, provider_tx_id, payment_code, currency, amount_minor, status, raw_json)
                VALUES(?,?,?,?,?,'received',?)
                """,
                (provider, provider_tx_id, payment_code, cur, amount_minor, dumps(raw)),
            ).rowcount
            if inserted == 0:
                event = conn.execute(
                    "SELECT * FROM external_payment_events WHERE provider=? AND provider_tx_id=?",
                    (provider, provider_tx_id),
                ).fetchone()
                result = {"status": "duplicate", "event": dict(event)}
            else:
                intent = conn.execute(
                    "SELECT * FROM payment_intents WHERE public_code=? AND provider=?",
                    (payment_code, provider),
                ).fetchone()
                if not intent:
                    conn.execute(
                        "UPDATE external_payment_events SET status='unmatched' WHERE provider=? AND provider_tx_id=?",
                        (provider, provider_tx_id),
                    )
                    error = PaymentMatchError("no intent matched this provider/code")
                else:
                    intent_dict = dict(intent)
                    expected_minor = int(intent["amount_minor"])
                    event_type = "order_payment" if intent["order_id"] else "wallet_topup"
                    conn.execute(
                        """
                        INSERT INTO cash_ledger(event_type, provider, direction, currency, amount_minor, fee_minor, reference_type, reference_id, idempotency_key, note)
                        VALUES(?, ?, 'in', ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_type,
                            provider,
                            cur,
                            amount_minor,
                            fee_minor,
                            "payment_intent",
                            intent["public_code"],
                            f"cash:{provider}:{provider_tx_id}",
                            description,
                        ),
                    )

                    def credit_wallet(status: str, reference_type: str, note: dict | None = None) -> dict:
                        balance = WalletService.credit_in_conn(
                            conn,
                            user_id=int(intent["user_id"]),
                            currency=cur,
                            amount_minor=amount_minor,
                            reference_type=reference_type,
                            reference_id=intent["public_code"],
                            idempotency_key=f"wallet-{reference_type}:{provider}:{provider_tx_id}",
                            metadata={"provider": provider, "payment_code": payment_code, **(note or {})},
                        )
                        conn.execute(
                            "UPDATE external_payment_events SET status=? WHERE provider=? AND provider_tx_id=?",
                            (status, provider, provider_tx_id),
                        )
                        return {"status": status, "intent": intent_dict, "balance_after_minor": balance}

                    if intent["status"] != "pending":
                        # Same code was paid again after an intent was already settled.
                        # Do not lose that money; credit it to the buyer wallet.
                        result = credit_wallet("wallet_credited_extra_payment", "extra_payment")
                    elif intent["expires_at"] < iso(utcnow()):
                        conn.execute("UPDATE payment_intents SET status='confirmed', provider_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (provider_tx_id, intent["id"]))
                        if intent["order_id"]:
                            order = conn.execute("SELECT * FROM orders WHERE id=?", (intent["order_id"],)).fetchone()
                            if order and order["status"] == "awaiting_payment":
                                OrderService._cancel_in_conn(conn, int(order["id"]), "expired")
                        result = credit_wallet("wallet_credited_expired_intent", "expired_intent_payment")
                    elif cur != intent["currency"]:
                        conn.execute("UPDATE payment_intents SET status='confirmed', provider_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (provider_tx_id, intent["id"]))
                        result = credit_wallet("wallet_credited_currency_mismatch", "currency_mismatch_payment", {"expected_currency": intent["currency"]})
                    elif intent["order_id"]:
                        order = conn.execute("SELECT * FROM orders WHERE id=?", (intent["order_id"],)).fetchone()
                        if not order or order["status"] != "awaiting_payment" or order["expires_at"] < iso(utcnow()):
                            conn.execute("UPDATE payment_intents SET status='confirmed', provider_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (provider_tx_id, intent["id"]))
                            if order and order["status"] == "awaiting_payment":
                                OrderService._cancel_in_conn(conn, int(order["id"]), "expired")
                            result = credit_wallet("wallet_credited_late_order", "late_order_payment", {"order_id": intent["order_id"]})
                        elif amount_minor < expected_minor:
                            # Partial order payment: keep money as wallet balance. The user can top up the rest and pay by wallet.
                            conn.execute("UPDATE payment_intents SET status='confirmed', provider_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (provider_tx_id, intent["id"]))
                            result = credit_wallet("wallet_credited_underpaid_order", "underpaid_order_payment", {"order_id": intent["order_id"], "expected_minor": expected_minor})
                        else:
                            conn.execute("UPDATE payment_intents SET status='confirmed', provider_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (provider_tx_id, intent["id"]))
                            delivery = OrderService._mark_paid_and_deliver_in_conn(conn, int(intent["order_id"]), provider)
                            overpaid_minor = amount_minor - expected_minor
                            overpay_balance = None
                            if overpaid_minor > 0:
                                overpay_balance = WalletService.credit_in_conn(
                                    conn,
                                    user_id=int(intent["user_id"]),
                                    currency=cur,
                                    amount_minor=overpaid_minor,
                                    reference_type="order_overpayment",
                                    reference_id=intent["public_code"],
                                    idempotency_key=f"wallet-overpay:{provider}:{provider_tx_id}",
                                    metadata={"provider": provider, "payment_code": payment_code, "order_id": intent["order_id"]},
                                )
                            conn.execute(
                                "UPDATE external_payment_events SET status='order_delivered' WHERE provider=? AND provider_tx_id=?",
                                (provider, provider_tx_id),
                            )
                            result = {
                                "status": "order_delivered",
                                "intent": intent_dict,
                                "delivery": delivery,
                                "overpaid_minor": overpaid_minor,
                                "overpay_balance_after_minor": overpay_balance,
                            }
                    else:
                        conn.execute("UPDATE payment_intents SET status='confirmed', provider_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (provider_tx_id, intent["id"]))
                        balance = WalletService.credit_in_conn(
                            conn,
                            user_id=int(intent["user_id"]),
                            currency=cur,
                            amount_minor=amount_minor,
                            reference_type="payment_intent",
                            reference_id=intent["public_code"],
                            idempotency_key=f"wallet-topup:{provider}:{provider_tx_id}",
                            metadata={"provider": provider, "payment_code": payment_code},
                        )
                        conn.execute(
                            "UPDATE external_payment_events SET status='wallet_credited' WHERE provider=? AND provider_tx_id=?",
                            (provider, provider_tx_id),
                        )
                        result = {"status": "wallet_credited", "intent": intent_dict, "balance_after_minor": balance}
        if error:
            raise error
        assert result is not None
        return result


    def attach_provider_reference(self, *, intent_id: int, provider_ref: str, metadata: dict | None = None) -> None:
        if not provider_ref:
            raise ValueError("provider_ref is required")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM payment_intents WHERE id=?", (intent_id,)).fetchone()
            if not row:
                raise ValueError("payment intent not found")
            conn.execute(
                "UPDATE payment_intents SET provider_ref=?, metadata_json=? WHERE id=?",
                (provider_ref, dumps(metadata), intent_id),
            )

    def expire_pending_intents(self) -> int:
        now = iso(utcnow())
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT id FROM payment_intents WHERE status='pending' AND expires_at < ?", (now,)).fetchall()
            conn.executemany("UPDATE payment_intents SET status='expired' WHERE id=?", [(r["id"],) for r in rows])
            return len(rows)
