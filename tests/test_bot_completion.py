from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from nimo_shop.bot.admin_commands import parse_add_product, parse_add_stock, parse_confirm, parse_one_int_arg
from nimo_shop.bot import views
from nimo_shop.bot.i18n import SUPPORTED_LANGUAGES, menu_rows, menu_texts
from nimo_shop.bot.keyboards import language_keyboard_rows, main_inline_keyboard_rows, product_detail_keyboard_rows, wallet_keyboard_rows
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
        keyboard_text = str(wallet_keyboard_rows())
        self.assertIn("Nạp vào ví", keyboard_text)
        self.assertIn("topup:bank", keyboard_text)
        self.assertNotIn("Nạp ngân hàng 50.000đ", keyboard_text)


    def test_quantity_purchase_ui_and_wallet_balance_are_visible(self) -> None:
        self.catalog.add_stock(self.product_id, ["acc2|pass", "acc3|pass", "acc4|pass", "acc5|pass"] )
        product = self.catalog.list_products(self.cat_id)[0]
        keyboard = str(product_detail_keyboard_rows(self.product_id, int(product["available_stock"])))
        self.assertIn("buyqty", keyboard)
        self.assertIn("buycustom", keyboard)
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=3)
        WalletService(self.db).credit(self.user_id, "VND", 200_000, reason="test", idempotency_key="credit-visible")
        rendered = views.order_created(order, WalletService(self.db).get_balances(self.user_id))
        self.assertIn("Số lượng", rendered)
        self.assertIn("Đơn giá", rendered)
        self.assertIn("Số dư ví hiện tại", rendered)
        self.assertIn("Còn thiếu", rendered)

    def test_popular_language_menu_labels_are_available(self) -> None:
        self.assertTrue({"vi", "en", "zh", "ja", "ko", "th", "es", "fr"}.issubset(SUPPORTED_LANGUAGES))
        self.users.set_language(self.user_id, "en")
        self.assertEqual(self.users.get_language(self.user_id), "en")
        self.assertIn("🛒 Buy now", menu_rows("en")[0])
        self.assertIn("🛒 Mua ngay", menu_texts("buy"))
        keyboard = str(language_keyboard_rows())
        for code in ["vi", "en", "zh", "ja", "ko", "th", "es", "fr"]:
            self.assertIn(f"lang:{code}", keyboard)


    def test_product_search_returns_matching_products(self) -> None:
        results = self.catalog.search_products("chatgpt")
        self.assertTrue(results)
        self.assertEqual(results[0]["id"], self.product_id)
        rendered = views.search_results("chatgpt", results)
        self.assertIn("Kết quả tìm kiếm", rendered)
        self.assertIn("ChatGPT Plus", rendered)
        self.assertIn("#", rendered)

    def test_search_menu_label_is_available(self) -> None:
        self.assertIn("🔎 Tìm sản phẩm", menu_texts("search"))
        self.assertTrue(any("Search" in item for row in menu_rows("en") for item in row))


    def test_large_delivery_is_exported_as_downloadable_text_file(self) -> None:
        bulk_items = [f"bulk-{i}|secret" for i in range(1, 31)]
        self.catalog.add_stock(self.product_id, bulk_items)
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=25)
        WalletService(self.db).credit(self.user_id, "VND", 150_000 * 25, reason="bulk", idempotency_key="bulk-credit")
        paid = self.orders.pay_with_wallet(order["id"])
        self.assertEqual(len(paid["delivery"]), 25)
        self.assertTrue(views.delivery_needs_file(paid["order"], paid["delivery"]))
        summary = views.delivery(paid["order"], paid["delivery"])
        self.assertIn("đã gửi file TXT", summary)
        file_text = views.delivery_file_text(paid["order"], paid["delivery"])
        self.assertIn(paid["order"]["public_code"], file_text)
        self.assertIn("bulk-1|secret", file_text)
        self.assertTrue(views.delivery_filename(paid["order"]).endswith("_delivery.txt"))

    def test_delivery_output_mode_can_force_file_for_one_item(self) -> None:
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id)
        WalletService(self.db).credit(self.user_id, "VND", 150_000, reason="single-file", idempotency_key="single-file-credit")
        paid = self.orders.pay_with_wallet(order["id"])
        self.assertEqual(len(paid["delivery"]), 1)
        with patch.dict(os.environ, {"DELIVERY_OUTPUT_MODE": "auto", "DELIVERY_FILE_THRESHOLD": "20"}, clear=False):
            self.assertFalse(views.delivery_needs_file(paid["order"], paid["delivery"]))
            self.assertIn("Thông tin hàng", views.delivery(paid["order"], paid["delivery"]))
        with patch.dict(os.environ, {"DELIVERY_OUTPUT_MODE": "file_only", "DELIVERY_FILE_THRESHOLD": "20"}, clear=False):
            self.assertTrue(views.delivery_needs_file(paid["order"], paid["delivery"]))
            self.assertIn("đã gửi file TXT", views.delivery(paid["order"], paid["delivery"]))
        with patch.dict(os.environ, {"DELIVERY_OUTPUT_MODE": "inline_and_file", "DELIVERY_FILE_THRESHOLD": "20"}, clear=False):
            self.assertTrue(views.delivery_needs_file(paid["order"], paid["delivery"]))
            rendered = views.delivery(paid["order"], paid["delivery"])
            self.assertIn("Thông tin hàng", rendered)
            self.assertIn("gửi kèm file TXT", rendered)

    def test_delivery_auto_threshold_is_configurable(self) -> None:
        self.catalog.add_stock(self.product_id, ["two|pass"] )
        order = self.orders.create_order(user_id=self.user_id, product_id=self.product_id, quantity=2)
        WalletService(self.db).credit(self.user_id, "VND", 300_000, reason="threshold", idempotency_key="threshold-credit")
        paid = self.orders.pay_with_wallet(order["id"])
        with patch.dict(os.environ, {"DELIVERY_OUTPUT_MODE": "auto", "DELIVERY_FILE_THRESHOLD": "2"}, clear=False):
            self.assertTrue(views.delivery_needs_file(paid["order"], paid["delivery"]))
        with patch.dict(os.environ, {"DELIVERY_OUTPUT_MODE": "auto", "DELIVERY_FILE_THRESHOLD": "999"}, clear=False):
            self.assertFalse(views.delivery_needs_file(paid["order"], paid["delivery"]))

    def test_main_inline_keyboard_supports_single_message_navigation(self) -> None:
        markup = str(main_inline_keyboard_rows("vi"))
        for callback_name in ["buy:categories", "search:menu", "wallet:open", "lang:menu"]:
            self.assertIn(callback_name, markup)

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


    def test_bank_topup_instruction_text_does_not_duplicate_qr_url(self) -> None:
        from nimo_shop.payments.sepay import BankAccount, bank_instruction, vietqr_url
        bank = BankAccount(bank_bin="970436", account_no="0123456789", account_name="PHAM XUAN TOI", bank_name="VCB")
        instruction = bank_instruction(bank, amount_minor=100_000, currency="VND", payment_code="NAPABCDEF12")
        qr = vietqr_url(bank, amount_minor=100_000, currency="VND", add_info="NAPABCDEF12")
        self.assertIn("NAPABCDEF12", instruction)
        self.assertNotIn("img.vietqr.io", instruction)
        self.assertIn("img.vietqr.io", qr)

    def test_apply_sepay_transactions_counts_invalid_rows_without_mutating_money(self) -> None:
        summary = apply_sepay_transactions(self.payments, [{"id": "bad"}])
        self.assertEqual(summary["invalid"], 1)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM external_payment_events").fetchone()["c"], 0)


if __name__ == "__main__":
    unittest.main()

class BotProductMediaViewsTest(unittest.TestCase):
    def test_product_icons_custom_emoji_and_button_labels_render(self) -> None:
        product = {
            "id": 9,
            "name": "ChatGPT Plus",
            "price_minor": 153000,
            "currency": "VND",
            "available_stock": 12,
            "description": "old desc",
            "warranty_text": "24h",
            "product_icon": "🤖",
            "product_custom_emoji_id": "5368324170671202286",
            "product_short_description": "Tài khoản mới",
            "product_long_description": "Mô tả dài",
            "product_image_path": "media/products/product_9.png",
        }
        rendered_list = views.product_list([product], "AI")
        self.assertIn("tg-emoji", rendered_list)
        self.assertIn("📦 12", rendered_list)
        detail = views.product_detail(product)
        self.assertIn("Tóm tắt", detail)
        self.assertIn("Mô tả dài", detail)
        self.assertTrue(views.product_has_image(product))
        from nimo_shop.bot.keyboards import search_results_keyboard_rows
        keyboard_rows = str(search_results_keyboard_rows([product]))
        self.assertIn("🤖 ChatGPT Plus", keyboard_rows)
        self.assertIn("📦 12", keyboard_rows)


class BotCategoryPreorderViewsTest(unittest.TestCase):
    def test_category_and_product_buttons_show_stock_state_and_preorder(self) -> None:
        rows = views.category_list([
            {"name": "ChatGPT", "category_icon": "🤖", "available_stock": 3},
            {"name": "Grok", "category_icon": "◼️", "available_stock": 0},
        ])
        self.assertIn("🟢", rows)
        self.assertIn("🔴", rows)
        keyboard = str(product_detail_keyboard_rows(10, 0))
        self.assertIn("preorderqty:10:1", keyboard)
        self.assertIn("preordercustom:10", keyboard)

    def test_preorder_views_render_deposit_and_wallet_balance(self) -> None:
        preorder = {
            "public_code": "PREABC123",
            "product_name": "ChatGPT Plus",
            "quantity": 2,
            "currency": "VND",
            "unit_amount_minor": 100_000,
            "total_amount_minor": 200_000,
            "deposit_percent": 10,
            "deposit_amount_minor": 20_000,
        }
        text = views.preorder_created(preorder, {"VND": 10_000})
        self.assertIn("Đơn đặt trước", text)
        self.assertIn("10%", text)
        self.assertIn("Còn thiếu", text)
        paid = views.preorder_paid(preorder)
        self.assertIn("Đã nhận đặt trước", paid)

class V26BotUiTest(unittest.TestCase):
    def test_categories_keyboard_is_grid_and_has_refresh(self) -> None:
        from nimo_shop.bot.keyboards import categories_keyboard_rows
        cats = [
            {"id": 1, "name": "ChatGPT", "category_icon": "🤖", "available_stock": 2},
            {"id": 2, "name": "Gemini", "category_icon": "🌈", "available_stock": 0},
            {"id": 3, "name": "Grok", "category_icon": "◼️", "available_stock": 1},
            {"id": 4, "name": "Canva", "category_icon": "🟣", "available_stock": 5},
        ]
        rows = categories_keyboard_rows(cats)
        self.assertEqual(len(rows[0]), 3)
        self.assertIn("refresh:home", str(rows))
        self.assertIn("🟢", str(rows))
        self.assertIn("🔴", str(rows))

    def test_api_link_view_contains_documentation_and_regenerate_button(self) -> None:
        from nimo_shop.bot import views
        from nimo_shop.bot.keyboards import api_link_keyboard_rows
        text = views.api_link("tgb_test", "https://example.test")
        self.assertIn("GET /api/telegram-buyer/products", text)
        self.assertIn("POST /api/telegram-buyer/purchase", text)
        self.assertIn("tgb_test", text)
        self.assertIn("api:regen", str(api_link_keyboard_rows()))
