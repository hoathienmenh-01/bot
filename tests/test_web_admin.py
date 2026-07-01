from __future__ import annotations

import hashlib
import hmac
import http.cookiejar
import json
import re
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from nimo_shop.db import Database
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.users import UserService
from nimo_shop.services.notifications import NotificationService
from nimo_shop.services.preorders import PreorderService
from nimo_shop.services.wallet import WalletService
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
        self.assertEqual(inserted, 3)
        self.assertEqual(self.web.counts()["available_stock"], 3)
        self.web.update_settings({"STOCK_DUPLICATE_POLICY": "reject"}, admin_id=admin_id)
        with self.assertRaises(ValueError):
            self.web.add_stock(prod_id, "acc1|pass\nacc3|pass", admin_id=admin_id)
        self.web.update_settings({"STOCK_DUPLICATE_POLICY": "allow"}, admin_id=admin_id)

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
        self.assertGreaterEqual(len(NotificationService(self.db).pending()), 1)

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

        self.web.update_settings({"SHOP_NAME": "NIMO TEST", "BANK_ENABLED": "on", "WEB_PORT": "9090", "WEB_ADMIN_USERNAME": "owner2", "WEB_ADMIN_PASSWORD": "NewStrongPass123", "BOT_TOKEN": "123456789:AASecretTokenValueForTest", "DELIVERY_OUTPUT_MODE": "file_only", "DELIVERY_FILE_THRESHOLD": "1"}, admin_id=admin_id, write_env=True)
        env_text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("SHOP_NAME=NIMO TEST", env_text)
        self.assertIn("BANK_ENABLED=true", env_text)
        self.assertIn("BOT_TOKEN=123456789:AASecretTokenValueForTest", env_text)
        self.assertIn("DELIVERY_OUTPUT_MODE=file_only", env_text)
        self.assertIn("DELIVERY_FILE_THRESHOLD=1", env_text)
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
            self.assertIn("Giao hàng cho khách", settings_page)
            self.assertIn("Luôn gửi file TXT cho mọi đơn", settings_page)
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

class WebAdminV21OperationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "shop.db"
        self.db = Database(self.db_path)
        self.web = AdminWebService(self.db, project_root=self.root)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")
        self.admin_id = int(self.web.authenticate("owner", "StrongPass123")["id"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_v21_operations_status_import_export_coupons_roles_reconcile_low_stock(self) -> None:
        self.assertFalse(self.web.system_status()["bot_token_ok"])
        self.assertFalse(self.web.check_bot_token("bad")["ok"])
        self.assertTrue(self.web.check_bot_token("987654321:AAVeryLongTokenValueForFormatCheck_123456")["ok"])

        result = self.web.import_catalog_csv(
            "category,name,price,currency,cost,description,warranty_text,stock\n"
            "AI,Searchable GPT,1000,VND,500,desc,bh,key1;key2\n",
            admin_id=self.admin_id,
        )
        self.assertEqual(result["products"], 1)
        self.assertEqual(result["stock"], 2)
        filename, data = self.web.export_report("products")
        self.assertTrue(filename.endswith(".csv"))
        self.assertIn("Searchable GPT", data.decode("utf-8-sig"))

        coupon_id = self.web.create_coupon({"code": "SALE10", "discount_type": "percent", "discount_value": "10", "max_uses": "5", "is_active": "on"}, admin_id=self.admin_id)
        self.assertTrue(any(c["id"] == coupon_id for c in self.web.list_coupons()))
        self.web.update_coupon(coupon_id, {"code": "SALE20", "discount_type": "percent", "discount_value": "20", "max_uses": "3", "is_active": "on", "currency": "VND"}, admin_id=self.admin_id)
        self.assertEqual(self.web.list_coupons()[0]["code"], "SALE20")

        role_id = self.web.create_admin_account(username="finance1", password="Pass123456", role="finance", admin_id=self.admin_id)
        self.web.update_admin_account(role_id, role="viewer", is_active=True, admin_id=self.admin_id)
        self.assertTrue(any(a["username"] == "finance1" and a["role"] == "viewer" for a in self.web.list_admin_accounts()))

        with self.db.transaction() as conn:
            conn.execute("INSERT INTO external_payment_events(provider, provider_tx_id, payment_code, currency, amount_minor, status, raw_json) VALUES('bank','BADTX','', 'VND', 1000, 'unmatched', '{}')")
        events = self.web.list_reconciliation_events()
        self.assertEqual(events[0]["provider_tx_id"], "BADTX")
        self.web.mark_payment_event_reviewed(events[0]["id"], "checked", admin_id=self.admin_id)
        self.assertEqual(self.web.list_reconciliation_events(status="reviewed")[0]["status"], "reviewed")

        self.web.log_delivery_download(order_id=None, user_id=None, source="test", filename="order.txt")
        self.assertEqual(self.web.list_delivery_downloads()[0]["filename"], "order.txt")
        lows = self.web.low_stock_items(threshold=10)
        self.assertTrue(any(i["name"] == "Searchable GPT" for i in lows))
        queued = self.web.queue_low_stock_notifications(threshold=10, admin_id=self.admin_id)
        self.assertGreaterEqual(queued, 1)

    def test_v21_http_pages_and_exports_are_reachable(self) -> None:
        server = create_server(self.db_path, host="127.0.0.1", port=0, session_secret="test-secret", project_root=self.root, bootstrap_username="owner", bootstrap_password="StrongPass123")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            data = urllib.parse.urlencode({"username": "owner", "password": "StrongPass123"}).encode("utf-8")
            opener.open(urllib.request.Request(base + "/login", data=data, method="POST"))
            for path, marker in [
                ("/status", "Trạng thái hệ thống"),
                ("/imports", "Import sản phẩm"),
                ("/exports", "Xuất báo cáo"),
                ("/reconcile", "Đối soát giao dịch"),
                ("/coupons", "Tạo mã giảm giá"),
                ("/roles", "Thêm admin"),
                ("/deliveries", "Nhật ký tải"),
                ("/low-stock", "Cảnh báo hết hàng"),
            ]:
                page = opener.open(base + path).read().decode("utf-8")
                self.assertIn(marker, page)
            resp = opener.open(base + "/exports/download?kind=orders")
            self.assertEqual(resp.headers.get_content_type(), "text/csv")
            self.web.update_settings({"WEBHOOK_SHARED_SECRET": "secret123"}, admin_id=self.admin_id)
            payload = urllib.parse.urlencode({"tx_id": "WH1", "amount": "1000", "currency": "VND", "description": "khong co ma"}).encode("utf-8")
            sig = hmac.new(b"secret123", payload, hashlib.sha256).hexdigest()
            wh = opener.open(urllib.request.Request(base + "/webhook/sepay", data=payload, headers={"X-NIMO-Signature": sig}, method="POST")).read().decode("utf-8")
            self.assertIn("payment code not found", wh)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

class WebAdminV22StockUploadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "shop.db"
        self.db = Database(self.db_path)
        self.web = AdminWebService(self.db, project_root=self.root)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")
        self.admin_id = int(self.web.authenticate("owner", "StrongPass123")["id"])
        self.cat_id = self.web.create_category("Clone", admin_id=self.admin_id)
        self.prod_id = self.web.create_product({"category_id": str(self.cat_id), "name": "Clone FB", "currency": "VND", "price": "1000", "cost": "500", "description": "", "warranty_text": ""}, admin_id=self.admin_id)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pipe_account_file_is_detected_masked_and_imported(self) -> None:
        raw = "\n".join([
            "100001|pass-a|c_user=100001;xs=abc;|EAATOKEN001",
            "100002|pass-b|c_user=100002;xs=def;|EAATOKEN002",
            "100003|pass-c|c_user=100003;xs=ghi;|EAATOKEN003",
        ])
        parsed = self.web.parse_stock_text(raw, parser_mode="auto")
        self.assertEqual(parsed["detected"], "uid_pass_cookie_token")
        self.assertEqual(parsed["count"], 3)
        self.assertIn("Cookie", parsed["preview"][0])
        self.assertNotIn("EAATOKEN001", parsed["preview"][0])
        result = self.web.add_stock_upload(self.prod_id, filename="accounts.txt", data=raw.encode("utf-8"), parser_mode="auto", admin_id=self.admin_id)
        self.assertEqual(result["inserted"], 3)
        with self.db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM stock_items WHERE product_id=?", (self.prod_id,)).fetchone()[0]
        self.assertEqual(count, 3)


    def test_product_specific_stock_formats_normalize_diverse_account_inputs(self) -> None:
        chat_prod = self.web.create_product({
            "category_id": str(self.cat_id),
            "name": "ChatGPT Account",
            "currency": "VND",
            "price": "1000",
            "cost": "0",
            "description": "",
            "warranty_text": "",
            "stock_format": "email_pass_2fa_pipe",
            "stock_format_labels": "Email|Mật khẩu|2FA",
            "delivery_format": "labeled",
        }, admin_id=self.admin_id)
        slash_prod = self.web.create_product({
            "category_id": str(self.cat_id),
            "name": "Email Pass Slash",
            "currency": "VND",
            "price": "1000",
            "cost": "0",
            "description": "",
            "warranty_text": "",
            "stock_format": "email_pass_slash",
            "stock_format_labels": "Email|Mật khẩu",
            "delivery_format": "labeled",
        }, admin_id=self.admin_id)
        parsed_pipe = self.web.parse_stock_text("user@example.com|ChatPlus@123|YSQIL2FCYEOW6S6Q", parser_mode="email_pass_2fa_pipe")
        self.assertEqual(parsed_pipe["lines"][0], "user@example.com|ChatPlus@123|YSQIL2FCYEOW6S6Q")
        self.assertIn("2FA", self.web.get_product(chat_prod)["stock_format_labels"])

        inserted = self.web.add_stock(slash_prod, "antoniocraig@example.site / 111111", parser_mode="product", admin_id=self.admin_id)
        self.assertEqual(inserted, 1)
        with self.db.connect() as conn:
            content = conn.execute("SELECT content FROM stock_items WHERE product_id=?", (slash_prod,)).fetchone()["content"]
        self.assertEqual(content, "antoniocraig@example.site|111111")

    def test_docx_stock_upload_uses_word_paragraphs_without_extra_dependency(self) -> None:
        import zipfile, io
        docx = io.BytesIO()
        xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:r><w:t>email1@example.com|pass1</w:t></w:r></w:p>
        <w:p><w:r><w:t>email2@example.com|pass2</w:t></w:r></w:p>
        </w:body></w:document>'''
        with zipfile.ZipFile(docx, "w") as zf:
            zf.writestr("word/document.xml", xml)
        result = self.web.add_stock_upload(self.prod_id, filename="stock.docx", data=docx.getvalue(), parser_mode="auto", admin_id=self.admin_id)
        self.assertEqual(result["inserted"], 2)

    def test_http_stock_page_exposes_file_upload_and_pipe_mode(self) -> None:
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
            data = urllib.parse.urlencode({"username": "owner", "password": "StrongPass123"}).encode("utf-8")
            opener.open(urllib.request.Request(base + "/login", data=data, method="POST"))
            page = opener.open(base + f"/stock?product_id={self.prod_id}").read().decode("utf-8")
            self.assertIn('enctype="multipart/form-data"', page)
            self.assertIn('name="stock_file"', page)
            self.assertIn('value="pipe"', page)
            self.assertIn("UID|Pass|Cookie|Token", page)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

class WebAdminV24ProductMediaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "shop.db")
        self.web = AdminWebService(self.db, project_root=self.root)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")
        self.admin = int(self.web.authenticate("owner", "StrongPass123")["id"])
        self.cat = self.web.create_category("Media", admin_id=self.admin)
        self.product = self.web.create_product({
            "category_id": str(self.cat),
            "name": "ChatGPT Plus with image",
            "currency": "VND",
            "price": "150000",
            "cost": "100000",
            "description": "Old description",
            "product_icon": "🤖",
            "product_custom_emoji_id": "5368324170671202286",
            "product_short_description": "Short desc",
            "product_long_description": "Long desc",
            "warranty_text": "24h",
        }, admin_id=self.admin)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_product_image_upload_validation_preview_and_backup_media(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        rel = self.web.save_product_image(self.product, filename="cover.png", data=png, admin_id=self.admin)
        self.assertEqual(rel, f"media/products/product_{self.product}.png")
        product = self.web.get_product(self.product)
        self.assertEqual(product["product_icon"], "🤖")
        self.assertEqual(product["product_custom_emoji_id"], "5368324170671202286")
        self.assertTrue((self.root / product["product_image_path"]).exists())
        self.web.update_product_image_file_id(self.product, "telegram-file-id")
        self.assertEqual(self.web.get_product(self.product)["product_image_file_id"], "telegram-file-id")
        with self.assertRaises(ValueError):
            self.web.save_product_image(self.product, filename="bad.gif", data=b"GIF89a", admin_id=self.admin)
        backup = self.web.create_backup(include_env=False, admin_id=self.admin)
        import zipfile
        with zipfile.ZipFile(backup) as zf:
            self.assertIn(f"media/products/product_{self.product}.png", zf.namelist())


class WebAdminV25CategoryPreorderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "shop.db")
        self.web = AdminWebService(self.db, project_root=self.root)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_category_icons_preorder_settings_and_management(self) -> None:
        admin = self.web.authenticate("owner", "StrongPass123")
        self.assertIsNotNone(admin)
        admin_id = int(admin["id"])
        cat_id = self.web.create_category("ChatGPT", category_icon="🤖", admin_id=admin_id)
        cats = self.web.list_categories()
        self.assertEqual(cats[0]["category_icon"], "🤖")
        self.web.update_category(cat_id, name="ChatGPT AI", category_icon="✨", sort_order=1, is_active=True, admin_id=admin_id)
        self.assertEqual(self.web.list_categories()[0]["category_icon"], "✨")
        prod_id = self.web.create_product({"category_id": str(cat_id), "name": "Plus", "currency": "VND", "price": "100000", "cost": "0"}, admin_id=admin_id)
        uid = UserService(self.db).get_or_create(999, "buyer", "Buyer")
        from nimo_shop.services.preorders import PreorderService
        pr_service = PreorderService(self.db, 15)
        pr = pr_service.create_preorder(user_id=uid, product_id=prod_id, quantity=2)
        WalletService(self.db).credit(uid, "VND", int(pr["deposit_amount_minor"]), reason="preorder", idempotency_key="preorder-credit")
        pr_service.pay_deposit_with_wallet(pr["id"], expected_user_id=uid)
        CatalogService(self.db).add_stock(prod_id, ["pre-a", "pre-b"])
        self.assertEqual(self.web.list_preorders()[0]["id"], pr["id"])
        order = self.web.fulfill_preorder(pr["id"], admin_id=admin_id)
        self.assertEqual(order["status"], "awaiting_payment")
        self.assertEqual(self.web.list_preorders(status="fulfilled")[0]["id"], pr["id"])
        self.web.update_settings({"PREORDER_DEPOSIT_PERCENT": "15"}, admin_id=admin_id, write_env=True)
        self.assertIn("PREORDER_DEPOSIT_PERCENT=15", (self.root / ".env").read_text(encoding="utf-8"))

class BuyerApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "shop.db"
        self.db = Database(self.db_path)
        self.web = AdminWebService(self.db, project_root=self.root)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_buyer_api_lists_products_and_purchases_with_wallet(self) -> None:
        cat = CatalogService(self.db).add_category("ChatGPT")
        prod = CatalogService(self.db).add_product(category_id=cat, name="ChatGPT Plus", description="", currency="VND", price_minor=50_000)
        CatalogService(self.db).add_stock(prod, ["acc-a", "acc-b"])
        users = UserService(self.db)
        user_id = users.get_or_create(999000111, "apiuser", "API User")
        api_key = users.ensure_api_key(user_id)
        from nimo_shop.services.wallet import WalletService
        WalletService(self.db).credit(user_id, "VND", 100_000, reason="test", idempotency_key="api-credit")

        server = create_server(str(self.db_path), host="127.0.0.1", port=0, session_secret="secret", project_root=self.root, bootstrap_username="owner", bootstrap_password="StrongPass123")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/telegram-buyer/products")
            self.assertEqual(cm.exception.code, 401)

            req = urllib.request.Request(base + "/api/telegram-buyer/products", headers={"X-API-Key": api_key})
            data = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(len(data["products"]), 1)
            self.assertEqual(data["products"][0]["available_stock"], 2)

            body = json.dumps({"product_id": prod, "quantity": 2}).encode("utf-8")
            req = urllib.request.Request(base + "/api/telegram-buyer/purchase", data=body, method="POST", headers={"X-API-Key": api_key, "Content-Type": "application/json"})
            purchased = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
            self.assertTrue(purchased["ok"])
            self.assertEqual(purchased["order"]["status"], "delivered")
            self.assertEqual(purchased["delivery"], ["acc-a", "acc-b"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

class WebSecurityAndWebhookRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "shop.db"
        self.db = Database(self.db_path)
        self.web = AdminWebService(self.db, project_root=self.root)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _serve(self):
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
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def test_sepay_and_binance_webhook_urls_map_to_internal_payment_providers(self) -> None:
        cat = self.web.create_category("Pay")
        prod = self.web.create_product({"category_id": str(cat), "name": "Item", "currency": "VND", "price": "100000", "cost": "0", "description": "", "warranty_text": ""})
        self.web.add_stock(prod, "acc-bank\nacc-binance")
        user_id = UserService(self.db).get_or_create(888, "buyer", "Buyer")
        order = OrderService(self.db).create_order(user_id=user_id, product_id=prod, quantity=1)
        bank_intent = PaymentService(self.db).create_order_payment_intent(order_id=order["id"], provider="bank", expected_user_id=user_id)
        owner = self.web.authenticate("owner", "StrongPass123")
        self.web.update_settings({"WEBHOOK_SHARED_SECRET": "secret123"}, admin_id=int(owner["id"]))
        server, thread, base = self._serve()
        try:
            payload = json.dumps({"tx_id": "SEP-TX-1", "amount": "100000", "currency": "VND", "description": bank_intent["public_code"]}).encode("utf-8")
            sig = hmac.new(b"secret123", payload, hashlib.sha256).hexdigest()
            response = urllib.request.urlopen(urllib.request.Request(base + "/webhook/sepay", data=payload, headers={"Content-Type": "application/json", "X-NIMO-Signature": sig}, method="POST"))
            data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(data["status"], "order_delivered")

            order2 = OrderService(self.db).create_order(user_id=user_id, product_id=prod, quantity=1)
            binance_intent = PaymentService(self.db).create_order_payment_intent(order_id=order2["id"], provider="binance_pay", expected_user_id=user_id)
            payload = json.dumps({"tx_id": "BN-TX-1", "amount": "100000", "currency": "VND", "description": binance_intent["public_code"]}).encode("utf-8")
            sig = hmac.new(b"secret123", payload, hashlib.sha256).hexdigest()
            response = urllib.request.urlopen(urllib.request.Request(base + "/webhook/binance", data=payload, headers={"Content-Type": "application/json", "X-NIMO-Signature": sig}, method="POST"))
            data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(data["status"], "order_delivered")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


    def test_webhook_shared_secret_blocks_unsigned_requests_when_configured(self) -> None:
        owner = self.web.authenticate("owner", "StrongPass123")
        self.web.update_settings({"WEBHOOK_SHARED_SECRET": "secret123"}, admin_id=int(owner["id"]))
        cat = self.web.create_category("Pay")
        prod = self.web.create_product({"category_id": str(cat), "name": "Item", "currency": "VND", "price": "100000", "cost": "0", "description": "", "warranty_text": ""})
        self.web.add_stock(prod, "acc-secret")
        user_id = UserService(self.db).get_or_create(889, "buyer", "Buyer")
        order = OrderService(self.db).create_order(user_id=user_id, product_id=prod, quantity=1)
        intent = PaymentService(self.db).create_order_payment_intent(order_id=order["id"], provider="bank", expected_user_id=user_id)
        server, thread, base = self._serve()
        try:
            payload = json.dumps({"tx_id": "SEC-TX-1", "amount": "100000", "currency": "VND", "description": intent["public_code"]}).encode("utf-8")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(urllib.request.Request(base + "/webhook/sepay", data=payload, headers={"Content-Type": "application/json"}, method="POST"))
            self.assertEqual(cm.exception.code, 401)
            sig = hmac.new(b"secret123", payload, hashlib.sha256).hexdigest()
            response = urllib.request.urlopen(urllib.request.Request(base + "/webhook/sepay", data=payload, headers={"Content-Type": "application/json", "X-NIMO-Signature": sig}, method="POST"))
            data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(data["status"], "order_delivered")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_non_owner_admin_roles_cannot_access_or_write_owner_settings(self) -> None:
        owner = self.web.authenticate("owner", "StrongPass123")
        self.web.create_admin_account(username="finance", password="FinancePass123", role="finance", admin_id=int(owner["id"]))
        server, thread, base = self._serve()
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            opener.open(base + "/login").read()
            login = urllib.parse.urlencode({"username": "finance", "password": "FinancePass123"}).encode("utf-8")
            opener.open(urllib.request.Request(base + "/login", data=login, method="POST"))
            with self.assertRaises(urllib.error.HTTPError) as cm:
                opener.open(base + "/settings")
            self.assertEqual(cm.exception.code, 403)

            finance_page = opener.open(base + "/payments").read().decode("utf-8")
            csrf = re.search(r'name="csrf" value="([a-f0-9]+)"', finance_page).group(1)
            forbidden_post = urllib.parse.urlencode({"csrf": csrf, "SHOP_NAME": "BAD"}).encode("utf-8")
            with self.assertRaises(urllib.error.HTTPError) as cm2:
                opener.open(urllib.request.Request(base + "/settings", data=forbidden_post, method="POST"))
            self.assertEqual(cm2.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class V28CommercialHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "shop.db")
        self.web = AdminWebService(self.db, project_root=self.tmp.name)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")
        self.admin = self.web.authenticate("owner", "StrongPass123")
        self.admin_id = int(self.admin["id"])
        self.user_id = UserService(self.db).get_or_create(123456, "buyer", "Buyer")
        self.cat_id = self.web.create_category("ChatGPT", admin_id=self.admin_id)
        self.prod_id = self.web.create_product({"category_id": str(self.cat_id), "name": "Zero", "currency": "VND", "price": "0", "cost": "0", "description": "demo", "warranty_text": "test"}, admin_id=self.admin_id)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_zero_price_order_does_not_debit_wallet_crash(self) -> None:
        self.web.add_stock(self.prod_id, "free-key", admin_id=self.admin_id)
        orders = OrderService(self.db, order_expires_minutes=15)
        order = orders.create_order(user_id=self.user_id, product_id=self.prod_id, quantity=1)
        paid = orders.pay_with_wallet(order["id"], expected_user_id=self.user_id)
        self.assertEqual(paid["order"]["status"], "delivered")
        self.assertEqual(len(paid["delivery"]), 1)

    def test_stock_import_broadcasts_and_creates_preorder_remaining_order(self) -> None:
        # Product has no stock, so buyer places and pays a preorder deposit.
        pre = PreorderService(self.db, deposit_percent=10).create_preorder(user_id=self.user_id, product_id=self.prod_id, quantity=1)
        PreorderService(self.db, deposit_percent=10).pay_deposit_with_wallet(pre["id"], expected_user_id=self.user_id)
        inserted = self.web.add_stock(self.prod_id, "preorder-key", admin_id=self.admin_id)
        self.assertEqual(inserted, 1)
        notes = NotificationService(self.db).pending()
        kinds = {n["kind"] for n in notes}
        self.assertIn("product_update", kinds)
        self.assertIn("preorder_delivered", kinds)
