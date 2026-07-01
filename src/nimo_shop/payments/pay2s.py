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
        return self._extract_transactions(payload)

    @staticmethod
    def _extract_transactions(payload: object) -> list[dict]:
        """Return transaction rows from Pay2S responses without assuming one shape.

        Pay2S' documented history API returns `{transactions:[...]}`, but some
        accounts/proxy versions wrap rows in `data.transactions`, `data.items`,
        `records` or return the list directly. Being permissive here prevents
        a successful Pay2S response from being treated as an empty transaction
        list, which was the reason auto top-up looked dead while Pay2S already
        had the transfer.
        """
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            return []
        candidates = [
            payload.get("transactions"),
            payload.get("data"),
            payload.get("items"),
            payload.get("records"),
            payload.get("results"),
        ]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([
                data.get("transactions"),
                data.get("items"),
                data.get("records"),
                data.get("results"),
                data.get("list"),
            ])
        for value in candidates:
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []
