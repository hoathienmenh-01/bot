from __future__ import annotations

import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

from nimo_shop.db import Database
from nimo_shop.config import Settings
from nimo_shop.money import to_minor
from nimo_shop.payments.binance_pay import BinancePayClient, BinancePayConfig
from nimo_shop.payments.sepay import BankAccount, bank_instruction, vietqr_url
from nimo_shop.services.bank_accounts import BankAccountService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.provider_sync import apply_pay2s_transactions, apply_sepay_transactions, normalize_pay2s_transaction
from nimo_shop.services.users import UserService
from nimo_shop.services.wallet import WalletService


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

    def test_multi_bank_accounts_store_per_account_api_and_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            svc = BankAccountService(db)
            mb_id = svc.create({
                "label": "MB chính",
                "bank_name": "MB Bank",
                "bank_bin": "970422",
                "account_no": "123456789",
                "account_name": "PHAM XUAN TOI",
                "provider": "sepay",
                "api_key": "sepay-mb-key",
                "is_enabled": "on",
                "is_default": "on",
            })
            vcb_id = svc.create({
                "label": "VCB phụ",
                "bank_name": "Vietcombank",
                "bank_bin": "970436",
                "account_no": "987654321",
                "account_name": "PHAM XUAN TOI",
                "provider": "pay2s",
                "api_key": "pay2s-token",
                "api_secret": "pay2s-webhook-token",
                "is_enabled": "on",
            })
            self.assertEqual(svc.default_account()["id"], mb_id)
            svc.set_default(vcb_id)
            self.assertEqual(svc.default_account()["id"], vcb_id)
            svc.update(mb_id, {
                "label": "MB đã sửa",
                "bank_name": "MB Bank",
                "bank_bin": "970422",
                "account_no": "123456789",
                "account_name": "PHAM XUAN TOI",
                "provider": "sepay",
                "api_key": "",
                "is_enabled": "on",
            })
            self.assertEqual(svc.get(mb_id)["api_key"], "sepay-mb-key")
            self.assertEqual(svc.get(vcb_id)["provider"], "pay2s")
            self.assertEqual(svc.get(vcb_id)["api_key"], "pay2s-token")
            self.assertEqual(svc.get(vcb_id)["api_secret"], "pay2s-webhook-token")
            self.assertEqual(len(svc.enabled_accounts()), 2)

    def test_multi_bank_sepay_prefix_prevents_tx_id_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(123, "buyer", "Buyer")
            intent = PaymentService(db).create_wallet_topup_intent(user_id=user_id, provider="bank", currency="VND", amount_minor=100_000)
            tx = {"id": "DUP001", "amount_in": 100000, "transaction_content": f"nap {intent['public_code']}"}
            summary1 = apply_sepay_transactions(PaymentService(db), [{**tx, "_bank_account_id": 1}])
            summary2 = apply_sepay_transactions(PaymentService(db), [{**tx, "_bank_account_id": 2}])
            self.assertEqual(summary1["applied"], 1)
            self.assertEqual(summary2["applied"], 1)
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM external_payment_events").fetchone()["c"], 2)
            # First payment confirms the intent, second real payment with same code
            # becomes an extra wallet credit instead of being ignored as duplicate.
            self.assertEqual(WalletService(db).get_balances(user_id)["VND"], 200_000)

    def test_pay2s_transactions_are_normalized_and_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(222333, "buyer", "Buyer")
            intent = PaymentService(db).create_wallet_topup_intent(user_id=user_id, provider="bank", currency="VND", amount_minor=50_000)
            tx = {
                "transaction_id": "10439",
                "account_number": "737478888",
                "bank": "MBB",
                "amount": 50000,
                "description": f"QR - {intent['public_code']}",
                "type": "IN",
                "_bank_account_id": 9,
            }
            normalized = normalize_pay2s_transaction(tx)
            self.assertEqual(normalized["provider"], "bank")
            self.assertIn("pay2s:bankacct:9:10439", normalized["provider_tx_id"])
            summary = apply_pay2s_transactions(PaymentService(db), [tx])
            self.assertEqual(summary["applied"], 1)
            self.assertEqual(WalletService(db).get_balances(user_id)["VND"], 50_000)

    def test_pay2s_outgoing_transaction_is_not_applied(self) -> None:
        row = {"transaction_id": "OUT1", "amount": 50000, "description": "NAPTEST", "type": "OUT"}
        with self.assertRaises(ValueError):
            normalize_pay2s_transaction(row)


if __name__ == "__main__":
    unittest.main()
