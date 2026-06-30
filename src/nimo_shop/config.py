from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    shop_name: str
    admin_ids: tuple[int, ...]
    database_path: Path
    deposit_expires_minutes: int = 15
    order_expires_minutes: int = 20
    bank_enabled: bool = True
    sepay_api_key: str = ""
    bank_bin: str = ""
    bank_account: str = ""
    bank_owner: str = ""
    bank_name: str = ""
    binance_pay_enabled: bool = False
    binance_pay_api_key: str = ""
    binance_pay_secret_key: str = ""
    binance_pay_base_url: str = "https://bpay.binanceapi.com"
    binance_pay_return_url: str = ""
    binance_pay_webhook_url: str = ""
    support_contact: str = ""
    sepay_poll_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bot_token=os.getenv("BOT_TOKEN", ""),
            shop_name=os.getenv("SHOP_NAME", "NIMO SHOP PREMIUM"),
            admin_ids=tuple(int(x) for x in _csv(os.getenv("ADMIN_IDS")) if x.isdigit()),
            database_path=Path(os.getenv("DATABASE_PATH", "data/shop.db")),
            deposit_expires_minutes=int(os.getenv("DEPOSIT_EXPIRES_MINUTES", "15")),
            order_expires_minutes=int(os.getenv("ORDER_EXPIRES_MINUTES", "20")),
            bank_enabled=_bool(os.getenv("BANK_ENABLED"), True),
            sepay_api_key=os.getenv("SEPAY_API_KEY", ""),
            bank_bin=os.getenv("BANK_BIN", ""),
            bank_account=os.getenv("BANK_ACCOUNT", ""),
            bank_owner=os.getenv("BANK_OWNER", ""),
            bank_name=os.getenv("BANK_NAME", ""),
            binance_pay_enabled=_bool(os.getenv("BINANCE_PAY_ENABLED"), False),
            binance_pay_api_key=os.getenv("BINANCE_PAY_API_KEY", ""),
            binance_pay_secret_key=os.getenv("BINANCE_PAY_SECRET_KEY", ""),
            binance_pay_base_url=os.getenv("BINANCE_PAY_BASE_URL", "https://bpay.binanceapi.com"),
            binance_pay_return_url=os.getenv("BINANCE_PAY_RETURN_URL", ""),
            binance_pay_webhook_url=os.getenv("BINANCE_PAY_WEBHOOK_URL", ""),
            support_contact=os.getenv("SUPPORT_CONTACT", ""),
            sepay_poll_seconds=int(os.getenv("SEPAY_POLL_SECONDS", "30")),
        )
