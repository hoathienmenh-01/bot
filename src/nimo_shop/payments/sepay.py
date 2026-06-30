from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import parse, request

from nimo_shop.money import fmt_money, from_minor


@dataclass(frozen=True)
class BankAccount:
    bank_bin: str
    account_no: str
    account_name: str
    bank_name: str = ""


class SepayClient:
    def __init__(self, api_key: str, base_url: str = "https://my.sepay.vn/userapi") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def list_transactions(self, *, limit: int = 50) -> list[dict]:
        req = request.Request(
            f"{self.base_url}/transactions/list?limit={limit}",
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            method="GET",
        )
        with request.urlopen(req, timeout=20) as resp:  # nosec - fixed provider URL by default
            payload = json.loads(resp.read().decode())
        return payload.get("transactions") or payload.get("data") or []


def vietqr_url(bank: BankAccount, *, amount_minor: int, currency: str, add_info: str, template: str = "compact2") -> str:
    # VietQR expects VND integer amount. For non-VND, omit amount and show note only.
    amount = int(from_minor(amount_minor, currency)) if currency.upper() == "VND" else 0
    query = {
        "amount": amount,
        "addInfo": add_info,
        "accountName": bank.account_name,
    }
    return (
        f"https://img.vietqr.io/image/{parse.quote(bank.bank_bin)}-"
        f"{parse.quote(bank.account_no)}-{parse.quote(template)}.png?{parse.urlencode(query)}"
    )


def bank_instruction(bank: BankAccount, *, amount_minor: int, currency: str, payment_code: str) -> str:
    return (
        f"Ngân hàng: {bank.bank_name or bank.bank_bin}\n"
        f"Số tài khoản: {bank.account_no}\n"
        f"Chủ tài khoản: {bank.account_name}\n"
        f"Số tiền: {fmt_money(amount_minor, currency)}\n"
        f"Nội dung chuyển khoản: {payment_code}"
    )
