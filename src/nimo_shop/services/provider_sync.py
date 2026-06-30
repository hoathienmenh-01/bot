from __future__ import annotations

from decimal import Decimal
from typing import Any

from nimo_shop.money import to_minor
from nimo_shop.services.payments import PaymentMatchError, PaymentService

TX_ID_KEYS = ("id", "transaction_id", "transactionId", "reference", "bank_transaction_id", "code")
DESC_KEYS = ("transaction_content", "content", "description", "note", "memo", "transfer_content")
AMOUNT_KEYS = ("amount_in", "transferAmount", "amount", "value", "money", "creditAmount")


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


def normalize_sepay_transaction(tx: dict[str, Any]) -> dict[str, Any]:
    tx_id = _first_value(tx, TX_ID_KEYS)
    description = _first_value(tx, DESC_KEYS)
    amount_value = _first_value(tx, AMOUNT_KEYS)
    if not tx_id:
        raise ValueError("transaction id is missing")
    if not description:
        raise ValueError("transaction description is missing")
    amount_minor = _parse_vnd_amount_minor(amount_value)
    if amount_minor <= 0:
        raise ValueError("transaction amount must be positive")
    return {
        "provider": "bank",
        "provider_tx_id": str(tx_id),
        "amount_minor": amount_minor,
        "currency": "VND",
        "description": str(description),
        "raw": tx,
    }


def apply_sepay_transactions(payment_service: PaymentService, transactions: list[dict[str, Any]]) -> dict[str, int]:
    """Apply SePay transaction rows safely.

    Duplicate provider transaction ids are handled by PaymentService idempotency.
    Rows that do not contain a payment code are skipped because they cannot be
    assigned automatically; rows with an unknown code are persisted as unmatched
    by PaymentService for admin audit.
    """
    summary = {"processed": 0, "duplicates": 0, "unmatched": 0, "invalid": 0, "applied": 0}
    for tx in transactions:
        try:
            normalized = normalize_sepay_transaction(tx)
            result = payment_service.confirm_provider_transaction(**normalized)
            summary["processed"] += 1
            if result["status"] == "duplicate":
                summary["duplicates"] += 1
            else:
                summary["applied"] += 1
        except PaymentMatchError:
            summary["processed"] += 1
            summary["unmatched"] += 1
        except Exception:
            summary["invalid"] += 1
    return summary
