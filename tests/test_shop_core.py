from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nimo_shop.db import Database
from nimo_shop.money import fmt_money, to_minor
from nimo_shop.services.audit import AuditService
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.finance import FinanceService
from nimo_shop.services.orders import OrderOwnershipError, OrderService, OrderStateError, OutOfStock, iso, utcnow
from nimo_shop.services.payments import PaymentMatchError, PaymentService
from nimo_shop.services.users import UserService
from nimo_shop.services.wallet import InsufficientFunds, WalletService


class ShopCoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "shop.db")
        self.db.init()
        self.users = UserService(self.db)
        self.catalog = CatalogService(self.db)
        self.wallet = WalletService(self.db)
        self.orders = OrderService(self.db, order_expires_minutes=20)
        self.payments = PaymentService(self.db, deposit_expires_minutes=15)
        self.finance = FinanceService(self.db)
        self.audit = AuditService(self.db)
        self.user_id = self.users.get_or_create(telegram_id=111, username="buyer", full_name="Buyer")
        self.second_user_id = self.users.get_or_create(telegram_id=222, username="buyer2", full_name="Buyer Two")
        cat_id = self.catalog.add_category("ChatGPT")
        self.product_id = self.catalog.add_product(
            category_id=cat_id,
            name="ChatGPT Plus 1 tháng",
            description="Tài khoản dùng 30 ngày",
            currency="VND",
            price_minor=150_000,
            cost_minor=100_000,
            warranty_text="1 đổi 1 trong thời hạn bảo hành",
        )
        inserted = self.catalog.add_stock(self.product_id, ["acc1|pass1", "acc2|pass2"])
        self.assertEqual(inserted, 2)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _one(self, sql: str, params: tuple = ()):  # noqa: ANN001
        with self.db.connect() as conn:
            return conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()):  # noqa: ANN001
        with self.db.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def test_create_order_reserves_stock_and_prevents_oversell(self) -> None:
        order1 = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=2)
        self.assertEqual(order1["status"], "awaiting_payment")
        with self.assertRaises(OutOfStock):
            self.orders.create_order(user_id=self.second_user_id, product_id=self.product_id, quantity=1)
        summary = self.catalog.stock_summary()[0]
        self.assertEqual(summary["available"], 0)
        self.assertEqual(summary["reserved"], 2)

    def test_duplicate_stock_import_is_not_inserted_twice(self) -> None:
        inserted = self.catalog.add_stock(self.product_id, ["acc2|pass2", "acc3|pass3", "acc3|pass3"])
        self.assertEqual(inserted, 1)
        row = self._one("SELECT COUNT(*) AS c FROM stock_items WHERE product_id=?", (self.product_id,))
        self.assertEqual(row["c"], 3)

    def test_wallet_credit_debit_are_idempotent(self) -> None:
        self.assertEqual(self.wallet.credit(self.user_id, "VND", 200_000, reason="manual", idempotency_key="credit-1"), 200_000)
        self.assertEqual(self.wallet.credit(self.user_id, "VND", 200_000, reason="manual", idempotency_key="credit-1"), 200_000)
        self.assertEqual(self.wallet.debit(self.user_id, "VND", 50_000, reason="manual", idempotency_key="debit-1"), 150_000)
        self.assertEqual(self.wallet.debit(self.user_id, "VND", 50_000, reason="manual", idempotency_key="debit-1"), 150_000)
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 150_000)
        row = self._one("SELECT COUNT(*) AS c FROM ledger_entries")
        self.assertEqual(row["c"], 2)

    def test_wallet_rejects_zero_and_negative_amounts(self) -> None:
        with self.assertRaises(ValueError):
            self.wallet.credit(self.user_id, "VND", 0, reason="bad", idempotency_key="credit-zero")
        with self.assertRaises(ValueError):
            self.wallet.credit(self.user_id, "VND", -1, reason="bad", idempotency_key="credit-neg")
        self.wallet.credit(self.user_id, "VND", 100_000, reason="manual", idempotency_key="credit-ok")
        with self.assertRaises(ValueError):
            self.wallet.debit(self.user_id, "VND", 0, reason="bad", idempotency_key="debit-zero")
        with self.assertRaises(ValueError):
            self.wallet.debit(self.user_id, "VND", -50_000, reason="bad", idempotency_key="debit-neg")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 100_000)

    def test_wallet_payment_delivers_and_records_cash_ledger(self) -> None:
        self.wallet.credit(self.user_id, "VND", 300_000, reason="admin", idempotency_key="admin:topup:1")
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        result = self.orders.pay_with_wallet(order["id"])
        self.assertEqual(result["order"]["status"], "delivered")
        self.assertEqual(len(result["delivery"]), 1)
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 150_000)
        finance = self.finance.summary()
        self.assertEqual(finance["sales"][0]["revenue_minor"], 150_000)
        self.assertEqual(finance["sales"][0]["cost_minor"], 100_000)

    def test_wallet_payment_retry_after_delivery_does_not_double_debit_or_double_deliver(self) -> None:
        self.wallet.credit(self.user_id, "VND", 150_000, reason="admin", idempotency_key="admin:topup:retry")
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        first = self.orders.pay_with_wallet(order["id"])
        second = self.orders.pay_with_wallet(order["id"])
        self.assertEqual(first["order"]["status"], "delivered")
        self.assertEqual(second["order"]["status"], "delivered")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 0)
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM deliveries WHERE order_id=?", (order["id"],))["c"], 1)
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM ledger_entries WHERE reference_type='order'", ())["c"], 1)

    def test_insufficient_wallet_does_not_sell_stock(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        with self.assertRaises(InsufficientFunds):
            self.orders.pay_with_wallet(order["id"])
        o = self._one("SELECT status FROM orders WHERE id=?", (order["id"],))
        stock = self._all("SELECT status FROM stock_items WHERE reserved_order_id=?", (order["id"],))
        self.assertEqual(o["status"], "awaiting_payment")
        self.assertEqual([s["status"] for s in stock], ["reserved"])

    def test_cancel_order_releases_reserved_stock(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        self.orders.cancel_order(order["id"])
        summary = self.catalog.stock_summary()[0]
        self.assertEqual(summary["available"], 2)
        self.assertEqual(self._one("SELECT status FROM orders WHERE id=?", (order["id"],))["status"], "cancelled")

    def test_sweep_expired_orders_releases_stock(self) -> None:
        expired_orders = OrderService(self.db, order_expires_minutes=-1)
        order = expired_orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        swept = expired_orders.sweep_expired()
        self.assertEqual(swept, 1)
        self.assertEqual(self._one("SELECT status FROM orders WHERE id=?", (order["id"],))["status"], "cancelled")
        self.assertEqual(self.catalog.stock_summary()[0]["available"], 2)

    def test_bank_topup_is_idempotent_and_event_is_confirmed(self) -> None:
        intent = self.payments.create_wallet_topup_intent(
            user_id=self.user_id,
            provider="SePay",
            currency="VND",
            amount_minor=200_000,
        )
        result1 = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-1",
            amount_minor=200_000,
            currency="VND",
            description=f"Nap tien {intent['public_code']}",
            raw={"id": "BANK-TX-1"},
        )
        result2 = self.payments.confirm_provider_transaction(
            provider="SEPAY",
            provider_tx_id="BANK-TX-1",
            amount_minor=200_000,
            currency="VND",
            description=f"Nap tien {intent['public_code']}",
            raw={"id": "BANK-TX-1"},
        )
        self.assertEqual(result1["status"], "wallet_credited")
        self.assertEqual(result2["status"], "duplicate")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 200_000)
        self.assertEqual(self._one("SELECT status FROM external_payment_events WHERE provider_tx_id='BANK-TX-1'")["status"], "wallet_credited")
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM cash_ledger WHERE provider='sepay'")["c"], 1)

    def test_wallet_topup_under_requested_amount_credits_actual_money_received(self) -> None:
        intent = self.payments.create_wallet_topup_intent(
            user_id=self.user_id,
            provider="sepay",
            currency="VND",
            amount_minor=200_000,
        )
        result = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-LOW-TOPUP",
            amount_minor=100_000,
            currency="VND",
            description=f"Nap {intent['public_code']}",
            raw={},
        )
        self.assertEqual(result["status"], "wallet_credited")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 100_000)

    def test_direct_external_order_payment_delivers_without_wallet_credit(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        intent = self.payments.create_order_payment_intent(order_id=order["id"], provider="sepay")
        result = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-ORDER-1",
            amount_minor=150_000,
            currency="VND",
            description=f"Thanh toan {intent['public_code']}",
            raw={"id": "BANK-TX-ORDER-1"},
        )
        self.assertEqual(result["status"], "order_delivered")
        self.assertEqual(result["overpaid_minor"], 0)
        self.assertEqual(len(result["delivery"]), 1)
        self.assertEqual(self.wallet.get_balances(self.user_id), {})
        self.assertEqual(self._one("SELECT status FROM external_payment_events WHERE provider_tx_id='BANK-TX-ORDER-1'")["status"], "order_delivered")

    def test_direct_external_order_overpayment_delivers_and_credits_surplus(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        intent = self.payments.create_order_payment_intent(order_id=order["id"], provider="sepay")
        result = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-OVERPAY",
            amount_minor=170_000,
            currency="VND",
            description=f"Thanh toan {intent['public_code']}",
            raw={},
        )
        self.assertEqual(result["status"], "order_delivered")
        self.assertEqual(result["overpaid_minor"], 20_000)
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 20_000)
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM ledger_entries WHERE reference_type='order_overpayment'")["c"], 1)

    def test_second_different_payment_to_confirmed_intent_is_credited_to_wallet(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        intent = self.payments.create_order_payment_intent(order_id=order["id"], provider="sepay")
        self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-ORDER-FIRST",
            amount_minor=150_000,
            currency="VND",
            description=f"Pay {intent['public_code']}",
            raw={},
        )
        extra = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-ORDER-SECOND",
            amount_minor=150_000,
            currency="VND",
            description=f"Pay again {intent['public_code']}",
            raw={},
        )
        self.assertEqual(extra["status"], "wallet_credited_extra_payment")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 150_000)
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM deliveries WHERE order_id=?", (order["id"],))["c"], 1)

    def test_underpaid_order_payment_credits_wallet_and_does_not_deliver(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        intent = self.payments.create_order_payment_intent(order_id=order["id"], provider="sepay")
        result = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-UNDERPAY-ORDER",
            amount_minor=100_000,
            currency="VND",
            description=f"Pay {intent['public_code']}",
            raw={},
        )
        self.assertEqual(result["status"], "wallet_credited_underpaid_order")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 100_000)
        self.assertEqual(self._one("SELECT status FROM orders WHERE id=?", (order["id"],))["status"], "awaiting_payment")
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM deliveries WHERE order_id=?", (order["id"],))["c"], 0)
        self.wallet.credit(self.user_id, "VND", 50_000, reason="topup rest", idempotency_key="rest-50k")
        paid = self.orders.pay_with_wallet(order["id"])
        self.assertEqual(paid["order"]["status"], "delivered")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 0)

    def test_cancelled_order_external_payment_credits_wallet_and_does_not_deliver(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        intent = self.payments.create_order_payment_intent(order_id=order["id"], provider="sepay")
        self.orders.cancel_order(order["id"])
        result = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-CANCELLED",
            amount_minor=150_000,
            currency="VND",
            description=f"Late {intent['public_code']}",
            raw={},
        )
        self.assertEqual(result["status"], "wallet_credited_late_order")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 150_000)
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM deliveries WHERE order_id=?", (order["id"],))["c"], 0)
        self.assertEqual(self.catalog.stock_summary()[0]["available"], 2)

    def test_expired_order_external_payment_credits_wallet_cancels_order_and_releases_stock(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        intent = self.payments.create_order_payment_intent(order_id=order["id"], provider="sepay")
        with self.db.transaction() as conn:
            conn.execute("UPDATE orders SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (order["id"],))
        result = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-EXPIRED-ORDER",
            amount_minor=150_000,
            currency="VND",
            description=f"Late {intent['public_code']}",
            raw={},
        )
        self.assertEqual(result["status"], "wallet_credited_late_order")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 150_000)
        self.assertEqual(self._one("SELECT status FROM orders WHERE id=?", (order["id"],))["status"], "cancelled")
        self.assertEqual(self.catalog.stock_summary()[0]["available"], 2)

    def test_expired_wallet_topup_intent_still_credits_received_money(self) -> None:
        intent = self.payments.create_wallet_topup_intent(user_id=self.user_id, provider="sepay", currency="VND", amount_minor=50_000)
        with self.db.transaction() as conn:
            conn.execute("UPDATE payment_intents SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (intent["id"],))
        result = self.payments.confirm_provider_transaction(
            provider="sepay",
            provider_tx_id="BANK-TX-EXPIRED-TOPUP",
            amount_minor=50_000,
            currency="VND",
            description=f"Late {intent['public_code']}",
            raw={},
        )
        self.assertEqual(result["status"], "wallet_credited_expired_intent")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 50_000)

    def test_currency_mismatch_payment_is_credited_to_received_currency_wallet(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        intent = self.payments.create_order_payment_intent(order_id=order["id"], provider="binance")
        result = self.payments.confirm_provider_transaction(
            provider="binance",
            provider_tx_id="BINANCE-USDT-FOR-VND-ORDER",
            amount_minor=to_minor("10", "USDT"),
            currency="USDT",
            description=f"USDT {intent['public_code']}",
            raw={},
        )
        self.assertEqual(result["status"], "wallet_credited_currency_mismatch")
        self.assertEqual(self.wallet.get_balances(self.user_id)["USDT"], to_minor("10", "USDT"))
        self.assertEqual(self._one("SELECT status FROM orders WHERE id=?", (order["id"],))["status"], "awaiting_payment")

    def test_unmatched_payment_code_is_persisted_for_admin_audit_and_raises(self) -> None:
        with self.assertRaises(PaymentMatchError):
            self.payments.confirm_provider_transaction(
                provider="sepay",
                provider_tx_id="BANK-TX-UNKNOWN",
                amount_minor=10_000,
                currency="VND",
                description="UNKNOWN NAPDEADBEEF",
                raw={},
            )
        event = self._one("SELECT status FROM external_payment_events WHERE provider_tx_id='BANK-TX-UNKNOWN'")
        self.assertEqual(event["status"], "unmatched")

    def test_missing_payment_code_raises_without_creating_event(self) -> None:
        with self.assertRaises(PaymentMatchError):
            self.payments.confirm_provider_transaction(
                provider="sepay",
                provider_tx_id="BANK-TX-NOCODE",
                amount_minor=10_000,
                currency="VND",
                description="No code here",
                raw={},
            )
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM external_payment_events WHERE provider_tx_id='BANK-TX-NOCODE'")["c"], 0)

    def test_create_payment_intent_for_expired_order_cancels_and_rejects(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        with self.db.transaction() as conn:
            conn.execute("UPDATE orders SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (order["id"],))
        with self.assertRaises(ValueError):
            self.payments.create_order_payment_intent(order_id=order["id"], provider="sepay")
        self.assertEqual(self._one("SELECT status FROM orders WHERE id=?", (order["id"],))["status"], "cancelled")
        self.assertEqual(self.catalog.stock_summary()[0]["available"], 2)

    def test_refund_delivered_order_to_wallet_is_idempotent(self) -> None:
        self.wallet.credit(self.user_id, "VND", 150_000, reason="admin", idempotency_key="refund-seed")
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        self.orders.pay_with_wallet(order["id"])
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 0)
        first = self.orders.refund_to_wallet(order["id"], reason="bad account")
        second = self.orders.refund_to_wallet(order["id"], reason="bad account retry")
        self.assertEqual(first["order"]["status"], "refunded")
        self.assertEqual(second["order"]["status"], "refunded")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 150_000)
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM ledger_entries WHERE reference_type='order_refund'")["c"], 1)
        self.assertEqual(self._one("SELECT COUNT(*) AS c FROM cash_ledger WHERE event_type='refund'")["c"], 1)

    def test_refund_unpaid_order_is_rejected(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        with self.assertRaises(OrderStateError):
            self.orders.refund_to_wallet(order["id"])


    def test_user_cannot_pay_cancel_or_create_payment_intent_for_someone_else_order(self) -> None:
        self.wallet.credit(self.user_id, "VND", 150_000, reason="admin", idempotency_key="owner-seed")
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        with self.assertRaises(OrderOwnershipError):
            self.orders.pay_with_wallet(order["id"], expected_user_id=self.second_user_id)
        with self.assertRaises(PermissionError):
            self.payments.create_order_payment_intent(order_id=order["id"], provider="sepay", expected_user_id=self.second_user_id)
        with self.assertRaises(OrderOwnershipError):
            self.orders.cancel_order(order["id"], expected_user_id=self.second_user_id)
        # Correct owner can still complete the order normally after failed foreign attempts.
        result = self.orders.pay_with_wallet(order["id"], expected_user_id=self.user_id)
        self.assertEqual(result["order"]["status"], "delivered")
        self.assertEqual(self.wallet.get_balances(self.user_id)["VND"], 0)

    def test_delivery_clears_reservation_fields_to_keep_stock_audit_clean(self) -> None:
        self.wallet.credit(self.user_id, "VND", 150_000, reason="admin", idempotency_key="delivery-clean-seed")
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        self.orders.pay_with_wallet(order["id"], expected_user_id=self.user_id)
        row = self._one("SELECT status, reserved_by_user_id, reserved_order_id, reserved_until, sold_order_id FROM stock_items WHERE sold_order_id=?", (order["id"],))
        self.assertEqual(row["status"], "sold")
        self.assertIsNone(row["reserved_by_user_id"])
        self.assertIsNone(row["reserved_order_id"])
        self.assertIsNone(row["reserved_until"])
        self.assertEqual(row["sold_order_id"], order["id"])


    def test_audit_passes_for_clean_database_and_detects_wallet_drift(self) -> None:
        self.wallet.credit(self.user_id, "VND", 150_000, reason="audit-seed", idempotency_key="audit-seed")
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        self.orders.pay_with_wallet(order["id"], expected_user_id=self.user_id)
        self.assertEqual(self.audit.run(), [])
        with self.db.transaction() as conn:
            conn.execute("UPDATE wallet_balances SET balance_minor=999999 WHERE user_id=? AND currency='VND'", (self.user_id,))
        issues = self.audit.run()
        self.assertTrue(any(issue.code == "wallet_ledger_mismatch" for issue in issues))

    def test_audit_detects_reserved_stock_mismatch(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=1)
        self.assertEqual(self.audit.run(), [])
        with self.db.transaction() as conn:
            conn.execute("UPDATE stock_items SET status='available', reserved_order_id=NULL, reserved_by_user_id=NULL, reserved_until=NULL WHERE reserved_order_id=?", (order["id"],))
        issues = self.audit.run()
        self.assertTrue(any(issue.code == "awaiting_order_reserved_stock_mismatch" for issue in issues))

    def test_money_helpers_support_usdt_and_vnd_formatting(self) -> None:
        self.assertEqual(to_minor("1.234567", "USDT"), 1_234_567)
        self.assertEqual(fmt_money(150_000, "VND"), "150.000đ")
        self.assertEqual(fmt_money(1_234_567, "USDT"), "1.234567 USDT")


if __name__ == "__main__":
    unittest.main()
