from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib import request


@dataclass(frozen=True)
class BinancePayConfig:
    api_key: str
    secret_key: str
    base_url: str = "https://bpay.binanceapi.com"


class BinancePayClient:
    """Small Binance Pay v3 client.

    It prepares signed requests for merchant order creation. Network calls are kept
    in one place so tests can mock/replace this class easily.
    """

    def __init__(self, config: BinancePayConfig) -> None:
        self.config = config

    def _headers(self, body: dict[str, Any]) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        signature_payload = f"{timestamp}\n{nonce}\n{payload}\n"
        signature = hmac.new(
            self.config.secret_key.encode(),
            signature_payload.encode(),
            hashlib.sha512,
        ).hexdigest().upper()
        return {
            "Content-Type": "application/json",
            "BinancePay-Timestamp": timestamp,
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": self.config.api_key,
            "BinancePay-Signature": signature,
        }

    def create_order_payload(
        self,
        *,
        merchant_trade_no: str,
        product_name: str,
        amount: str,
        currency: str = "USDT",
        return_url: str | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "env": {"terminalType": "APP"},
            "merchantTradeNo": merchant_trade_no,
            "orderAmount": amount,
            "currency": currency,
            "goods": {
                "goodsType": "02",
                "goodsCategory": "D000",
                "referenceGoodsId": merchant_trade_no,
                "goodsName": product_name[:256],
            },
        }
        if return_url:
            payload["returnUrl"] = return_url
        if webhook_url:
            payload["webhookUrl"] = webhook_url
        return payload

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        req = request.Request(
            f"{self.config.base_url.rstrip('/')}/binancepay/openapi/v3/order",
            data=data,
            headers=self._headers(payload),
            method="POST",
        )
        with request.urlopen(req, timeout=20) as resp:  # nosec - URL is user configuration
            return json.loads(resp.read().decode())

    def verify_webhook_signature(self, *, timestamp: str, nonce: str, body: str, signature: str) -> bool:
        payload = f"{timestamp}\n{nonce}\n{body}\n"
        expected = hmac.new(self.config.secret_key.encode(), payload.encode(), hashlib.sha512).hexdigest().upper()
        return hmac.compare_digest(expected, signature.upper())
