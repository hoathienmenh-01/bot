from __future__ import annotations

import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch

from nimo_shop.config import Settings
from nimo_shop.money import to_minor
from nimo_shop.payments.binance_pay import BinancePayClient, BinancePayConfig
from nimo_shop.payments.sepay import BankAccount, bank_instruction, vietqr_url


class PaymentClientHelpersTest(unittest.TestCase):
    def test_vietqr_url_contains_bank_amount_and_payment_code(self) -> None:
        bank = BankAccount(bank_bin="970436", account_no="0123456789", account_name="PHAM XUAN TOI", bank_name="VCB")
        url = vietqr_url(bank, amount_minor=150_000, currency="VND", add_info="ORDABCDEF12")
        self.assertIn("970436-0123456789-compact2.png", url)
        self.assertIn("amount=150000", url)
        self.assertIn("addInfo=ORDABCDEF12", url)
        self.assertIn("accountName=PHAM+XUAN+TOI", url)

    def test_bank_instruction_formats_payment_details(self) -> None:
        bank = BankAccount(bank_bin="970436", account_no="0123456789", account_name="PHAM XUAN TOI", bank_name="Vietcombank")
        text = bank_instruction(bank, amount_minor=150_000, currency="VND", payment_code="NAPABCDEF12")
        self.assertIn("Vietcombank", text)
        self.assertIn("0123456789", text)
        self.assertIn("150.000đ", text)
        self.assertIn("NAPABCDEF12", text)

    def test_vietqr_non_vnd_omits_numeric_amount(self) -> None:
        bank = BankAccount(bank_bin="970436", account_no="0123456789", account_name="PHAM XUAN TOI")
        url = vietqr_url(bank, amount_minor=to_minor("10", "USDT"), currency="USDT", add_info="NAPUSDT0001")
        self.assertIn("amount=0", url)

    def test_binance_pay_create_order_payload_is_merchant_order_v3_shape(self) -> None:
        client = BinancePayClient(BinancePayConfig(api_key="api", secret_key="secret"))
        payload = client.create_order_payload(
            merchant_trade_no="ORDABCDEF12",
            product_name="ChatGPT Plus 1 tháng",
            amount="10.50",
            currency="USDT",
            return_url="https://shop.example/return",
            webhook_url="https://shop.example/webhook",
        )
        self.assertEqual(payload["merchantTradeNo"], "ORDABCDEF12")
        self.assertEqual(payload["orderAmount"], "10.50")
        self.assertEqual(payload["currency"], "USDT")
        self.assertEqual(payload["goods"]["goodsType"], "02")
        self.assertEqual(payload["returnUrl"], "https://shop.example/return")
        self.assertEqual(payload["webhookUrl"], "https://shop.example/webhook")

    def test_binance_pay_webhook_signature_verification(self) -> None:
        secret = "very-secret"
        body = json.dumps({"bizStatus": "PAY_SUCCESS", "data": {"merchantTradeNo": "ORDABCDEF12"}}, separators=(",", ":"))
        timestamp = "1710000000000"
        nonce = "abc123"
        expected = hmac.new(secret.encode(), f"{timestamp}\n{nonce}\n{body}\n".encode(), hashlib.sha512).hexdigest().upper()
        client = BinancePayClient(BinancePayConfig(api_key="api", secret_key=secret))
        self.assertTrue(client.verify_webhook_signature(timestamp=timestamp, nonce=nonce, body=body, signature=expected))
        self.assertFalse(client.verify_webhook_signature(timestamp=timestamp, nonce=nonce, body=body, signature="bad"))

    def test_settings_boolean_and_admin_id_parsing(self) -> None:
        env = {
            "BOT_TOKEN": "token",
            "SHOP_NAME": "NIMO",
            "ADMIN_IDS": "123, bad,456",
            "BANK_ENABLED": "false",
            "BINANCE_PAY_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.bot_token, "token")
        self.assertEqual(settings.admin_ids, (123, 456))
        self.assertFalse(settings.bank_enabled)
        self.assertTrue(settings.binance_pay_enabled)


if __name__ == "__main__":
    unittest.main()
