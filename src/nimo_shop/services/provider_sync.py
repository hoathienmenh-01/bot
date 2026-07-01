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
    text = str(value).strip().replace("đ", "").replace("VND", "").replace("vnd", "").replace(" ", "")
    # Vietnamese bank APIs and admin exports often use 1.000.000 or 1,000,000.
    # VND has no decimal minor unit, so dots/commas here are thousand separators.
    text = text.replace(".", "").replace(",", "")
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


def canonical_bank_provider_tx_id(source: str, tx_id: Any) -> str:
    source = str(source or "bank").strip().lower()
    raw_id = str(tx_id or "").strip()
    if not raw_id:
        raise ValueError("transaction id is missing")
    if raw_id.lower().startswith(f"{source}:"):
        return raw_id
    # Canonical source prefix prevents SePay/Pay2S ID collisions while keeping
    # the same real transaction id identical across webhook and polling paths.
    return f"{source}:{raw_id}"


def _normalize_bank_transaction(tx: dict[str, Any], *, source: str) -> dict[str, Any]:
    _ensure_incoming(tx)
    tx_id = canonical_bank_provider_tx_id(source, _first_value(tx, TX_ID_KEYS))
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
        normalized: dict[str, Any] | None = None
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
            if normalized:
                err_text = str(exc).lower()
                if "payment code not found" in err_text:
                    status = "missing_code"
                elif "account" in err_text and "match" in err_text:
                    status = "account_mismatch"
                else:
                    status = "unmatched"
                payment_service.record_unmatched_event(
                    provider=normalized["provider"],
                    provider_tx_id=normalized["provider_tx_id"],
                    amount_minor=int(normalized["amount_minor"]),
                    currency=str(normalized["currency"]),
                    description=str(normalized.get("description") or ""),
                    raw=normalized.get("raw") if isinstance(normalized.get("raw"), dict) else {"transaction": tx},
                    status=status,
                )
            results.append({"outcome": "unmatched", "transaction": tx, "normalized": normalized, "error": str(exc)})
        except Exception as exc:
            summary["invalid"] += 1
            results.append({"outcome": "invalid", "transaction": tx, "normalized": normalized, "error": str(exc)})
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
