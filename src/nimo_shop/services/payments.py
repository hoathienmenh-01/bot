from __future__ import annotations

import re
import secrets
from datetime import timedelta

from nimo_shop.db import Database, dumps, loads
from nimo_shop.money import normalize_currency
from nimo_shop.services.orders import OrderService, iso, utcnow
from nimo_shop.services.wallet import WalletService


class PaymentMatchError(Exception):
    pass


BANK_PROVIDER_ALIASES = {"bank", "sepay", "sepay_api", "pay2s", "pay2s_api", "casso", "casso_api", "custom", "manual"}
CRYPTO_ORDER_PROVIDERS = {"binance_pay", "binance_manual", "usdt_bep20"}


def normalize_provider(provider: str) -> str:
    value = (provider or "").strip().lower().replace("-", "_")
    if not value:
        raise ValueError("provider is required")
    if value in BANK_PROVIDER_ALIASES:
        return "bank"
    if value in {"binance", "binance_id"}:
        return "binance_manual"
    if value in {"usdt", "usdt_bep20", "usdt_bep_20"}:
        return "usdt_bep20"
    return value


def provider_match_candidates(provider: str) -> tuple[str, ...]:
    canonical = normalize_provider(provider)
    if canonical == "bank":
        # Legacy rows may still contain sepay/pay2s/casso/manual, but all new
        # bank transfer intents settle through canonical provider="bank".
        return ("bank", "sepay", "pay2s", "casso", "manual", "custom")
    if canonical == "binance_manual":
        return ("binance_manual", "binance")
    return (canonical,)


def make_payment_code(prefix: str = "NAP") -> str:
    # 64-bit random suffix. Earlier 32-bit codes were too easy to guess for a
    # public webhook/checkout flow. extract_payment_code still accepts legacy
    # 8-hex codes so existing unpaid intents remain reconcilable after upgrade.
    return f"{prefix}{secrets.token_hex(8).upper()}"


def extract_payment_code(text: str) -> str | None:
    match = re.search(r"\b(?:NAP|ORD)[A-F0-9]{8,32}\b", (text or "").upper())
    return match.group(0) if match else None


class PaymentService:
    def __init__(self, db: Database, deposit_expires_minutes: int = 15) -> None:
        self.db = db
        self.deposit_expires_minutes = deposit_expires_minutes

    def create_wallet_topup_intent(self, *, user_id: int, provider: str, currency: str, amount_minor: int, bank_account_id: int | None = None, metadata: dict | None = None) -> dict:
        if amount_minor <= 0:
            raise ValueError("amount must be > 0")
        cur = normalize_currency(currency)
        provider = normalize_provider(provider)
        code = make_payment_code("NAP")
        meta = dict(metadata or {})
        if bank_account_id is not None:
            meta["bank_account_id"] = int(bank_account_id)
        expires = iso(utcnow() + timedelta(minutes=self.deposit_expires_minutes))
        with self.db.transaction() as conn:
            c = conn.execute(
                """
                INSERT INTO payment_intents(public_code, user_id, provider, currency, amount_minor, metadata_json, expires_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (code, user_id, provider, cur, amount_minor, dumps(meta), expires),
            )
            return dict(conn.execute("SELECT * FROM payment_intents WHERE id=?", (c.lastrowid,)).fetchone())

    def create_order_payment_intent(self, *, order_id: int, provider: str, expected_user_id: int | None = None, bank_account_id: int | None = None, metadata: dict | None = None) -> dict:
        provider = normalize_provider(provider)
        code = make_payment_code("ORD")
        meta = dict(metadata or {})
        if bank_account_id is not None:
            meta["bank_account_id"] = int(bank_account_id)
        expires = iso(utcnow() + timedelta(minutes=self.deposit_expires_minutes))
        now = iso(utcnow())
        error: ValueError | PermissionError | None = None
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
            elif int(order["total_amount_minor"] or 0) <= 0:
                # Free/fully-prepaid orders must be delivered through the wallet/free
                # path. payment_intents intentionally require amount_minor > 0.
                error = ValueError("zero-amount orders do not need external payment")
            elif provider in CRYPTO_ORDER_PROVIDERS and str(order["currency"]).upper() != "USDT":
                # Do not treat a VND sale amount as a USDT/Binance amount. A
                # separate FX quote module must convert VND -> USDT before crypto
                # checkout can be enabled for VND-priced products.
                error = ValueError("Binance/USDT order payment requires a USDT-priced order or an explicit VND-to-USDT quote")
            else:
                # Reuse a still-pending intent for the same order/provider.
                # Without this, repeatedly pressing a payment button creates many
                # valid ORD codes for one order, which confuses customers and
                # makes reconciliation noisy.
                candidates = provider_match_candidates(provider)
                placeholders = ",".join("?" for _ in candidates)
                existing = conn.execute(
                    f"""
                    SELECT * FROM payment_intents
                     WHERE order_id=? AND provider IN ({placeholders}) AND status='pending' AND expires_at>=?
                     ORDER BY CASE WHEN provider=? THEN 0 ELSE 1 END, id DESC LIMIT 1
                    """,
                    (order_id, *candidates, now, provider),
                ).fetchone()
                if existing:
                    result = dict(existing)
                else:
                    c = conn.execute(
                        """
                        INSERT INTO payment_intents(public_code, user_id, order_id, provider, currency, amount_minor, metadata_json, expires_at)
                        VALUES(?,?,?,?,?,?,?,?)
                        """,
                        (code, order["user_id"], order_id, provider, order["currency"], order["total_amount_minor"], dumps(meta), expires),
                    )
                    result = dict(conn.execute("SELECT * FROM payment_intents WHERE id=?", (c.lastrowid,)).fetchone())
        if error:
            raise error
        assert result is not None
        return result

    @staticmethod
    def _compact_account_no(value: object) -> str:
        return "".join(ch for ch in str(value or "") if ch.isalnum()).upper()

    @staticmethod
    def _raw_first(raw: dict | None, keys: tuple[str, ...]) -> object | None:
        if not raw:
            return None
        for key in keys:
            value = raw.get(key)
            if value not in (None, ""):
                return value
        return None

    def _validate_bank_account_binding(self, conn, *, intent: dict, raw: dict | None) -> str | None:  # noqa: ANN001
        if normalize_provider(str(intent.get("provider") or "")) != "bank":
            return None
        try:
            meta = loads(str(intent.get("metadata_json") or "{}"))
        except Exception:
            meta = {}
        expected_id = meta.get("bank_account_id")
        if expected_id in (None, ""):
            return None
        try:
            expected_id_int = int(expected_id)
        except (TypeError, ValueError):
            return "invalid bank_account_id in payment intent metadata"
        expected = conn.execute("SELECT * FROM bank_accounts WHERE id=?", (expected_id_int,)).fetchone()
        expected_no = self._compact_account_no(expected["account_no"] if expected else "")
        raw_bank_id = self._raw_first(raw, ("_bank_account_id", "bank_account_id", "bankAccountId"))
        if raw_bank_id not in (None, ""):
            try:
                if int(raw_bank_id) != expected_id_int:
                    return f"received account id {raw_bank_id} does not match selected bank account {expected_id_int}"
            except (TypeError, ValueError):
                return f"invalid received bank account id {raw_bank_id}"
        received_no = self._raw_first(raw, ("accountNumber", "account_number", "accountNo", "bankAccount", "bank_account", "receiveAccount", "receiverAccount", "creditAccount"))
        if received_no not in (None, "") and expected_no:
            got_no = self._compact_account_no(received_no)
            if got_no and got_no != expected_no:
                return f"received account {received_no} does not match selected account {expected['account_no'] if expected else expected_id_int}"
        return None

    def record_unmatched_event(
        self,
        *,
        provider: str,
        provider_tx_id: str,
        amount_minor: int,
        currency: str,
        description: str = "",
        raw: dict | None = None,
        status: str = "unmatched",
    ) -> dict:
        provider = normalize_provider(provider)
        provider_tx_id = (provider_tx_id or "").strip()
        if not provider_tx_id:
            raise ValueError("provider_tx_id is required")
        cur = normalize_currency(currency)
        payment_code = extract_payment_code(description) or ""
        payload = {"description": description, **(raw or {})}
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO external_payment_events(provider, provider_tx_id, payment_code, currency, amount_minor, status, raw_json)
                VALUES(?,?,?,?,?,?,?)
                """,
                (provider, provider_tx_id, payment_code, cur, amount_minor, status, dumps(payload)),
            )
            conn.execute(
                """
                UPDATE external_payment_events
                   SET payment_code=COALESCE(NULLIF(payment_code,''), ?), currency=?, amount_minor=?, status=?, raw_json=?
                 WHERE provider=? AND provider_tx_id=? AND status IN ('received','unmatched','missing_code','reviewed','account_mismatch')
                """,
                (payment_code, cur, amount_minor, status, dumps(payload), provider, provider_tx_id),
            )
            row = conn.execute("SELECT * FROM external_payment_events WHERE provider=? AND provider_tx_id=?", (provider, provider_tx_id)).fetchone()
            return dict(row) if row else {}

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
        duplicate-code payments, overpayments, or admin reconciliation of an
        earlier unmatched event.
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
            event = conn.execute(
                "SELECT * FROM external_payment_events WHERE provider=? AND provider_tx_id=?",
                (provider, provider_tx_id),
            ).fetchone()
            assert event is not None

            if inserted == 0:
                # Already-settled events must remain idempotent. Only previously
                # unmatched/reviewed/received events may be reconciled by an admin
                # with the same provider_tx_id and a corrected payment code.
                if str(event["status"]) not in {"unmatched", "received", "reviewed", "missing_code", "account_mismatch"}:
                    result = {"status": "duplicate", "event": dict(event)}
                else:
                    conn.execute(
                        """
                        UPDATE external_payment_events
                           SET payment_code=?, currency=?, amount_minor=?, raw_json=?
                         WHERE provider=? AND provider_tx_id=?
                        """,
                        (payment_code, cur, amount_minor, dumps(raw), provider, provider_tx_id),
                    )
                    event = conn.execute(
                        "SELECT * FROM external_payment_events WHERE provider=? AND provider_tx_id=?",
                        (provider, provider_tx_id),
                    ).fetchone()

            if result is None:
                candidates = provider_match_candidates(provider)
                placeholders = ",".join("?" for _ in candidates)
                intent = conn.execute(
                    f"""
                    SELECT * FROM payment_intents
                     WHERE public_code=? AND provider IN ({placeholders})
                     ORDER BY CASE WHEN provider=? THEN 0 ELSE 1 END, id DESC LIMIT 1
                    """,
                    (payment_code, *candidates, provider),
                ).fetchone()
                if not intent:
                    conn.execute(
                        "UPDATE external_payment_events SET status='unmatched' WHERE provider=? AND provider_tx_id=?",
                        (provider, provider_tx_id),
                    )
                    error = PaymentMatchError("no intent matched this provider/code")
                else:
                    intent_dict = dict(intent)
                    account_mismatch = self._validate_bank_account_binding(conn, intent=intent_dict, raw=raw)
                    if account_mismatch:
                        merged_raw = {"account_mismatch": account_mismatch, **(raw or {})}
                        conn.execute(
                            "UPDATE external_payment_events SET status='account_mismatch', raw_json=? WHERE provider=? AND provider_tx_id=?",
                            (dumps(merged_raw), provider, provider_tx_id),
                        )
                        error = PaymentMatchError(account_mismatch)
                    else:
                        expected_minor = int(intent["amount_minor"])
                        event_type = "order_payment" if intent["order_id"] else "wallet_topup"
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO cash_ledger(event_type, provider, direction, currency, amount_minor, fee_minor, reference_type, reference_id, idempotency_key, note)
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
    
                        now_iso = iso(utcnow())
                        if intent["status"] != "pending":
                            # Same code was paid again after an intent was already settled.
                            # Do not lose that money; credit it to the buyer wallet.
                            result = credit_wallet("wallet_credited_extra_payment", "extra_payment")
                        elif intent["expires_at"] < now_iso:
                            conn.execute("UPDATE payment_intents SET status='confirmed', provider_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (provider_tx_id, intent["id"]))
                            if intent["order_id"]:
                                order = conn.execute("SELECT * FROM orders WHERE id=?", (intent["order_id"],)).fetchone()
                                if order and order["status"] == "awaiting_payment":
                                    OrderService._cancel_in_conn(conn, int(order["id"]), "expired")
                            result = credit_wallet("wallet_credited_expired_intent", "expired_intent_payment")
                        elif cur != intent["currency"]:
                            # Do not auto-settle currency mismatches as a sale. Credit the
                            # exact received money to wallet for manual review and avoid
                            # marking the order paid in the wrong currency.
                            conn.execute("UPDATE payment_intents SET status='confirmed', provider_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (provider_tx_id, intent["id"]))
                            result = credit_wallet("wallet_credited_currency_mismatch", "currency_mismatch_payment", {"expected_currency": intent["currency"]})
                        elif intent["order_id"]:
                            order = conn.execute("SELECT * FROM orders WHERE id=?", (intent["order_id"],)).fetchone()
                            if not order or order["status"] != "awaiting_payment" or order["expires_at"] < now_iso:
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
