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
        inserted = self.web.add_stock(prod_id, "acc1|pass\nacc1|pass\nacc2|pass", admin_id=admin_id)
        self.assertEqual(inserted, 2)
        self.assertEqual(self.web.counts()["available_stock"], 2)

        user_id = UserService(self.db).get_or_create(111, "buyer", "Buyer")
        self.web.manual_wallet_adjust(user_id=user_id, direction="credit", currency="VND", amount="100000", reason="test_credit", admin_id=admin_id)
        wallets = self.web.list_wallets()
        self.assertEqual(wallets[0]["balance_minor"], 100_000)

        order = OrderService(self.db).create_order(user_id=user_id, product_id=prod_id)
        intent = PaymentService(self.db).create_order_payment_intent(order_id=order["id"], provider="bank")
        result = self.web.confirm_payment(payment_code=intent["public_code"], tx_id="WEBTX001", amount="150000", currency="VND", provider="bank", admin_id=admin_id)
        self.assertEqual(result["status"], "order_delivered")
        self.assertEqual(OrderService(self.db).get_order(order["id"])["status"], "delivered")

        self.web.update_settings({"SHOP_NAME": "NIMO TEST", "BANK_ENABLED": "on", "WEB_PORT": "9090"}, admin_id=admin_id, write_env=True)
        env_text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("SHOP_NAME=NIMO TEST", env_text)
        self.assertIn("BANK_ENABLED=true", env_text)
        self.assertEqual(self.web.audit(), [])
        self.assertGreaterEqual(len(self.web.audit_logs()), 5)

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
            self.assertIn("Gemini Pro", products)
            token = re.search(r'name="csrf" value="([a-f0-9]+)"', products).group(1)
            post("/stock/import", {"csrf": token, "product_id": "1", "contents": "key-a\nkey-b"})
            stock = opener.open(base + "/stock").read().decode("utf-8")
            self.assertIn("key-a", stock)
            dark = opener.open(base + "/?theme=dark&lang=en").read().decode("utf-8")
            self.assertIn('data-theme="dark"', dark)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
