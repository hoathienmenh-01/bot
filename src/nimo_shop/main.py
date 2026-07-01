from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path

from nimo_shop.bot.app import build_dispatcher
from nimo_shop.config import Settings
from nimo_shop.db import Database
from nimo_shop.payments.pay2s import Pay2SClient, Pay2SConfig
from nimo_shop.payments.sepay import SepayClient
from nimo_shop.services.bank_accounts import BankAccountService
from nimo_shop.services.payments import PaymentService
from nimo_shop.money import fmt_money
from nimo_shop.services.provider_sync import apply_pay2s_transactions_detailed, apply_sepay_transactions_detailed
from nimo_shop.services.notifications import NotificationService

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


async def notification_send_loop(bot, db: Database) -> None:
    service = NotificationService(db)
    while True:
        try:
            pending = await asyncio.to_thread(service.pending, 5)
            for item in pending:
                recipients = await asyncio.to_thread(service.recipients, item, 5000)
                sent = 0
                for telegram_id in recipients:
                    try:
                        await bot.send_message(telegram_id, item["message"], parse_mode="HTML")
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
    """Notify the buyer after a bank provider transaction is actually applied.

    Before this fix the wallet/order was processed silently by the background
    poller, so customers thought bank/Pay2S auto payment was broken because no
    Telegram message was sent after the transfer.
    """
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
        status = str(result.get("status") or "confirmed")
        amount_text = fmt_money(int(intent.get("amount_minor") or 0), str(intent.get("currency") or "VND"))
        code = str(intent.get("public_code") or "")
        if status == "order_delivered":
            text = (
                "✅ <b>Đã xác nhận chuyển khoản thành công</b>\n\n"
                f"Mã thanh toán: <code>{code}</code>\n"
                f"Số tiền: <b>{amount_text}</b>\n"
                "Đơn hàng đã được thanh toán và giao hàng tự động. Bấm /taidon để tải lại hàng nếu cần."
            )
        else:
            balance = result.get("balance_after_minor")
            balance_text = ""
            if balance is not None:
                balance_text = f"\nSố dư sau cộng: <b>{fmt_money(int(balance), str(intent.get('currency') or 'VND'))}</b>"
            text = (
                "✅ <b>Đã nhận chuyển khoản và cộng vào ví</b>\n\n"
                f"Mã nạp: <code>{code}</code>\n"
                f"Số tiền: <b>{amount_text}</b>"
                f"{balance_text}"
            )
        await bot.send_message(int(user["telegram_id"]), text, parse_mode="HTML")
    except Exception as exc:  # pragma: no cover - Telegram runtime only
        print(f"[bank-sync] cannot notify payment result: {exc}")


async def sepay_poll_loop(settings: Settings, payments: PaymentService, db: Database, bot=None) -> None:
    while True:
        poll_seconds = 10
        try:
            bank_enabled = _runtime_bool(db, "BANK_ENABLED", settings.bank_enabled)
            legacy_poll_seconds = max(5, int(_runtime_setting(db, "SEPAY_POLL_SECONDS", settings.sepay_poll_seconds) or settings.sepay_poll_seconds))
            poll_seconds = legacy_poll_seconds

            account_service = BankAccountService(db)
            accounts = [
                a for a in account_service.enabled_accounts()
                if str(a.get("provider") or "").lower() in {"sepay", "pay2s"} and str(a.get("api_key") or "").strip()
            ]

            # Multi-bank accounts have their own enabled switch. Do not require
            # the legacy BANK_ENABLED flag, otherwise a correctly configured
            # Pay2S/SePay account never polls if the old single-bank switch is off.
            if not accounts and bank_enabled:
                api_key = _runtime_setting(db, "SEPAY_API_KEY", settings.sepay_api_key)
                if api_key:
                    accounts = [{"id": "legacy", "label": "Legacy SePay", "provider": "sepay", "api_key": api_key, "base_url": "", "poll_seconds": legacy_poll_seconds}]
            if not accounts:
                await asyncio.sleep(min(poll_seconds, 10))
                continue

            combined = {"processed": 0, "duplicates": 0, "unmatched": 0, "invalid": 0, "applied": 0}
            for account in accounts:
                account_poll = max(5, int(account.get("poll_seconds") or legacy_poll_seconds))
                poll_seconds = min(poll_seconds, account_poll)
                provider = str(account.get("provider") or "sepay").lower()
                tagged_transactions = []
                if provider == "pay2s":
                    client = Pay2SClient(Pay2SConfig(
                        token=str(account.get("api_key") or ""),
                        account_no=str(account.get("account_no") or ""),
                        base_url=str(account.get("base_url") or "https://api.pay2s.vn/userapi"),
                    ))
                    transactions = await asyncio.to_thread(client.list_transactions, days=2)
                    apply_fn = apply_pay2s_transactions_detailed
                else:
                    client = SepayClient(str(account.get("api_key") or ""), base_url=str(account.get("base_url") or "https://my.sepay.vn/userapi"))
                    transactions = await asyncio.to_thread(client.list_transactions, limit=50)
                    apply_fn = apply_sepay_transactions_detailed
                for tx in transactions:
                    item = dict(tx)
                    item["_bank_account_id"] = str(account.get("id") or "legacy")
                    item["_bank_account_label"] = str(account.get("label") or "")
                    tagged_transactions.append(item)
                detailed = apply_fn(payments, tagged_transactions)
                summary = detailed.get("summary", {})
                for item in detailed.get("results", []):
                    if bot is not None:
                        await _notify_provider_payment(bot, db, item)
                for key in combined:
                    combined[key] += int(summary.get(key) or 0)
            if combined.get("applied") or combined.get("unmatched") or combined.get("invalid"):
                print(f"[bank-sync] {combined}")
        except Exception as exc:  # pragma: no cover - runtime logging only
            print(f"[sepay] polling error: {exc}")
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
