from __future__ import annotations

from typing import Any

from nimo_shop.db import Database, dumps, loads
from nimo_shop.money import fmt_money
from nimo_shop.services.notifications import NotificationService


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _intent_metadata(intent: dict | None) -> dict:
    if not intent:
        return {}
    try:
        return loads(str(intent.get("metadata_json") or "{}"))
    except Exception:
        return {}


def payment_prompt_delete_messages(intent: dict | None) -> list[dict]:
    """Return Telegram payment prompt messages that should be removed after settlement."""
    meta = _intent_metadata(intent)
    raw_items = meta.get("telegram_payment_messages") or []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    result: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        chat_id = _as_int(item.get("chat_id"))
        message_id = _as_int(item.get("message_id"))
        if chat_id is None or message_id is None:
            continue
        key = (chat_id, message_id)
        if key in seen:
            continue
        seen.add(key)
        result.append({"chat_id": chat_id, "message_id": message_id})
    return result


def remember_payment_prompt_messages(db: Database, *, intent_id: int, chat_id: int, message_ids: list[int]) -> None:
    """Persist Telegram instruction/QR message ids on the payment intent.

    Webhooks are handled by the web thread/process, not by an aiogram handler.
    Persisting the prompt ids lets the bot-side notification loop delete the
    stale QR/instruction after the bank provider confirms the money.
    """
    clean_ids: list[int] = []
    for mid in message_ids:
        mid_int = _as_int(mid)
        if mid_int is not None and mid_int > 0 and mid_int not in clean_ids:
            clean_ids.append(mid_int)
    if not clean_ids:
        return
    with db.transaction() as conn:
        row = conn.execute("SELECT metadata_json FROM payment_intents WHERE id=?", (int(intent_id),)).fetchone()
        if not row:
            return
        try:
            meta = loads(str(row["metadata_json"] or "{}"))
        except Exception:
            meta = {}
        current = meta.get("telegram_payment_messages") or []
        if isinstance(current, dict):
            current = [current]
        if not isinstance(current, list):
            current = []
        seen = {
            (str(item.get("chat_id")), str(item.get("message_id")))
            for item in current
            if isinstance(item, dict)
        }
        for message_id in clean_ids:
            key = (str(int(chat_id)), str(int(message_id)))
            if key not in seen:
                current.append({"chat_id": int(chat_id), "message_id": int(message_id)})
                seen.add(key)
        meta["telegram_payment_messages"] = current
        conn.execute("UPDATE payment_intents SET metadata_json=? WHERE id=?", (dumps(meta), int(intent_id)))


def payment_success_message(result: dict) -> str | None:
    """Build the buyer-facing settlement notice for a provider payment result."""
    if not isinstance(result, dict):
        return None
    status = str(result.get("status") or "")
    if status == "duplicate":
        return None
    intent = result.get("intent") or {}
    if not isinstance(intent, dict) or not intent.get("user_id"):
        return None
    currency = str(intent.get("currency") or "VND")
    amount_text = fmt_money(int(intent.get("amount_minor") or 0), currency)
    code = str(intent.get("public_code") or "")

    if status == "order_delivered":
        overpaid = int(result.get("overpaid_minor") or 0)
        overpay_line = ""
        if overpaid > 0:
            overpay_line = f"\nTiền chuyển dư đã cộng vào ví: <b>{fmt_money(overpaid, currency)}</b>"
        return (
            "✅ <b>Thanh toán thành công</b>\n\n"
            f"Mã đơn/thanh toán: <code>{code}</code>\n"
            f"Số tiền nhận: <b>{amount_text}</b>"
            f"{overpay_line}\n\n"
            "Đơn hàng đã được xác nhận và giao tự động. Bấm /taidon nếu cần tải lại hàng."
        )

    balance = result.get("balance_after_minor")
    balance_line = ""
    if balance is not None:
        balance_line = f"\nSố dư ví hiện tại: <b>{fmt_money(int(balance), currency)}</b>"
    status_note = ""
    if status not in {"wallet_credited", "confirmed"}:
        status_note = f"\nTrạng thái xử lý: <code>{status}</code>"
    return (
        "✅ <b>Nạp tiền thành công</b>\n\n"
        f"Mã nạp: <code>{code}</code>\n"
        f"Số tiền đã cộng: <b>{amount_text}</b>"
        f"{balance_line}"
        f"{status_note}\n\n"
        "Tiền đã vào ví của bạn."
    )


def queue_payment_success_notice(db: Database, applied_item: dict) -> int | None:
    """Queue a Telegram notice after a webhook-settled provider payment.

    Only queue when the transaction was actually applied. Duplicate/unmatched
    provider callbacks must not send repeated buyer success messages.
    """
    if not isinstance(applied_item, dict):
        return None
    if applied_item.get("outcome") not in {None, "applied"}:
        return None
    result = applied_item.get("result") if isinstance(applied_item.get("result"), dict) else applied_item
    if not isinstance(result, dict) or result.get("status") == "duplicate":
        return None
    intent = result.get("intent") or {}
    if not isinstance(intent, dict):
        return None
    user_id = _as_int(intent.get("user_id"))
    if user_id is None:
        return None
    message = payment_success_message(result)
    if not message:
        return None
    metadata = {"delete_messages": payment_prompt_delete_messages(intent), "payment_status": result.get("status")}
    return NotificationService(db).queue_user_message(
        user_id=user_id,
        kind="payment_success",
        title="Thanh toán thành công",
        message=message,
        product_id=None,
        metadata=metadata,
    )
