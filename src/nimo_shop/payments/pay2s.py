from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from urllib import error, request


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


class Pay2SError(RuntimeError):
    """Raised with provider response details so admins can fix Pay2S config."""


class Pay2SClient:
    def __init__(self, config: Pay2SConfig) -> None:
        token = (config.token or "").strip()
        if not token:
            raise ValueError("Pay2S token is required")
        # Normalize copy/pasted base URLs from Pay2S docs/Admin. Admins often paste
        # the full endpoint; without this guard the client calls /transactions/transactions.
        base_url = (config.base_url or "https://api.pay2s.vn/userapi").strip().rstrip("/")
        if base_url.endswith("/transactions"):
            base_url = base_url[: -len("/transactions")]
        self.config = Pay2SConfig(token=token, account_no=str(config.account_no or "").strip(), base_url=base_url)
        self.base_url = base_url

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
        try:
            with request.urlopen(req, timeout=20) as resp:  # nosec - fixed provider URL by default
                payload = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # Include the provider body, but never echo the token. This turns a vague
            # HTTP 400 into an actionable Pay2S config error in the bot log.
            raise Pay2SError(
                f"Pay2S HTTP {exc.code}: {detail[:1000]} | "
                f"url={self.base_url}/transactions account={self.config.account_no or '<all>'}"
            ) from exc
        except error.URLError as exc:
            raise Pay2SError(f"Pay2S connection error: {exc}") from exc
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
