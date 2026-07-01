from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nimo_shop.db import Database
from nimo_shop.main import _send_order_delivery_payload
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.orders import OrderService
from nimo_shop.services.payments import PaymentService
from nimo_shop.services.users import UserService


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.documents: list[tuple[int, object, str | None]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.messages.append((int(chat_id), text))

    async def send_document(self, chat_id: int, document, **kwargs):
        self.documents.append((int(chat_id), document, kwargs.get("caption")))


class PaymentDeliveryNotificationTest(unittest.TestCase):
    def test_webhook_delivery_helper_sends_actual_order_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "shop.db")
            db.init()
            user_id = UserService(db).get_or_create(998877, "buyer", "Buyer")
            with db.transaction() as conn:
                cat_id = int(conn.execute("INSERT INTO categories(name) VALUES('Cat')").lastrowid)
            product_id = CatalogService(db).add_product(
                category_id=cat_id,
                name="Delivered Product",
                description="",
                currency="VND",
                price_minor=10_000,
            )
            CatalogService(db).add_stock(product_id, ["login@example.com|secret-pass"])
            order = OrderService(db, order_expires_minutes=15).create_order(user_id=user_id, product_id=product_id)
            intent = PaymentService(db).create_order_payment_intent(order_id=order["id"], provider="bank", expected_user_id=user_id)
            result = PaymentService(db).confirm_provider_transaction(
                provider="bank",
                provider_tx_id="BANK-DELIVERY-NOTIFY-1",
                amount_minor=10_000,
                currency="VND",
                description=f"pay {intent['public_code']}",
            )
            self.assertEqual(result["status"], "order_delivered")
            bot = FakeBot()
            sent = asyncio.run(_send_order_delivery_payload(bot, db, telegram_id=998877, order_id=order["id"], source="test"))
            self.assertTrue(sent)
            joined = "\n".join(text for _, text in bot.messages)
            self.assertIn("Đã giao hàng", joined)
            self.assertIn("Delivered Product", joined)
            self.assertIn("login@example.com", joined)


if __name__ == "__main__":
    unittest.main()
