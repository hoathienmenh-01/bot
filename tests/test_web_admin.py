from __future__ import annotations

import http.cookiejar
import re
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

from nimo_shop.db import Database
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.users import UserService
from nimo_shop.services.notifications import NotificationService
from nimo_shop.web.app import create_server
from nimo_shop.web.security import create_session, csrf_token, hash_password, read_session, verify_password, verify_csrf
from nimo_shop.web.service import AdminWebService


class WebAdminTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "shop.db"
        self.db = Database(self.db_path)
        self.web = AdminWebService(self.db, project_root=self.root)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_password_hash_session_and_csrf_are_enforced(self) -> None:
        encoded = hash_password("StrongPass123")
        self.assertTrue(verify_password("StrongPass123", encoded))
        self.assertFalse(verify_password("wrong-pass", encoded))
        token = create_session("secret", admin_id=1, username="owner", role="owner")
        session = read_session("secret", token)
        self.assertIsNotNone(session)
        self.assertEqual(session.username, "owner")
        csrf = csrf_token("secret", token)
        self.assertTrue(verify_csrf("secret", token, csrf))
        self.assertFalse(verify_csrf("secret", token, "bad"))
        self.assertIsNone(read_session("other-secret", token))

    def test_service_manages_products_stock_settings_wallet_and_payment(self) -> None:
        admin = self.web.authenticate("owner", "StrongPass123")
        self.assertIsNotNone(admin)
        admin_id = int(admin["id"])

        cat_id = self.web.create_category("ChatGPT", admin_id=admin_id)
        prod_id = self.web.create_product(
            {"category_id": str(cat_id), "name": "ChatGPT Plus", "currency": "VND", "price": "150000", "cost": "100000", "description": "30 ngày", "warranty_text": "1 đổi 1"},
            admin_id=admin_id,
        )
        with self.assertRaises(ValueError):
            self.web.add_stock(prod_id, "acc1|pass\nacc1|pass\nacc2|pass", admin_id=admin_id)
        inserted = self.web.add_stock(prod_id, "acc1|pass\nacc2|pass", admin_id=admin_id)
        self.assertEqual(inserted, 2)
        self.assertEqual(self.web.counts()["available_stock"], 2)

        self.web.update_product(
            prod_id,
            {
                "category_id": str(cat_id),
                "name": "ChatGPT Plus Premium",
                "currency": "VND",
                "price": "160000",
                "cost": "100000",
                "description": "30 ngày, đã sửa",
                "warranty_text": "1 đổi 1",
                "is_active": "1",
                "notify_users": "on",
            },
            admin_id=admin_id,
        )
        self.assertEqual(self.web.get_product(prod_id)["name"], "ChatGPT Plus Premium")
        self.assertEqual(len(NotificationService(self.db).pending()), 1)

        temp_prod = self.web.create_product(
            {"category_id": str(cat_id), "name": "Temp Product", "currency": "VND", "price": "1000", "cost": "0", "description": "", "warranty_text": ""},
            admin_id=admin_id,
        )
        self.assertEqual(self.web.delete_product(temp_prod, admin_id=admin_id), "deleted")
        with self.assertRaises(ValueError):
            self.web.get_product(temp_prod)

        user_id = UserService(self.db).get_or_create(111, "buyer", "Buyer")
        self.web.manual_wallet_adjust(user_ref="111", direction="credit", currency="VND", amount="100000", reason="test_credit", admin_id=admin_id)
        wallets = self.web.list_wallets()
        self.assertEqual(wallets[0]["balance_minor"], 100_000)
        self.web.manual_wallet_adjust(user_ref="999777555", direction="credit", currency="VND", amount="50000", reason="new_customer_credit", admin_id=admin_id)
        self.assertTrue(any(w["telegram_id"] == "999777555" and w["balance_minor"] == 50_000 for w in self.web.list_wallets()))

        order = OrderService(self.db).create_order(user_id=user_id, product_id=prod_id)
        intent = PaymentService(self.db).create_order_payment_intent(order_id=order["id"], provider="bank")
        result = self.web.confirm_payment(payment_code=intent["public_code"], tx_id="WEBTX001", amount="160000", currency="VND", provider="bank", admin_id=admin_id)
        self.assertEqual(result["status"], "order_delivered")
        self.assertEqual(OrderService(self.db).get_order(order["id"])["status"], "delivered")

        self.web.update_settings({"SHOP_NAME": "NIMO TEST", "BANK_ENABLED": "on", "WEB_PORT": "9090", "WEB_ADMIN_USERNAME": "owner2", "WEB_ADMIN_PASSWORD": "NewStrongPass123", "BOT_TOKEN": "123456789:AASecretTokenValueForTest"}, admin_id=admin_id, write_env=True)
        env_text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("SHOP_NAME=NIMO TEST", env_text)
        self.assertIn("BANK_ENABLED=true", env_text)
        self.assertIn("BOT_TOKEN=123456789:AASecretTokenValueForTest", env_text)
        self.assertIsNotNone(self.web.authenticate("owner2", "NewStrongPass123"))

        self.web.update_settings({"BOT_TOKEN": "", "WEB_ADMIN_USERNAME": "owner2", "WEB_ADMIN_PASSWORD": ""}, admin_id=admin_id, write_env=True)
        env_text_after_blank_secret = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("BOT_TOKEN=123456789:AASecretTokenValueForTest", env_text_after_blank_secret)
        self.assertIsNotNone(self.web.authenticate("owner2", "NewStrongPass123"))
        results = self.web.search_products("chatgpt")
        self.assertTrue(results)
        bot_id = self.web.create_managed_bot({"name": "Shop Bot", "token": "123456789:AATestManagedBotTokenValue", "bot_type": "shop", "is_primary": "on", "is_enabled": "on", "username": "shop_bot"}, admin_id=admin_id)
        self.assertEqual(self.web.list_managed_bots()[0]["id"], bot_id)
        self.web.update_managed_bot(bot_id, {"name": "Shop Bot 2", "token": "", "bot_type": "shop", "is_primary": "on", "is_enabled": "on", "username": "shop_bot", "notes": "ok"}, admin_id=admin_id)
        self.assertEqual(self.web.list_managed_bots()[0]["name"], "Shop Bot 2")
        self.web.create_notification(title="Sale", message="<b>Sale</b> hôm nay", admin_id=admin_id)
        self.assertTrue(any(n["title"] == "Sale" for n in self.web.list_notifications()))
        backup = self.web.create_backup(include_env=True, admin_id=admin_id)
        self.assertTrue(backup.exists())
        self.assertEqual(self.web.audit(), [])
        self.assertGreaterEqual(len(self.web.audit_logs()), 9)

    def test_http_admin_login_csrf_forms_and_pages(self) -> None:
        server = create_server(
            self.db_path,
            host="127.0.0.1",
            port=0,
            session_secret="test-secret",
            project_root=self.root,
            bootstrap_username="owner",
            bootstrap_password="StrongPass123",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            login_html = opener.open(base + "/login").read().decode("utf-8")
            self.assertIn("NIMO Shop Admin", login_html)
            data = urllib.parse.urlencode({"username": "owner", "password": "StrongPass123"}).encode("utf-8")
            opener.open(urllib.request.Request(base + "/login", data=data, method="POST"))
            dashboard = opener.open(base + "/").read().decode("utf-8")
            self.assertIn("NIMO", dashboard)
            categories_page = opener.open(base + "/categories").read().decode("utf-8")
            csrf = re.search(r'name="csrf" value="([a-f0-9]+)"', categories_page)
            self.assertIsNotNone(csrf)
            token = csrf.group(1)

            post = lambda path, fields: opener.open(urllib.request.Request(base + path, data=urllib.parse.urlencode(fields).encode("utf-8"), method="POST"))
            post("/categories/create", {"csrf": token, "name": "Gemini", "sort_order": "100"})
            cats = opener.open(base + "/categories").read().decode("utf-8")
            self.assertIn("Gemini", cats)
            token = re.search(r'name="csrf" value="([a-f0-9]+)"', cats).group(1)
            post("/products/create", {"csrf": token, "category_id": "1", "name": "Gemini Pro", "currency": "VND", "price": "99000", "cost": "50000", "description": "1 tháng", "warranty_text": "7 ngày"})
            products = opener.open(base + "/products").read().decode("utf-8")
            self.assertIn("Danh sách sản phẩm", products)
            self.assertIn("Thêm sản phẩm", products)
            self.assertIn("Gemini Pro", products)
            self.assertIn(">Sửa<", products)
            self.assertIn(">Xóa<", products)
            token = re.search(r'name="csrf" value="([a-f0-9]+)"', products).group(1)
            edit_page = opener.open(base + "/products?edit=1").read().decode("utf-8")
            self.assertIn("Sửa sản phẩm", edit_page)
            self.assertIn("Gửi thông báo cập nhật sản phẩm", edit_page)
            settings_page = opener.open(base + "/settings").read().decode("utf-8")
            self.assertIn("Hướng dẫn cấu hình", settings_page)
            self.assertIn("Bot Token", settings_page)
            self.assertIn("Mã ngân hàng", settings_page)
            self.assertNotIn("Payment intents", settings_page)
            self.assertIn("Quản lý bot", opener.open(base + "/bots").read().decode("utf-8"))
            self.assertIn("Tạo thông báo bot", opener.open(base + "/notifications").read().decode("utf-8"))
            self.assertIn("Backup/Restore dữ liệu", opener.open(base + "/backup").read().decode("utf-8"))
            self.assertIn("Tạo bot với BotFather", opener.open(base + "/guide").read().decode("utf-8"))
            backup_resp = opener.open(base + "/backup/download?include_env=0")
            self.assertEqual(backup_resp.headers.get_content_type(), "application/zip")
            post("/stock/import", {"csrf": token, "product_id": "1", "contents": "key-a\nkey-b"})
            stock = opener.open(base + "/stock").read().decode("utf-8")
            self.assertIn("key-a", stock)

            wallet_page = opener.open(base + "/wallets").read().decode("utf-8")
            token = re.search(r'name="csrf" value="([a-f0-9]+)"', wallet_page).group(1)
            post("/wallets/adjust", {"csrf": token, "user_ref": "555666777", "direction": "credit", "currency": "VND", "amount": "25000", "reason": "http_test"})
            self.assertTrue(any(w["telegram_id"] == "555666777" and w["balance_minor"] == 25_000 for w in self.web.list_wallets()))

            user_id = UserService(self.db).get_or_create(333444555, "paybuyer", "Pay Buyer")
            order = OrderService(self.db).create_order(user_id=user_id, product_id=1)
            intent = PaymentService(self.db).create_order_payment_intent(order_id=order["id"], provider="bank")
            payments_page = opener.open(base + "/payments").read().decode("utf-8")
            token = re.search(r'name="csrf" value="([a-f0-9]+)"', payments_page).group(1)
            post("/payments/confirm", {"csrf": token, "payment_code": intent["public_code"], "tx_id": "HTTPPAY001", "amount": "99000", "currency": "VND", "provider": "bank"})
            with self.db.connect() as conn:
                event_count = conn.execute("SELECT COUNT(*) AS c FROM external_payment_events WHERE provider_tx_id='HTTPPAY001'").fetchone()["c"]
                delivered = conn.execute("SELECT status FROM orders WHERE id=?", (order["id"],)).fetchone()["status"]
            self.assertEqual(event_count, 1)
            self.assertEqual(delivered, "delivered")

            dark = opener.open(base + "/?theme=dark&lang=en").read().decode("utf-8")
            self.assertIn('data-theme="dark"', dark)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
