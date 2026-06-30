from __future__ import annotations

from dataclasses import dataclass

from nimo_shop.money import to_minor


@dataclass(frozen=True)
class ProductCommand:
    category_id: int
    name: str
    price_minor: int
    cost_minor: int
    description: str
    warranty_text: str


def command_body(text: str, command: str) -> str:
    parts = text.split(maxsplit=1)
    if not parts or not parts[0].split('@', 1)[0].lower() == command.lower():
        raise ValueError(f"expected {command}")
    return parts[1].strip() if len(parts) > 1 else ""


def parse_add_product(text: str) -> ProductCommand:
    body = command_body(text, "/addproduct")
    pieces = [p.strip() for p in body.split("|")]
    if len(pieces) < 5:
        raise ValueError("format: /addproduct category_id | name | price_vnd | cost_vnd | description | warranty")
    category_id = int(pieces[0])
    name = pieces[1]
    price_minor = to_minor(pieces[2], "VND")
    cost_minor = to_minor(pieces[3], "VND")
    description = pieces[4]
    warranty_text = pieces[5] if len(pieces) > 5 else ""
    if not name:
        raise ValueError("product name is required")
    return ProductCommand(category_id, name, price_minor, cost_minor, description, warranty_text)


def parse_add_stock(text: str) -> tuple[int, list[str]]:
    body = command_body(text, "/addstock")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("format: /addstock product_id then stock lines")
    product_id = int(lines[0])
    return product_id, lines[1:]


def parse_confirm(text: str) -> tuple[str, str, int, str, str]:
    body = command_body(text, "/confirm")
    parts = body.split()
    if len(parts) < 3:
        raise ValueError("format: /confirm PAYMENT_CODE TX_ID AMOUNT [CURRENCY] [PROVIDER]")
    payment_code, tx_id, amount = parts[:3]
    currency = parts[3].upper() if len(parts) >= 4 else "VND"
    provider = parts[4].lower() if len(parts) >= 5 else "bank"
    amount_minor = to_minor(amount, currency)
    return payment_code.upper(), tx_id, amount_minor, currency, provider


def parse_one_int_arg(text: str, command: str) -> int:
    body = command_body(text, command)
    if not body:
        raise ValueError(f"format: {command} ID")
    return int(body.split()[0])
