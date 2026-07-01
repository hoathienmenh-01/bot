from __future__ import annotations

from decimal import Decimal
from typing import Any

from nimo_shop.money import to_minor
from nimo_shop.services.payments import PaymentMatchError, PaymentService

TX_ID_KEYS = ("id", "transaction_id", "transactionId", "reference", "bank_transaction_id", "transactionNumber", "code", "checksum")
DESC_KEYS = ("transaction_content", "content", "description", "note", "memo", "transfer_content", "remark")
AMOUNT_KEYS = ("amount_in", "amountIn", "transferAmount", "amount", "value", "money", "creditAmount")
DIRECTION_KEYS = ("type", "transferType", "direction", "transaction_type")


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_vnd_amount_minor(value: Any) -> int:
    if value is None:
        raise ValueError("transaction amount is missing")
    if isinstance(value, (int, float, Decimal)):
        return to_minor(str(value), "VND")
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text:
        raise ValueError("transaction amount is empty")
    return to_minor(text, "VND")


def _ensure_incoming(tx: dict[str, Any]) -> None:
    direction = _first_value(tx, DIRECTION_KEYS)
    if direction is None:
        return
    value = str(direction).strip().upper()
    if value and value not in {"IN", "CREDIT", "RECEIVE", "RECEIVED", "+"}:
        raise ValueError("outgoing transaction ignored")


def _normalize_bank_transaction(tx: dict[str, Any], *, source: str) -> dict[str, Any]:
    _ensure_incoming(tx)
    tx_id = _first_value(tx, TX_ID_KEYS)
    source_account_id = tx.get("_bank_account_id")
    if tx_id and source_account_id not in (None, ""):
        # Prefix by provider + bank-account id so two connected accounts cannot
        # collide if a provider returns a short/local transaction reference.
        tx_id = f"{source}:bankacct:{source_account_id}:{tx_id}"
    description = _first_value(tx, DESC_KEYS)
    amount_value = _first_value(tx, AMOUNT_KEYS)
    if not tx_id:
        raise ValueError("transaction id is missing")
    if not description:
        raise ValueError("transaction description is missing")
    amount_minor = _parse_vnd_amount_minor(amount_value)
    if amount_minor <= 0:
        raise ValueError("transaction amount must be positive")
    raw = dict(tx)
    raw["_provider_source"] = source
    return {
        "provider": "bank",
        "provider_tx_id": str(tx_id),
        "amount_minor": amount_minor,
        "currency": "VND",
        "description": str(description),
        "raw": raw,
    }


def normalize_sepay_transaction(tx: dict[str, Any]) -> dict[str, Any]:
    return _normalize_bank_transaction(tx, source="sepay")


def normalize_pay2s_transaction(tx: dict[str, Any]) -> dict[str, Any]:
    """Normalize both Pay2S transaction API and Pay2S webhook payload rows.

    Pay2S transaction API commonly returns fields like transaction_id,
    account_number, bank, amount, description, type=IN. Pay2S webhook commonly
    returns id, transactionNumber, accountNumber, content, transferType=IN,
    transferAmount. Both are converted to internal provider="bank" because the
    payment intent codes are bank transfer codes (NAP/ORD).
    """
    return _normalize_bank_transaction(tx, source="pay2s")


def _apply_transactions_detailed(payment_service: PaymentService, transactions: list[dict[str, Any]], *, normalizer) -> dict[str, Any]:
    summary = {"processed": 0, "duplicates": 0, "unmatched": 0, "invalid": 0, "applied": 0}
    results: list[dict[str, Any]] = []
    for tx in transactions:
        try:
            normalized = normalizer(tx)
            result = payment_service.confirm_provider_transaction(**normalized)
            summary["processed"] += 1
            if result["status"] == "duplicate":
                summary["duplicates"] += 1
                outcome = "duplicate"
            else:
                summary["applied"] += 1
                outcome = "applied"
            results.append({"outcome": outcome, "transaction": tx, "normalized": normalized, "result": result})
        except PaymentMatchError as exc:
            summary["processed"] += 1
            summary["unmatched"] += 1
            results.append({"outcome": "unmatched", "transaction": tx, "error": str(exc)})
        except Exception as exc:
            summary["invalid"] += 1
            results.append({"outcome": "invalid", "transaction": tx, "error": str(exc)})
    return {"summary": summary, "results": results}


def _apply_transactions(payment_service: PaymentService, transactions: list[dict[str, Any]], *, normalizer) -> dict[str, int]:
    return dict(_apply_transactions_detailed(payment_service, transactions, normalizer=normalizer)["summary"])


def apply_sepay_transactions(payment_service: PaymentService, transactions: list[dict[str, Any]]) -> dict[str, int]:
    """Apply SePay transaction rows safely and idempotently."""
    return _apply_transactions(payment_service, transactions, normalizer=normalize_sepay_transaction)


def apply_pay2s_transactions(payment_service: PaymentService, transactions: list[dict[str, Any]]) -> dict[str, int]:
    """Apply Pay2S transaction API/webhook rows safely and idempotently."""
    return _apply_transactions(payment_service, transactions, normalizer=normalize_pay2s_transaction)


def apply_sepay_transactions_detailed(payment_service: PaymentService, transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply SePay rows and include per-transaction results for Telegram notifications."""
    return _apply_transactions_detailed(payment_service, transactions, normalizer=normalize_sepay_transaction)


def apply_pay2s_transactions_detailed(payment_service: PaymentService, transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply Pay2S rows and include per-transaction results for Telegram notifications."""
    return _apply_transactions_detailed(payment_service, transactions, normalizer=normalize_pay2s_transaction)
