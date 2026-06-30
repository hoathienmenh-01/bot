from __future__ import annotations

import asyncio

from nimo_shop.bot.app import build_dispatcher
from nimo_shop.config import Settings
from nimo_shop.db import Database
from nimo_shop.payments.sepay import SepayClient
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.provider_sync import apply_sepay_transactions


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


async def amain() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        load_dotenv = None
    if load_dotenv:
        load_dotenv()
    try:
        from aiogram import Bot
    except ImportError as exc:
        raise SystemExit("Thiếu aiogram. Chạy: pip install -r requirements.txt") from exc
    settings = Settings.from_env()
    if not settings.bot_token:
        raise SystemExit("Thiếu BOT_TOKEN trong .env hoặc biến môi trường")
    db = Database(settings.database_path)
    db.init()
    bot = Bot(settings.bot_token)
    dp = build_dispatcher(settings, db)
    poll_task = None
    if settings.bank_enabled and settings.sepay_api_key:
        poll_task = asyncio.create_task(sepay_poll_loop(settings, PaymentService(db, settings.deposit_expires_minutes)))
    try:
        await dp.start_polling(bot)
    finally:
        if poll_task:
            poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
