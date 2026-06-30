from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nimo_shop.bot.admin_commands import parse_add_product, parse_add_stock, parse_confirm, parse_one_int_arg
from nimo_shop.bot import views
from nimo_shop.db import Database
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.provider_sync import apply_sepay_transactions, normalize_sepay_transaction
from nimo_shop.services.users import UserService
from nimo_shop.services.wallet import WalletService


class BotCompletionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "shop.db")
        self.db.init()
        self.users = UserService(self.db)
        self.catalog = CatalogService(self.db)
        self.orders = OrderService(self.db)
        self.payments = PaymentService(self.db)
        self.user_id = self.users.get_or_create(999, "buyer", "Buyer")
        self.cat_id = self.catalog.add_category("ChatGPT")
        self.product_id = self.catalog.add_product(
            category_id=self.cat_id,
            name="ChatGPT Plus",
            description="Dùng 30 ngày",
            currency="VND",
            price_minor=150_000,
            cost_minor=100_000,
            warranty_text="1 đổi 1",
        )
        self.catalog.add_stock(self.product_id, ["acc|pass"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_admin_command_parsers_accept_documented_formats(self) -> None:
        product = parse_add_product("/addproduct 1 | ChatGPT Plus | 150000 | 100000 | Dùng 30 ngày | 1 đổi 1")
        self.assertEqual(product.category_id, 1)
        self.assertEqual(product.name, "ChatGPT Plus")
        self.assertEqual(product.price_minor, 150_000)
        self.assertEqual(product.cost_minor, 100_000)
        self.assertEqual(product.warranty_text, "1 đổi 1")

        product_id, stock = parse_add_stock("/addstock 7\nkey1\nkey2")
        self.assertEqual(product_id, 7)
        self.assertEqual(stock, ["key1", "key2"])

        payment_code, tx_id, amount_minor, currency, provider = parse_confirm("/confirm ORDABCDEF12 TX123 150000 VND bank")
        self.assertEqual(payment_code, "ORDABCDEF12")
        self.assertEqual(tx_id, "TX123")
        self.assertEqual(amount_minor, 150_000)
        self.assertEqual(currency, "VND")
        self.assertEqual(provider, "bank")
        self.assertEqual(parse_one_int_arg("/cancel 123", "/cancel"), 123)

    def test_views_render_core_customer_and_admin_texts(self) -> None:
        products = self.catalog.list_products(self.cat_id)
        product = products[0]
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id)
        WalletService(self.db).credit(self.user_id, "VND", 150_000, reason="test", idempotency_key="credit-ui")
        paid = self.orders.pay_with_wallet(order["id"])
        profile = self.users.get_profile(999)
        self.assertIn("NIMO SHOP", views.welcome("NIMO SHOP"))
        self.assertIn("ChatGPT Plus", views.product_list(products, "ChatGPT"))
        self.assertIn("Mô tả", views.product_detail(product))
        self.assertIn(order["public_code"], views.order_created(order))
        self.assertIn("Đã giao hàng", views.delivery(paid["order"], paid["delivery"]))
        self.assertIn("Số dư ví", views.profile(profile, 999, "buyer"))
        self.assertIn("Lịch sử mua", views.history(self.orders.order_history(self.user_id)))
        self.assertIn("Quản lý dòng tiền", views.finance(__import__("nimo_shop.services.finance", fromlist=["FinanceService"]).FinanceService(self.db).summary()))

    def test_sepay_normalizer_accepts_common_field_names(self) -> None:
        row = normalize_sepay_transaction({
            "transaction_id": "BANK123",
            "transaction_content": "Thanh toan ORDABCDEF12",
            "amount_in": "150,000",
        })
        self.assertEqual(row["provider_tx_id"], "BANK123")
        self.assertEqual(row["amount_minor"], 150_000)
        self.assertEqual(row["description"], "Thanh toan ORDABCDEF12")

    def test_apply_sepay_transactions_delivers_order_and_is_idempotent(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id)
        intent = self.payments.create_order_payment_intent(order_id=order["id"], provider="bank")
        tx = {
            "id": "BANK999",
            "content": f"Khach chuyen {intent['public_code']}",
            "amount": "150000",
        }
        first = apply_sepay_transactions(self.payments, [tx])
        second = apply_sepay_transactions(self.payments, [tx])
        self.assertEqual(first["applied"], 1)
        self.assertEqual(second["duplicates"], 1)
        delivered = self.orders.get_order(order["id"])
        self.assertEqual(delivered["status"], "delivered")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM deliveries WHERE order_id=?", (order["id"],)).fetchone()["c"], 1)

    def test_apply_sepay_transactions_counts_invalid_rows_without_mutating_money(self) -> None:
        summary = apply_sepay_transactions(self.payments, [{"id": "bad"}])
        self.assertEqual(summary["invalid"], 1)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM external_payment_events").fetchone()["c"], 0)


if __name__ == "__main__":
    unittest.main()
