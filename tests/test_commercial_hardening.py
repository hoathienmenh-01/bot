from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
import zipfile
from pathlib import Path

from nimo_shop.db import Database
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.payments import PaymentMatchError, PaymentService
from nimo_shop.services.preorders import PreorderService
from nimo_shop.services.users import UserService
from nimo_shop.services.wallet import WalletService
from nimo_shop.web.app import create_server
from nimo_shop.web.service import AdminWebService


class CommercialHardeningRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "shop.db"
        self.db = Database(self.db_path)
        self.web = AdminWebService(self.db, project_root=self.root)
        self.web.init(bootstrap_username="owner", bootstrap_password="StrongPass123")
        self.user_id = UserService(self.db).get_or_create(111222333, "buyer", "Buyer")
        self.cat_id = CatalogService(self.db).add_category("AI")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _product_with_stock(self, *, price_minor: int = 100_000, stock: int = 1) -> int:
        product_id = CatalogService(self.db).add_product(
            category_id=self.cat_id,
            name="Commercial Item",
            description="",
            currency="VND",
            price_minor=price_minor,
        )
        if stock > 0:
            CatalogService(self.db).add_stock(product_id, [f"key-{i}" for i in range(stock)])
        return product_id

    def test_unmatched_payment_can_be_reconciled_with_same_provider_tx_id(self) -> None:
        product_id = self._product_with_stock()
        order = OrderService(self.db, order_expires_minutes=15).create_order(user_id=self.user_id, product_id=product_id)
        intent = PaymentService(self.db).create_order_payment_intent(order_id=order["id"], provider="bank")
        payments = PaymentService(self.db)
        with self.assertRaises(PaymentMatchError):
            payments.confirm_provider_transaction(
                provider="bank",
                provider_tx_id="REAL-TX-1",
                amount_minor=100_000,
                currency="VND",
                description="khach chuyen sai noi dung",
            )
        result = payments.confirm_provider_transaction(
            provider="bank",
            provider_tx_id="REAL-TX-1",
            amount_minor=100_000,
            currency="VND",
            description=f"admin reconcile {intent['public_code']}",
            raw={"source": "admin_reconcile"},
        )
        self.assertEqual(result["status"], "order_delivered")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM orders WHERE id=?", (order["id"],)).fetchone()["status"], "delivered")
            self.assertEqual(conn.execute("SELECT status FROM external_payment_events WHERE provider_tx_id='REAL-TX-1'").fetchone()["status"], "order_delivered")

    def test_paid_preorder_cancel_refunds_deposit_to_wallet_once(self) -> None:
        product_id = self._product_with_stock(stock=1)
        WalletService(self.db).credit(self.user_id, "VND", 100_000, reason="fund", idempotency_key="fund-preorder")
        service = PreorderService(self.db, deposit_percent=10)
        preorder = service.create_preorder(user_id=self.user_id, product_id=product_id)
        service.pay_deposit_with_wallet(preorder["id"], expected_user_id=self.user_id)
        result = service.cancel_preorder(preorder["id"], expected_user_id=self.user_id)
        self.assertEqual(result["preorder"]["status"], "refunded")
        self.assertEqual(result["refunded_minor"], 10_000)
        self.assertEqual(WalletService(self.db).get_balances(self.user_id)["VND"], 100_000)

    def test_full_deposit_preorder_is_delivered_when_stock_arrives(self) -> None:
        product_id = CatalogService(self.db).add_product(
            category_id=self.cat_id,
            name="Full Deposit Item",
            description="",
            currency="VND",
            price_minor=50_000,
        )
        WalletService(self.db).credit(self.user_id, "VND", 50_000, reason="fund", idempotency_key="fund-full-preorder")
        service = PreorderService(self.db, deposit_percent=100)
        preorder = service.create_preorder(user_id=self.user_id, product_id=product_id)
        service.pay_deposit_with_wallet(preorder["id"], expected_user_id=self.user_id)
        CatalogService(self.db).add_stock(product_id, ["full-key"])
        created = service.create_payment_orders_for_available_stock(product_id)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["status"], "delivered")
        self.assertEqual(created[0]["total_amount_minor"], 0)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM deliveries").fetchone()["c"], 1)

    def test_restore_backup_rejects_media_path_traversal(self) -> None:
        attack = self.root / "attack.zip"
        with zipfile.ZipFile(attack, "w") as zf:
            zf.writestr("data/shop.db", self.db_path.read_bytes())
            zf.writestr("media/products/../../owned.txt", "bad")
        with self.assertRaises(ValueError):
            self.web.restore_backup(str(attack))
        self.assertFalse((self.root / "owned.txt").exists())

    def test_webhook_without_secret_is_rejected(self) -> None:
        server = create_server(
            self.db_path,
            host="127.0.0.1",
            port=0,
            session_secret="local-test-secret",
            project_root=self.root,
            bootstrap_username="owner",
            bootstrap_password="StrongPass123",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            payload = json.dumps({"tx_id": "NOSECRET", "amount": "1000", "currency": "VND", "description": "ORD12345678"}).encode("utf-8")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(urllib.request.Request(base + "/webhook/sepay", data=payload, headers={"Content-Type": "application/json"}, method="POST"))
            self.assertEqual(cm.exception.code, 401)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_banned_user_cannot_create_order(self) -> None:
        product_id = self._product_with_stock()
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET is_banned=1 WHERE id=?", (self.user_id,))
        with self.assertRaises(PermissionError):
            OrderService(self.db).create_order(user_id=self.user_id, product_id=product_id)

    def test_preorder_remaining_order_expiry_does_not_lose_deposit(self) -> None:
        product_id = self._product_with_stock(stock=0)
        WalletService(self.db).credit(self.user_id, "VND", 100_000, reason="fund", idempotency_key="fund-preorder-expiry")
        service = PreorderService(self.db, deposit_percent=10)
        preorder = service.create_preorder(user_id=self.user_id, product_id=product_id)
        service.pay_deposit_with_wallet(preorder["id"], expected_user_id=self.user_id)
        CatalogService(self.db).add_stock(product_id, ["reserved-for-preorder"])
        created = service.create_payment_orders_for_available_stock(product_id)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["status"], "awaiting_payment")
        with self.db.transaction() as conn:
            conn.execute("UPDATE orders SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (created[0]["id"],))
        self.assertEqual(OrderService(self.db).sweep_expired(), 1)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM preorders WHERE id=?", (preorder["id"],)).fetchone()["status"], "active")
            self.assertEqual(conn.execute("SELECT status FROM orders WHERE id=?", (created[0]["id"],)).fetchone()["status"], "cancelled")
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM stock_items WHERE product_id=? AND status='available'", (product_id,)).fetchone()["c"], 1)
        self.assertEqual(WalletService(self.db).get_balances(self.user_id)["VND"], 90_000)

    def test_preorder_fulfilled_only_after_remaining_payment_is_paid(self) -> None:
        product_id = self._product_with_stock(stock=0)
        WalletService(self.db).credit(self.user_id, "VND", 100_000, reason="fund", idempotency_key="fund-preorder-remaining")
        service = PreorderService(self.db, deposit_percent=10)
        preorder = service.create_preorder(user_id=self.user_id, product_id=product_id)
        service.pay_deposit_with_wallet(preorder["id"], expected_user_id=self.user_id)
        CatalogService(self.db).add_stock(product_id, ["preorder-final-key"])
        created = service.create_payment_orders_for_available_stock(product_id)[0]
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM preorders WHERE id=?", (preorder["id"],)).fetchone()["status"], "active")
        paid = OrderService(self.db).pay_with_wallet(created["id"], expected_user_id=self.user_id)
        self.assertEqual(paid["order"]["status"], "delivered")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM preorders WHERE id=?", (preorder["id"],)).fetchone()["status"], "fulfilled")
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM deliveries WHERE order_id=?", (created["id"],)).fetchone()["c"], 1)
        self.assertEqual(WalletService(self.db).get_balances(self.user_id).get("VND", 0), 0)



if __name__ == "__main__":
    unittest.main()
