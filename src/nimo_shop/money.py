from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

SCALE = {
    "VND": 1,
    "USDT": 1_000_000,
    "USD": 100,
}


def normalize_currency(currency: str) -> str:
    cur = (currency or "").strip().upper()
    if cur not in SCALE:
        raise ValueError(f"Unsupported currency: {currency}")
    return cur


def to_minor(amount: int | float | str | Decimal, currency: str) -> int:
    cur = normalize_currency(currency)
    scale = SCALE[cur]
    value = Decimal(str(amount))
    if value < 0:
        raise ValueError("amount must be non-negative")
    if scale == 1:
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return int((value * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor(amount_minor: int, currency: str) -> Decimal:
    cur = normalize_currency(currency)
    return Decimal(amount_minor) / Decimal(SCALE[cur])


def fmt_money(amount_minor: int, currency: str) -> str:
    cur = normalize_currency(currency)
    value = from_minor(amount_minor, cur)
    if cur == "VND":
        return f"{int(value):,}đ".replace(",", ".")
    if cur == "USDT":
        return f"{value.normalize()} USDT"
    return f"{value:.2f} {cur}"
