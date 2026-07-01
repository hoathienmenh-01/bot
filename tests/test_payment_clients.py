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
from nimo_shop.main import _bank_poll_accounts
from nimo_shop.money import to_minor
from nimo_shop.payments.binance_pay import BinancePayClient, BinancePayConfig
from nimo_shop.payments.pay2s import Pay2SClient, Pay2SConfig
from nimo_shop.payments.sepay import BankAccount, bank_instruction, vietqr_url
from nimo_shop.services.bank_accounts import BankAccountService
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.payment_notices import remember_payment_prompt_messages, queue_payment_success_notice
from nimo_shop.services.provider_sync import apply_pay2s_transactions, apply_pay2s_transactions_detailed, apply_sepay_transactions, normalize_pay2s_transaction
from nimo_shop.services.users import UserService
from nimo_shop.services.wallet import WalletService
from nimo_shop.services.notifications import NotificationService


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

    def test_multi_bank_sepay_same_real_tx_is_idempotent_across_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(123, "buyer", "Buyer")
            intent = PaymentService(db).create_wallet_topup_intent(user_id=user_id, provider="bank", currency="VND", amount_minor=100_000)
            tx = {"id": "DUP001", "amount_in": 100000, "transaction_content": f"nap {intent['public_code']}"}
            summary1 = apply_sepay_transactions(PaymentService(db), [{**tx, "_bank_account_id": 1}])
            summary2 = apply_sepay_transactions(PaymentService(db), [{**tx, "_bank_account_id": 2}])
            self.assertEqual(summary1["applied"], 1)
            self.assertEqual(summary2["duplicates"], 1)
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM external_payment_events").fetchone()["c"], 1)
            # Same provider transaction id arriving from two configured accounts is
            # one real bank transaction, not two payments to credit twice.
            self.assertEqual(WalletService(db).get_balances(user_id)["VND"], 100_000)



    def test_pay2s_multibank_disables_bad_legacy_sepay_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            svc = BankAccountService(db)
            svc.create({
                "label": "MB Pay2S",
                "bank_name": "MB Bank",
                "bank_bin": "970422",
                "account_no": "24301999999",
                "account_name": "PHAM XUAN TOI",
                "provider": "pay2s",
                "api_key": "pay2s-token",
                "is_enabled": "on",
                "is_default": "on",
            })
            accounts, _ = _bank_poll_accounts(Settings(bot_token="token", shop_name="NIMO", admin_ids=(), database_path=db.path, bank_enabled=True, sepay_api_key="wrong-legacy-key"), db)
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["provider"], "pay2s")
            self.assertEqual(accounts[0]["api_key"], "pay2s-token")

    def test_multibank_rows_prevent_legacy_sepay_even_when_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            BankAccountService(db).create({
                "label": "MB chưa nhập key",
                "bank_name": "MB Bank",
                "bank_bin": "970422",
                "account_no": "24301999999",
                "account_name": "PHAM XUAN TOI",
                "provider": "pay2s",
                "api_key": "",
                "is_enabled": "on",
            })
            accounts, _ = _bank_poll_accounts(Settings(bot_token="token", shop_name="NIMO", admin_ids=(), database_path=db.path, bank_enabled=True, sepay_api_key="legacy-sepay-key"), db)
            self.assertEqual(accounts, [])


    def test_pay2s_client_normalizes_copied_full_endpoint(self) -> None:
        client = Pay2SClient(Pay2SConfig(
            token=" token-with-spaces ",
            account_no=" 24301999999 ",
            base_url="https://api.pay2s.vn/userapi/transactions",
        ))
        self.assertEqual(client.base_url, "https://api.pay2s.vn/userapi")
        self.assertEqual(client.config.account_no, "24301999999")
        self.assertEqual(client.config.token, "token-with-spaces")

    def test_pay2s_client_extracts_nested_transaction_payloads(self) -> None:
        payloads = [
            {"transactions": [{"id": 1}]},
            {"data": {"transactions": [{"id": 2}]}},
            {"data": {"items": [{"id": 3}]}},
            {"records": [{"id": 4}]},
            [{"id": 5}],
        ]
        client = Pay2SClient(Pay2SConfig(token="token"))
        self.assertEqual([client._extract_transactions(p)[0]["id"] for p in payloads], [1, 2, 3, 4, 5])

    def test_pay2s_auto_payment_matches_bank_intent_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(567890, "pay2s", "Pay2S User")
            intent = PaymentService(db).create_wallet_topup_intent(user_id=user_id, provider="bank", currency="VND", amount_minor=120_000)
            tx = {
                "id": "pay2s-row-1",
                "transaction_id": "MBB-888",
                "account_number": "24301999999",
                "bank": "MBB",
                "amount": 120000,
                "description": f"QR - {intent['public_code']} GD 001",
                "type": "IN",
                "_bank_account_id": 11,
            }
            summary = apply_pay2s_transactions(PaymentService(db), [tx])
            self.assertEqual(summary["applied"], 1)
            self.assertEqual(WalletService(db).get_balances(user_id)["VND"], 120_000)

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
            self.assertEqual(normalized["provider_tx_id"], "pay2s:10439")
            summary = apply_pay2s_transactions(PaymentService(db), [tx])
            self.assertEqual(summary["applied"], 1)
            self.assertEqual(WalletService(db).get_balances(user_id)["VND"], 50_000)


    def test_pay2s_webhook_and_poller_do_not_double_credit_same_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(222444, "buyer", "Buyer")
            intent = PaymentService(db).create_wallet_topup_intent(user_id=user_id, provider="bank", currency="VND", amount_minor=50_000)
            webhook_tx = {
                "id": "TX10439",
                "transactionNumber": "TX10439",
                "accountNumber": "737478888",
                "transferAmount": 50000,
                "content": f"QR {intent['public_code']}",
                "transferType": "IN",
                "_bank_account_id": 9,
            }
            poll_tx = {
                "transaction_id": "TX10439",
                "account_number": "737478888",
                "amount": "50.000",
                "description": f"QR {intent['public_code']}",
                "type": "IN",
                "_bank_account_id": 9,
            }
            first = apply_pay2s_transactions(PaymentService(db), [webhook_tx])
            second = apply_pay2s_transactions(PaymentService(db), [poll_tx])
            self.assertEqual(first["applied"], 1)
            self.assertEqual(second["duplicates"], 1)
            self.assertEqual(WalletService(db).get_balances(user_id)["VND"], 50_000)
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM external_payment_events WHERE provider_tx_id='pay2s:TX10439'").fetchone()["c"], 1)

    def test_selected_bank_account_mismatch_is_not_auto_credited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(222555, "buyer", "Buyer")
            accounts = BankAccountService(db)
            expected_id = accounts.create({
                "label": "MB chính", "bank_name": "MB Bank", "bank_bin": "970422",
                "account_no": "111111111", "account_name": "SHOP", "provider": "pay2s",
                "api_key": "token-a", "is_enabled": "on", "is_default": "on",
            })
            wrong_id = accounts.create({
                "label": "MB phụ", "bank_name": "MB Bank", "bank_bin": "970422",
                "account_no": "222222222", "account_name": "SHOP", "provider": "pay2s",
                "api_key": "token-b", "is_enabled": "on",
            })
            intent = PaymentService(db).create_wallet_topup_intent(
                user_id=user_id, provider="bank", currency="VND", amount_minor=100_000, bank_account_id=expected_id
            )
            tx = {
                "transaction_id": "WRONG-ACCOUNT-1",
                "account_number": "222222222",
                "amount": 100000,
                "description": f"Nap {intent['public_code']}",
                "type": "IN",
                "_bank_account_id": wrong_id,
            }
            summary = apply_pay2s_transactions(PaymentService(db), [tx])
            self.assertEqual(summary["unmatched"], 1)
            self.assertEqual(WalletService(db).get_balances(user_id), {})
            with db.connect() as conn:
                event = conn.execute("SELECT status FROM external_payment_events WHERE provider_tx_id='pay2s:WRONG-ACCOUNT-1'").fetchone()
                self.assertEqual(event["status"], "account_mismatch")


    def test_webhook_payment_queues_success_notice_and_old_qr_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(555666, "buyer", "Buyer")
            intent = PaymentService(db).create_wallet_topup_intent(user_id=user_id, provider="bank", currency="VND", amount_minor=70_000)
            remember_payment_prompt_messages(db, intent_id=int(intent["id"]), chat_id=555666, message_ids=[10, 11])
            with db.connect() as conn:
                intent = dict(conn.execute("SELECT * FROM payment_intents WHERE id=?", (intent["id"],)).fetchone())
            tx = {
                "id": "PAY2S-NOTIFY-1",
                "transactionNumber": "PAY2S-NOTIFY-1",
                "accountNumber": "24301999999",
                "transferAmount": 70000,
                "content": f"Nap vi {intent['public_code']}",
                "transferType": "IN",
            }
            detailed = apply_pay2s_transactions_detailed(PaymentService(db), [tx])
            self.assertEqual(detailed["summary"]["applied"], 1)
            queued_id = queue_payment_success_notice(db, detailed["results"][0])
            self.assertIsNotNone(queued_id)
            self.assertEqual(WalletService(db).get_balances(user_id)["VND"], 70_000)
            note = NotificationService(db).pending()[0]
            self.assertEqual(note["kind"], "payment_success")
            self.assertIn("Nạp tiền thành công", note["message"])
            metadata = json.loads(note["metadata_json"])
            self.assertEqual(metadata["delete_messages"], [{"chat_id": 555666, "message_id": 10}, {"chat_id": 555666, "message_id": 11}])

    def test_order_webhook_notice_carries_delivery_order_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(555777, "buyer", "Buyer")
            with db.transaction() as conn:
                cat_id = int(conn.execute("INSERT INTO categories(name) VALUES('Cat')").lastrowid)
            product_id = CatalogService(db).add_product(
                category_id=cat_id,
                name="Auto Delivery Item",
                description="",
                currency="VND",
                price_minor=25_000,
            )
            CatalogService(db).add_stock(product_id, ["account|password"])
            order = OrderService(db, order_expires_minutes=15).create_order(user_id=user_id, product_id=product_id)
            intent = PaymentService(db).create_order_payment_intent(order_id=order["id"], provider="bank", expected_user_id=user_id)
            remember_payment_prompt_messages(db, intent_id=int(intent["id"]), chat_id=555777, message_ids=[20, 21])
            with db.connect() as conn:
                intent = dict(conn.execute("SELECT * FROM payment_intents WHERE id=?", (intent["id"],)).fetchone())
            tx = {
                "id": "PAY2S-ORDER-NOTIFY-1",
                "transactionNumber": "PAY2S-ORDER-NOTIFY-1",
                "accountNumber": "24301999999",
                "transferAmount": 25000,
                "content": f"Thanh toan {intent['public_code']}",
                "transferType": "IN",
            }
            detailed = apply_pay2s_transactions_detailed(PaymentService(db), [tx])
            self.assertEqual(detailed["summary"]["applied"], 1)
            self.assertEqual(detailed["results"][0]["result"]["status"], "order_delivered")
            queued_id = queue_payment_success_notice(db, detailed["results"][0])
            self.assertIsNotNone(queued_id)
            note = NotificationService(db).pending()[0]
            self.assertIn("Bot sẽ gửi hàng ngay bên dưới", note["message"])
            self.assertIn(f"/taidon {intent['public_code']}", note["message"])
            metadata = json.loads(note["metadata_json"])
            self.assertEqual(metadata["payment_status"], "order_delivered")
            self.assertEqual(metadata["delivery_order_id"], order["id"])
            self.assertEqual(metadata["delete_messages"], [{"chat_id": 555777, "message_id": 20}, {"chat_id": 555777, "message_id": 21}])

    def test_pay2s_outgoing_transaction_is_not_applied(self) -> None:
        row = {"transaction_id": "OUT1", "amount": 50000, "description": "NAPTEST", "type": "OUT"}
        with self.assertRaises(ValueError):
            normalize_pay2s_transaction(row)


if __name__ == "__main__":
    unittest.main()
