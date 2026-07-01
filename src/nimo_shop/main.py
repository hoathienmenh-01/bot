from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path

from nimo_shop.bot.app import build_dispatcher
from nimo_shop.config import Settings
from nimo_shop.db import Database
from nimo_shop.payments.sepay import SepayClient
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.provider_sync import apply_sepay_transactions
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
        await asyncio.sleep(15)


async def sepay_poll_loop(settings: Settings, payments: PaymentService) -> None:
    client = SepayClient(settings.sepay_api_key)
    while True:
        try:
            transactions = await asyncio.to_thread(client.list_transactions, limit=50)
            summary = apply_sepay_transactions(payments, transactions)
            if summary.get("applied") or summary.get("unmatched"):
                print(f"[sepay] {summary}")
        except Exception as exc:  # pragma: no cover - runtime logging only
            print(f"[sepay] polling error: {exc}")
        await asyncio.sleep(max(10, int(settings.sepay_poll_seconds)))



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
    if settings.bank_enabled and settings.sepay_api_key:
        background_tasks.append(asyncio.create_task(sepay_poll_loop(settings, PaymentService(db, settings.deposit_expires_minutes))))
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
