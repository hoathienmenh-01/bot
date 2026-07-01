from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path

from nimo_shop.bot.app import build_dispatcher
from nimo_shop.config import Settings
from nimo_shop.db import Database, loads
from nimo_shop.bot import views
from nimo_shop.payments.pay2s import Pay2SClient, Pay2SConfig
from nimo_shop.payments.sepay import SepayClient
from nimo_shop.services.bank_accounts import BankAccountService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.provider_sync import apply_pay2s_transactions_detailed, apply_sepay_transactions_detailed
from nimo_shop.services.notifications import NotificationService
from nimo_shop.services.payment_notices import payment_prompt_delete_messages, payment_success_message

_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
_PLACEHOLDER_TOKENS = {
    "",
    "token_botfather",
    "PASTE_TOKEN_BOTFATHER_VAO_DAY",
    "your_bot_token",
    "changeme",
}


def is_configured_bot_token(token: str | None) -> bool:
    """Return True only when token looks like a real BotFather token.

    This prevents first-run setup from crashing with aiogram's raw
    TokenValidationError when .env still contains placeholders. The bot still
    lets aiogram validate the token before polling, but invalid/missing tokens
    now open the web setup panel instead of killing the process.
    """
    value = (token or "").strip()
    if value in _PLACEHOLDER_TOKENS:
        return False
    return bool(_TOKEN_RE.match(value))


async def _delete_telegram_messages(bot, messages: list[dict]) -> None:
    """Best-effort cleanup of stale payment instruction/QR messages."""
    for item in messages:
        try:
            chat_id = int(item.get("chat_id"))
            message_id = int(item.get("message_id"))
            await bot.delete_message(chat_id, message_id)
            await asyncio.sleep(0.03)
        except Exception as exc:  # pragma: no cover - Telegram runtime only
            print(f"[notify] cannot delete old payment message {item}: {exc}")


def _log_delivery_download(db: Database, *, order_id: int | None, user_id: int | None, source: str, filename: str) -> None:
    """Best-effort audit log for automatic delivery messages sent by runtime loops."""
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO delivery_download_logs(order_id, user_id, source, filename) VALUES(?,?,?,?)",
                (order_id, user_id, source, filename),
            )
    except Exception:
        # Delivery itself must not fail only because an audit row could not be
        # written, especially while settling a paid order.
        pass


async def _send_order_delivery_payload(bot, db: Database, *, telegram_id: int, order_id: int, source: str) -> bool:
    """Send delivered goods for an already-paid order.

    Bank/Pay2S/SePay/Binance webhooks are handled outside an aiogram callback,
    so the usual bot purchase handler is not present to call send_delivery_payload.
    This helper mirrors that behavior for webhook/poller settlements.
    """
    try:
        with db.connect() as conn:
            order = OrderService._get_order_in_conn(conn, int(order_id))
            delivery_rows = OrderService._delivery_in_conn(conn, int(order_id))
        if not delivery_rows:
            await bot.send_message(
                int(telegram_id),
                "⚠️ Đã thanh toán nhưng chưa tìm thấy dữ liệu giao hàng. Hãy liên hệ admin kèm mã đơn.",
                parse_mode="HTML",
            )
            return False

        await bot.send_message(int(telegram_id), views.delivery(order, delivery_rows), parse_mode="HTML")
        if views.delivery_needs_file(order, delivery_rows):
            from aiogram.types import BufferedInputFile

            data = views.delivery_file_text(order, delivery_rows).encode("utf-8")
            doc = BufferedInputFile(data, filename=views.delivery_filename(order))
            await bot.send_document(
                int(telegram_id),
                doc,
                caption=(
                    f"📎 File thông tin hàng cho đơn {views.h(order['public_code'])}. "
                    "Hãy tải xuống và lưu lại."
                ),
                parse_mode="HTML",
            )
            _log_delivery_download(
                db,
                order_id=int(order.get("id") or order_id),
                user_id=int(order.get("user_id") or 0) or None,
                source=source,
                filename=views.delivery_filename(order),
            )
        return True
    except Exception as exc:  # pragma: no cover - Telegram/runtime only
        print(f"[delivery] cannot send order #{order_id} to {telegram_id}: {exc}")
        try:
            await bot.send_message(
                int(telegram_id),
                "⚠️ Thanh toán đã thành công nhưng bot chưa gửi được hàng tự động. "
                f"Hãy thử <code>/taidon {views.h(str(order.get('public_code') if 'order' in locals() else 'ORD...'))}</code> hoặc liên hệ admin.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return False


async def notification_send_loop(bot, db: Database) -> None:
    service = NotificationService(db)
    while True:
        try:
            pending = await asyncio.to_thread(service.pending, 5)
            for item in pending:
                recipients = await asyncio.to_thread(service.recipients, item, 5000)
                try:
                    metadata = loads(str(item.get("metadata_json") or "{}"))
                except Exception:
                    metadata = {}
                sent = 0
                for telegram_id in recipients:
                    try:
                        # Payment-success notices may carry stale QR/instruction
                        # message ids. Delete them first, then send a fresh final
                        # success message so the chat is not left with an active QR.
                        delete_messages = metadata.get("delete_messages") or []
                        if isinstance(delete_messages, list) and delete_messages:
                            await _delete_telegram_messages(bot, delete_messages)
                        await bot.send_message(telegram_id, item["message"], parse_mode="HTML")
                        delivery_order_id = metadata.get("delivery_order_id")
                        if delivery_order_id:
                            await _send_order_delivery_payload(
                                bot,
                                db,
                                telegram_id=int(telegram_id),
                                order_id=int(delivery_order_id),
                                source="bot_webhook_delivery",
                            )
                        sent += 1
                        await asyncio.sleep(0.05)
                    except Exception as exc:  # pragma: no cover - per-user runtime failure
                        print(f"[notify] cannot send to {telegram_id}: {exc}")
                if sent > 0:
                    await asyncio.to_thread(service.mark_sent, int(item["id"]), sent)
                    print(f"[notify] sent notification #{item['id']} to {sent} user(s)")
                else:
                    error = "no active recipient" if not recipients else "all recipient sends failed"
                    await asyncio.to_thread(service.mark_failed, int(item["id"]), error)
                    print(f"[notify] failed notification #{item['id']}: {error}")
        except Exception as exc:  # pragma: no cover - runtime logging only
            print(f"[notify] loop error: {exc}")
        await asyncio.sleep(2)  # responsive admin notifications


def _runtime_setting(db: Database, key: str, fallback: object = "") -> str:
    """Read app_settings first, then .env/runtime Settings fallback.

    Web Admin stores bank/SePay keys in app_settings and only writes .env when
    the owner ticks that option. The automatic SePay poller must therefore read
    the DB as the live source of truth, otherwise auto top-up stays disabled
    after configuration in the Admin panel.
    """
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            if row and str(row["value"] or "").strip() != "":
                return str(row["value"]).strip()
    except Exception:
        pass
    return str(fallback or "").strip()


def _runtime_bool(db: Database, key: str, fallback: bool = False) -> bool:
    value = _runtime_setting(db, key, "true" if fallback else "false").lower()
    return value in {"1", "true", "yes", "on"}


async def _notify_provider_payment(bot, db: Database, applied: dict) -> None:
    """Notify the buyer after a bank provider transaction is actually applied."""
    if applied.get("outcome") != "applied":
        return
    result = applied.get("result") or {}
    intent = result.get("intent") or {}
    user_id = intent.get("user_id")
    if not user_id:
        return
    try:
        with db.connect() as conn:
            user = conn.execute("SELECT telegram_id FROM users WHERE id=?", (int(user_id),)).fetchone()
        if not user:
            return
        await _delete_telegram_messages(bot, payment_prompt_delete_messages(intent))
        text = payment_success_message(result)
        if text:
            await bot.send_message(int(user["telegram_id"]), text, parse_mode="HTML")
        if str(result.get("status") or "") == "order_delivered" and intent.get("order_id"):
            await _send_order_delivery_payload(
                bot,
                db,
                telegram_id=int(user["telegram_id"]),
                order_id=int(intent["order_id"]),
                source="bot_poll_delivery",
            )
    except Exception as exc:  # pragma: no cover - Telegram runtime only
        print(f"[bank-sync] cannot notify payment result: {exc}")


def _bank_poll_accounts(settings: Settings, db: Database) -> tuple[list[dict], int]:
    """Return enabled automatic bank-provider accounts and the base poll interval.

    Legacy single-bank SePay settings are only used when the shop has not been
    migrated to the multi-bank table yet. If any multi-bank account exists, even
    a disabled or incomplete one, do not silently fall back to SEPAY_API_KEY.
    This prevents a Pay2S shop from accidentally calling SePay with a Pay2S/MB
    token and blocking real Pay2S polling with HTTP 400 errors.
    """
    legacy_poll_seconds = max(5, int(_runtime_setting(db, "SEPAY_POLL_SECONDS", settings.sepay_poll_seconds) or settings.sepay_poll_seconds))
    account_service = BankAccountService(db)
    all_accounts = account_service.list_accounts(include_disabled=True)
    accounts = [
        a for a in all_accounts
        if int(a.get("is_enabled") or 0)
        and str(a.get("provider") or "").lower() in {"sepay", "pay2s"}
        and str(a.get("api_key") or "").strip()
    ]

    # Compatibility path for old installs that only used .env/app_settings
    # SEPAY_API_KEY. Never use it once the admin has created multi-bank rows.
    if not all_accounts and _runtime_bool(db, "BANK_ENABLED", settings.bank_enabled):
        api_key = _runtime_setting(db, "SEPAY_API_KEY", settings.sepay_api_key)
        if api_key:
            accounts = [{
                "id": "legacy",
                "label": "Legacy SePay",
                "provider": "sepay",
                "api_key": api_key,
                "account_no": "",
                "base_url": "",
                "poll_seconds": legacy_poll_seconds,
            }]
    return accounts, legacy_poll_seconds


async def sepay_poll_loop(settings: Settings, payments: PaymentService, db: Database, bot=None) -> None:
    while True:
        poll_seconds = 10
        try:
            accounts, legacy_poll_seconds = _bank_poll_accounts(settings, db)
            poll_seconds = legacy_poll_seconds
            if not accounts:
                await asyncio.sleep(min(poll_seconds, 10))
                continue

            combined = {"processed": 0, "duplicates": 0, "unmatched": 0, "invalid": 0, "applied": 0}
            for account in accounts:
                provider = str(account.get("provider") or "sepay").lower()
                account_id = str(account.get("id") or "legacy")
                account_label = str(account.get("label") or account.get("bank_name") or account_id)
                try:
                    account_poll = max(5, int(account.get("poll_seconds") or legacy_poll_seconds))
                    poll_seconds = min(poll_seconds, account_poll)
                    tagged_transactions = []
                    if provider == "pay2s":
                        client = Pay2SClient(Pay2SConfig(
                            token=str(account.get("api_key") or ""),
                            account_no=str(account.get("account_no") or ""),
                            base_url=str(account.get("base_url") or "https://api.pay2s.vn/userapi"),
                        ))
                        transactions = await asyncio.to_thread(client.list_transactions, days=2)
                        apply_fn = apply_pay2s_transactions_detailed
                    elif provider == "sepay":
                        client = SepayClient(str(account.get("api_key") or ""), base_url=str(account.get("base_url") or "https://my.sepay.vn/userapi"))
                        transactions = await asyncio.to_thread(client.list_transactions, limit=50)
                        apply_fn = apply_sepay_transactions_detailed
                    else:
                        continue
                    for tx in transactions:
                        item = dict(tx)
                        item["_bank_account_id"] = account_id
                        item["_bank_account_label"] = account_label
                        tagged_transactions.append(item)
                    detailed = apply_fn(payments, tagged_transactions)
                    summary = detailed.get("summary", {})
                    for item in detailed.get("results", []):
                        if bot is not None:
                            await _notify_provider_payment(bot, db, item)
                    for key in combined:
                        combined[key] += int(summary.get(key) or 0)
                    if summary.get("applied") or summary.get("unmatched") or summary.get("invalid"):
                        print(f"[bank-sync:{provider}] account={account_id} {account_label!r} {summary}")
                except Exception as exc:  # pragma: no cover - provider/runtime logging only
                    # Do not let one bad provider/account block the others. This
                    # was visible as '[sepay] HTTP 400' while Pay2S was never reached.
                    print(f"[bank-sync:{provider}] account={account_id} {account_label!r} polling error: {exc}")
            if combined.get("applied") or combined.get("unmatched") or combined.get("invalid"):
                print(f"[bank-sync] {combined}")
        except Exception as exc:  # pragma: no cover - runtime logging only
            print(f"[bank-sync] loop error: {exc}")
        await asyncio.sleep(poll_seconds)


async def expired_order_notify_loop(bot, db: Database, settings: Settings) -> None:
    orders = __import__("nimo_shop.services.orders", fromlist=["OrderService"]).OrderService(db, settings.order_expires_minutes)
    while True:
        try:
            expired = await asyncio.to_thread(orders.sweep_expired_details)
            for order in expired:
                try:
                    await bot.send_message(
                        order["telegram_id"],
                        f"⏰ <b>Đơn hàng {order['public_code']} đã hết hạn</b>\n\n"
                        f"Sản phẩm: <b>{order['product_name']}</b>\n"
                        "Đơn chưa thanh toán đúng hạn nên đã tự hủy và hàng giữ tạm đã trả về kho.\n"
                        "Bấm /start để tạo đơn mới nếu bạn vẫn muốn mua.",
                        parse_mode="HTML",
                    )
                except Exception as exc:  # pragma: no cover
                    print(f"[expired] cannot notify user {order.get('telegram_id')}: {exc}")
            if expired:
                print(f"[expired] cancelled and notified {len(expired)} order(s)")
        except Exception as exc:  # pragma: no cover
            print(f"[expired] loop error: {exc}")
        await asyncio.sleep(30)


async def set_default_bot_commands(bot) -> None:
    """Publish the command menu shown by Telegram in the bottom-left Menu button."""
    try:
        from aiogram.types import BotCommand
        await bot.set_my_commands([
            BotCommand(command="start", description="Bắt đầu và xem sản phẩm"),
            BotCommand(command="menu", description="Open the main menu"),
            BotCommand(command="products", description="Show products"),
            BotCommand(command="wallet", description="Open wallet"),
            BotCommand(command="search", description="Search products"),
            BotCommand(command="taidon", description="Download delivered order"),
        ])
    except Exception as exc:  # pragma: no cover - Telegram runtime only
        print(f"[bot] cannot set command menu: {exc}")

def run_setup_web(settings: Settings, *, reason: str) -> None:
    """Run web admin setup when the bot cannot start yet."""
    from nimo_shop.web.app import create_server

    host = "0.0.0.0"
    port = 8080
    try:
        from dotenv import load_dotenv
        import os

        load_dotenv()
        host = os.getenv("WEB_HOST", host)
        port = int(os.getenv("WEB_PORT", str(port)))
        username = os.getenv("WEB_ADMIN_USERNAME", "admin")
        password = os.getenv("WEB_ADMIN_PASSWORD") or None
        session_secret = os.getenv("WEB_SESSION_SECRET") or secrets.token_urlsafe(32)
        if not password:
            password = secrets.token_urlsafe(12)
    except Exception:
        username = "admin"
        password = secrets.token_urlsafe(12)
        session_secret = secrets.token_urlsafe(32)

    server = create_server(
        str(settings.database_path),
        host=host,
        port=port,
        session_secret=session_secret,
        project_root=Path.cwd(),
        bootstrap_username=username,
        bootstrap_password=password,
    )
    print("\nNIMO Shop chưa thể chạy bot Telegram.")
    print(f"Lý do: {reason}")
    print(f"Web Admin Setup đang chạy tại: http://127.0.0.1:{port}")
    print(f"Nếu mở từ máy khác cùng WiFi: http://<IP_MAY_NAY>:{port}")
    print(f"Tài khoản setup: {username} / {password}")
    print("Hãy đổi mật khẩu và lưu WEB_SESSION_SECRET/WEB_ADMIN_PASSWORD vào .env trước khi mở public.")
    print("Vào Cấu hình → nhập BOT_TOKEN/ADMIN_IDS/Bank/SePay/Binance → tick 'Ghi ra .env' → Lưu → restart lệnh này.\n")
    server.serve_forever()


async def amain(*, setup_web_on_invalid_token: bool = True) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        load_dotenv = None
    if load_dotenv:
        load_dotenv()
    settings = Settings.from_env()
    db = Database(settings.database_path)
    db.init()

    if not is_configured_bot_token(settings.bot_token):
        if setup_web_on_invalid_token:
            await asyncio.to_thread(run_setup_web, settings, reason="BOT_TOKEN còn trống hoặc đang là token mẫu/sai định dạng")
        else:
            print("NIMO Telegram bot chưa chạy: BOT_TOKEN còn trống hoặc sai định dạng. Web Admin vẫn có thể dùng để cấu hình.")
        return

    try:
        from aiogram import Bot
    except ImportError as exc:
        raise SystemExit("Thiếu aiogram. Chạy: pip install -r requirements.txt") from exc
    from aiogram.utils.token import TokenValidationError

    try:
        bot = Bot(settings.bot_token)
    except TokenValidationError:
        if setup_web_on_invalid_token:
            await asyncio.to_thread(run_setup_web, settings, reason="BOT_TOKEN không hợp lệ theo định dạng BotFather")
        else:
            print("NIMO Telegram bot chưa chạy: BOT_TOKEN không hợp lệ theo định dạng BotFather. Web Admin vẫn đang chạy.")
        return

    await set_default_bot_commands(bot)
    dp = build_dispatcher(settings, db)
    background_tasks = []
    # Always start the SePay watcher. It reads live Admin settings from DB each
    # cycle, so enabling BANK/SEPAY in Web Admin takes effect after restart even
    # when the owner did not write those values to .env.
    background_tasks.append(asyncio.create_task(sepay_poll_loop(settings, PaymentService(db, settings.deposit_expires_minutes), db, bot)))
    background_tasks.append(asyncio.create_task(notification_send_loop(bot, db)))
    background_tasks.append(asyncio.create_task(expired_order_notify_loop(bot, db, settings)))
    try:
        print("NIMO Telegram bot đang chạy. Web admin có thể chạy ở terminal khác bằng: PYTHONPATH=src python -m nimo_shop.web.main --host 0.0.0.0 --port 8080")
        await dp.start_polling(bot)
    finally:
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
