from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from urllib import request


@dataclass(frozen=True)
class Pay2SConfig:
    """Pay2S transaction API settings.

    The normal Pay2S transaction-history API uses the `pay2s-token` header and
    `POST /userapi/transactions`. The partner webhook flow uses a different
    Bearer webhook token; that token is stored in bank_accounts.api_secret and
    verified by the web layer.
    """

    token: str
    account_no: str = ""
    base_url: str = "https://api.pay2s.vn/userapi"


class Pay2SClient:
    def __init__(self, config: Pay2SConfig) -> None:
        if not (config.token or "").strip():
            raise ValueError("Pay2S token is required")
        self.config = config
        self.base_url = (config.base_url or "https://api.pay2s.vn/userapi").rstrip("/")

    def list_transactions(self, *, days: int = 2) -> list[dict]:
        """Fetch recent Pay2S bank transactions for one account.

        Pay2S documents `begin`/`end` as dd/mm/yyyy and `bankAccounts` as the
        account number filter. Polling a short recent window keeps the bot
        idempotent because PaymentService deduplicates by provider transaction id.
        """
        end = date.today()
        begin = end - timedelta(days=max(0, int(days)))
        body = {
            "bankAccounts": self.config.account_no or "",
            "begin": begin.strftime("%d/%m/%Y"),
            "end": end.strftime("%d/%m/%Y"),
        }
        req = request.Request(
            f"{self.base_url}/transactions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "pay2s-token": self.config.token,
            },
            method="POST",
        )
        with request.urlopen(req, timeout=20) as resp:  # nosec - fixed provider URL by default
            payload = json.loads(resp.read().decode("utf-8"))
        transactions = payload.get("transactions") or payload.get("data") or []
        return transactions if isinstance(transactions, list) else []
