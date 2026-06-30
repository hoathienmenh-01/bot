from __future__ import annotations

import asyncio
import re
import traceback

from nimo_shop.bot import views
from nimo_shop.bot.i18n import language_display, menu_rows, menu_texts, t
from nimo_shop.bot.admin_commands import parse_add_product, parse_add_stock, parse_confirm, parse_one_int_arg
from nimo_shop.bot.keyboards import (
    ADMIN_MENU,
    MAIN_MENU,
    build_reply_keyboard,
    categories_keyboard,
    language_keyboard,
    order_payment_keyboard,
    product_detail_keyboard,
    products_keyboard,
    support_keyboard,
    search_results_keyboard,
    wallet_keyboard,
)
from nimo_shop.config import Settings
from nimo_shop.db import Database
from nimo_shop.money import fmt_money, from_minor, to_minor
from nimo_shop.payments.binance_pay import BinancePayClient, BinancePayConfig
from nimo_shop.payments.sepay import BankAccount, bank_instruction, vietqr_url
from nimo_shop.services.audit import AuditService
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.finance import FinanceService
from nimo_shop.services.orders import OrderOwnershipError, OrderService, OrderStateError, OutOfStock
from nimo_shop.services.payments import PaymentMatchError, PaymentService
from nimo_shop.services.users import UserService
from nimo_shop.services.wallet import InsufficientFunds, WalletService


def build_dispatcher(settings: Settings, db: Database):
    try:
        from aiogram import Dispatcher, F, Router
        from aiogram.filters import Command, CommandStart
        from aiogram.types import CallbackQuery, Message
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("aiogram is not installed. Run: pip install -r requirements.txt") from exc

    router = Router()
    users = UserService(db)
    catalog = CatalogService(db)
    orders = OrderService(db, order_expires_minutes=settings.order_expires_minutes)
    wallet = WalletService(db)
    payments = PaymentService(db, deposit_expires_minutes=settings.deposit_expires_minutes)
    finance = FinanceService(db)
    audit = AuditService(db)
    pending_quantity_product_by_tg: dict[int, int] = {}
    pending_search_by_tg: set[int] = set()

    def is_admin(telegram_id: int) -> bool:
        return telegram_id in settings.admin_ids

    def ensure_user_from_message(message: Message) -> int:
        user = message.from_user
        return users.get_or_create(user.id, user.username, user.full_name)

    def ensure_user_from_callback(callback: CallbackQuery) -> int:
        user = callback.from_user
        return users.get_or_create(user.id, user.username, user.full_name)

    def get_product(product_id: int) -> dict | None:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, c.name AS category_name,
                       COUNT(CASE WHEN s.status='available' THEN 1 END) AS available_stock
                  FROM products p
                  LEFT JOIN categories c ON c.id=p.category_id
                  LEFT JOIN stock_items s ON s.product_id=p.id
                 WHERE p.id=? AND p.is_active=1
                 GROUP BY p.id
                """,
                (product_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_category_name(category_id: int) -> str | None:
        with db.connect() as conn:
            row = conn.execute("SELECT name FROM categories WHERE id=?", (category_id,)).fetchone()
            return str(row["name"]) if row else None

    def pending_orders() -> list[dict]:
        with db.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT o.*, p.name AS product_name, u.telegram_id
                      FROM orders o
                      JOIN products p ON p.id=o.product_id
                      JOIN users u ON u.id=o.user_id
                     WHERE o.status='awaiting_payment'
                     ORDER BY o.id DESC LIMIT 30
                    """
                )
            ]

    def user_stats() -> str:
        with db.connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            banned = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_banned=1").fetchone()["c"]
            with_balance = conn.execute("SELECT COUNT(DISTINCT user_id) AS c FROM wallet_balances WHERE balance_minor>0").fetchone()["c"]
        return f"👥 <b>Khách hàng</b>\n\nTổng user: <b>{total}</b>\nBị chặn: <b>{banned}</b>\nCó số dư ví: <b>{with_balance}</b>"

    async def send_main_menu(message: Message, user_id: int | None = None) -> None:
        lang = users.get_language(user_id) if user_id else "vi"
        balances = wallet.get_balances(user_id) if user_id else {}
        await message.answer(
            views.welcome(settings.shop_name, balances, lang),
            reply_markup=build_reply_keyboard(menu_rows(lang), placeholder=t(lang, "input_placeholder")),
            parse_mode="HTML",
        )

    async def send_categories(message: Message) -> None:
        cats = catalog.list_categories()
        await message.answer(views.category_list(cats), reply_markup=categories_keyboard(cats), parse_mode="HTML")

    async def send_search_results(message: Message, query: str) -> None:
        products = catalog.search_products(query)
        await message.answer(
            views.search_results(query, products),
            reply_markup=search_results_keyboard(products),
            parse_mode="HTML",
        )

    async def send_wallet(message: Message, user_id: int) -> None:
        await message.answer(views.wallet(wallet.get_balances(user_id)), reply_markup=wallet_keyboard(), parse_mode="HTML")

    async def send_topup_intent(message: Message, user_id: int, amount_minor: int) -> None:
        if amount_minor <= 0:
            await message.answer("❌ Số tiền nạp phải lớn hơn 0.")
            return
        intent = payments.create_wallet_topup_intent(user_id=user_id, provider="bank", currency="VND", amount_minor=amount_minor)
        extra, _qr = payment_extra_for_bank(intent)
        await message.answer(
            views.payment_instruction(intent, provider_label="ngân hàng", extra=extra),
            parse_mode="HTML",
        )
    async def create_order_and_show_payment(message: Message, user_id: int, product_id: int, quantity: int) -> None:
        if quantity <= 0:
            await message.answer("❌ Số lượng phải lớn hơn 0.")
            return
        product = get_product(product_id)
        if not product:
            await message.answer("❌ Sản phẩm không tồn tại hoặc đã tắt bán.")
            return
        available = int(product.get("available_stock") or 0)
        if quantity > available:
            await message.answer(
                f"❌ Không đủ hàng. Sản phẩm hiện chỉ còn <b>{available}</b>, bạn đang muốn mua <b>{quantity}</b>.",
                parse_mode="HTML",
            )
            return
        try:
            order = orders.create_order(user_id=user_id, product_id=product_id, quantity=quantity)
            balances = wallet.get_balances(user_id)
            await message.answer(views.order_created(order, balances), reply_markup=order_payment_keyboard(order["id"]), parse_mode="HTML")
        except OutOfStock:
            await message.answer("❌ Sản phẩm vừa hết hàng. Vui lòng chọn sản phẩm khác.")
        except Exception as exc:
            await message.answer(f"❌ Không tạo được đơn: <code>{views.h(exc)}</code>", parse_mode="HTML")


    def payment_extra_for_bank(intent: dict) -> tuple[str, str | None]:
        if settings.bank_bin and settings.bank_account and settings.bank_owner:
            bank = BankAccount(
                bank_bin=settings.bank_bin,
                account_no=settings.bank_account,
                account_name=settings.bank_owner,
                bank_name=settings.bank_name,
            )
            instruction = bank_instruction(
                bank,
                amount_minor=int(intent["amount_minor"]),
                currency=intent["currency"],
                payment_code=intent["public_code"],
            )
            qr = vietqr_url(
                bank,
                amount_minor=int(intent["amount_minor"]),
                currency=intent["currency"],
                add_info=intent["public_code"],
            )
            return f"<pre>{views.h(instruction)}</pre>\n\nQR VietQR: {views.h(qr)}", qr
        return "Admin chưa cấu hình BANK_BIN/BANK_ACCOUNT/BANK_OWNER. Hãy chuyển khoản theo hướng dẫn admin và ghi đúng mã thanh toán.", None

    @router.message(CommandStart())
    async def start(message: Message):
        user_id = ensure_user_from_message(message)
        await send_main_menu(message, user_id)

    @router.message(Command("menu"))
    async def menu(message: Message):
        user_id = ensure_user_from_message(message)
        await send_main_menu(message, user_id)

    @router.message(Command("admin"))
    async def admin(message: Message):
        if not is_admin(message.from_user.id):
            await message.answer("Bạn không có quyền admin.")
            return
        await message.answer(
            views.admin_help(),
            reply_markup=build_reply_keyboard(ADMIN_MENU),
            parse_mode="HTML",
        )

    @router.message(F.text.in_(menu_texts("buy")))
    async def buy_now(message: Message):
        ensure_user_from_message(message)
        await send_categories(message)

    @router.message(F.text.in_(menu_texts("search")))
    async def search_menu(message: Message):
        ensure_user_from_message(message)
        pending_search_by_tg.add(int(message.from_user.id))
        await message.answer(views.search_prompt(), parse_mode="HTML")

    @router.message(Command("search", "timkiem", "find"))
    async def search_command(message: Message):
        ensure_user_from_message(message)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            pending_search_by_tg.add(int(message.from_user.id))
            await message.answer(views.search_prompt(), parse_mode="HTML")
            return
        await send_search_results(message, parts[1].strip())

    @router.message(F.text.in_(menu_texts("profile")))
    async def profile(message: Message):
        ensure_user_from_message(message)
        prof = users.get_profile(message.from_user.id)
        await message.answer(views.profile(prof or {}, message.from_user.id, message.from_user.username), reply_markup=wallet_keyboard(), parse_mode="HTML")

    @router.message(F.text.in_(menu_texts("history")))
    async def history_msg(message: Message):
        user_id = ensure_user_from_message(message)
        await message.answer(views.history(orders.order_history(user_id)), parse_mode="HTML")

    @router.message(F.text.in_(menu_texts("wallet")))
    async def wallet_msg(message: Message):
        user_id = ensure_user_from_message(message)
        await send_wallet(message, user_id)

    @router.message(Command("nap"))
    async def topup_custom_command(message: Message):
        user_id = ensure_user_from_message(message)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "✍️ Nhập số tiền muốn nạp theo mẫu:\n"
                "<code>/nap 150000</code>\n\n"
                "Ví dụ: <code>/nap 75000</code>, <code>/nap 250000</code>",
                parse_mode="HTML",
            )
            return
        try:
            amount_minor = to_minor(parts[1], "VND")
            await send_topup_intent(message, user_id, amount_minor)
        except Exception as exc:
            await message.answer(f"❌ Số tiền nạp không hợp lệ: <code>{views.h(exc)}</code>", parse_mode="HTML")

    @router.message(Command("mua", "buy"))
    async def buy_quantity_command(message: Message):
        user_id = ensure_user_from_message(message)
        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer(
                "🛒 Nhập đúng mẫu: <code>/mua PRODUCT_ID SO_LUONG</code>\n"
                "Ví dụ: <code>/mua 1 3</code>",
                parse_mode="HTML",
            )
            return
        try:
            await create_order_and_show_payment(message, user_id, int(parts[1]), int(parts[2]))
        except Exception as exc:
            await message.answer(f"❌ Không tạo được đơn: <code>{views.h(exc)}</code>", parse_mode="HTML")

    @router.message(F.text.regexp(r"^\d{1,6}$"))
    async def custom_quantity_number(message: Message):
        user_id = ensure_user_from_message(message)
        telegram_id = int(message.from_user.id)
        product_id = pending_quantity_product_by_tg.pop(telegram_id, None)
        if product_id is None:
            if telegram_id in pending_search_by_tg:
                pending_search_by_tg.discard(telegram_id)
                await send_search_results(message, (message.text or "").strip())
            return
        try:
            await create_order_and_show_payment(message, user_id, product_id, int(message.text or "0"))
        except Exception as exc:
            await message.answer(f"❌ Không tạo được đơn: <code>{views.h(exc)}</code>", parse_mode="HTML")

    @router.message(F.text.in_(menu_texts("support")))
    async def support_msg(message: Message):
        ensure_user_from_message(message)
        await message.answer(views.support(settings.support_contact), reply_markup=support_keyboard(), parse_mode="HTML")

    @router.message(F.text.in_(menu_texts("language")))
    async def language_msg(message: Message):
        ensure_user_from_message(message)
        await message.answer(views.language(), reply_markup=language_keyboard(), parse_mode="HTML")

    @router.message(F.text == "📦 Đơn chờ duyệt")
    async def admin_pending_msg(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(views.pending_orders(pending_orders()), parse_mode="HTML")

    @router.message(F.text == "💵 Dòng tiền")
    async def money_report(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(views.finance(finance.summary()), parse_mode="HTML")

    @router.message(F.text == "➕ Thêm sản phẩm")
    async def add_product_help(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(
                "➕ Thêm sản phẩm bằng lệnh:\n"
                "<code>/addproduct category_id | tên | giá_vnd | giá_vốn_vnd | mô tả | bảo hành</code>\n\n"
                "Ví dụ:\n"
                "<code>/addproduct 1 | ChatGPT Plus 1 tháng | 150000 | 100000 | Tài khoản dùng 30 ngày | 1 đổi 1</code>",
                parse_mode="HTML",
            )

    @router.message(F.text == "📥 Nhập kho")
    async def add_stock_help(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(
                "📥 Nhập kho bằng lệnh:\n"
                "<code>/addstock product_id\naccount1|pass1\naccount2|pass2</code>",
                parse_mode="HTML",
            )

    @router.message(F.text == "👥 Khách hàng")
    async def users_msg(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(user_stats(), parse_mode="HTML")

    @router.message(F.text == "📊 Thống kê")
    async def stats_msg(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(views.stock_summary(catalog.stock_summary()), parse_mode="HTML")


    @router.message(Command("orders"))
    async def orders_cmd(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(views.pending_orders(pending_orders()), parse_mode="HTML")

    @router.message(Command("finance"))
    async def finance_cmd(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(views.finance(finance.summary()), parse_mode="HTML")

    @router.message(Command("stock"))
    async def stock_cmd(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(views.stock_summary(catalog.stock_summary()), parse_mode="HTML")

    @router.message(Command("users"))
    async def users_cmd(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(user_stats(), parse_mode="HTML")

    @router.message(Command("newcategory"))
    async def new_category_cmd(message: Message):
        if not is_admin(message.from_user.id):
            return
        name = message.text.split(maxsplit=1)[1].strip() if len(message.text.split(maxsplit=1)) > 1 else ""
        if not name:
            await message.answer("Sai cú pháp. Dùng: <code>/newcategory Tên danh mục</code>", parse_mode="HTML")
            return
        cat_id = catalog.add_category(name)
        await message.answer(f"✅ Đã thêm danh mục #{cat_id}: <b>{views.h(name)}</b>", parse_mode="HTML")

    @router.message(Command("addproduct"))
    async def add_product_cmd(message: Message):
        if not is_admin(message.from_user.id):
            return
        try:
            cmd = parse_add_product(message.text or "")
            product_id = catalog.add_product(
                category_id=cmd.category_id,
                name=cmd.name,
                description=cmd.description,
                currency="VND",
                price_minor=cmd.price_minor,
                cost_minor=cmd.cost_minor,
                warranty_text=cmd.warranty_text,
            )
        except Exception as exc:
            await message.answer(f"❌ Không thêm được sản phẩm: <code>{views.h(exc)}</code>", parse_mode="HTML")
            return
        await message.answer(f"✅ Đã thêm sản phẩm #{product_id}: <b>{views.h(cmd.name)}</b>", parse_mode="HTML")

    @router.message(Command("addstock"))
    async def add_stock_cmd(message: Message):
        if not is_admin(message.from_user.id):
            return
        try:
            product_id, items = parse_add_stock(message.text or "")
            inserted = catalog.add_stock(product_id, items)
        except Exception as exc:
            await message.answer(f"❌ Không nhập được kho: <code>{views.h(exc)}</code>", parse_mode="HTML")
            return
        await message.answer(f"✅ Đã nhập <b>{inserted}</b> dòng hàng cho sản phẩm #{product_id}.", parse_mode="HTML")

    @router.message(Command("confirm"))
    async def confirm_cmd(message: Message):
        if not is_admin(message.from_user.id):
            return
        try:
            payment_code, tx_id, amount_minor, currency, provider = parse_confirm(message.text or "")
            result = payments.confirm_provider_transaction(
                provider=provider,
                provider_tx_id=tx_id,
                amount_minor=amount_minor,
                currency=currency,
                description=f"Admin manual confirm {payment_code}",
                raw={"source": "admin_command", "admin_id": message.from_user.id},
            )
        except Exception as exc:
            await message.answer(f"❌ Không xác nhận được: <code>{views.h(exc)}</code>", parse_mode="HTML")
            return
        await message.answer(f"✅ Đã xử lý thanh toán. Trạng thái: <b>{views.h(result['status'])}</b>", parse_mode="HTML")

    @router.message(Command("cancel"))
    async def cancel_cmd(message: Message):
        if not is_admin(message.from_user.id):
            return
        try:
            order_id = parse_one_int_arg(message.text or "", "/cancel")
            orders.cancel_order(order_id, "admin_cancel")
        except Exception as exc:
            await message.answer(f"❌ Không hủy được đơn: <code>{views.h(exc)}</code>", parse_mode="HTML")
            return
        await message.answer(f"✅ Đã hủy đơn #{order_id} và trả hàng giữ tạm về kho.")

    @router.message(Command("refund"))
    async def refund_cmd(message: Message):
        if not is_admin(message.from_user.id):
            return
        try:
            order_id = parse_one_int_arg(message.text or "", "/refund")
            result = orders.refund_to_wallet(order_id)
        except Exception as exc:
            await message.answer(f"❌ Không hoàn tiền được: <code>{views.h(exc)}</code>", parse_mode="HTML")
            return
        await message.answer(
            f"✅ Đã hoàn tiền đơn #{order_id}. Số dư mới: "
            f"<b>{fmt_money(int(result['balance_after_minor']), result['order']['currency'])}</b>",
            parse_mode="HTML",
        )

    @router.message(Command("sweep"))
    async def sweep_cmd(message: Message):
        if not is_admin(message.from_user.id):
            return
        count = orders.sweep_expired()
        expired_intents = payments.expire_pending_intents()
        await message.answer(f"✅ Đã quét hết hạn: {count} đơn, {expired_intents} payment intent.")

    @router.message(Command("audit"))
    async def audit_cmd(message: Message):
        if not is_admin(message.from_user.id):
            return
        issues = audit.run()
        if not issues:
            await message.answer("✅ Audit OK: ví/kho/đơn/ledger không phát hiện lệch dữ liệu.")
            return
        lines = [f"❌ Audit phát hiện {len(issues)} lỗi dữ liệu:", ""]
        for issue in issues[:20]:
            lines.append(f"- <b>{views.h(issue.code)}</b>: <code>{views.h(issue.message)}</code>")
        if len(issues) > 20:
            lines.append(f"... còn {len(issues) - 20} lỗi khác")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @router.callback_query(F.data == "menu:main")
    async def cb_main(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        await send_main_menu(callback.message, user_id)
        await callback.answer()

    @router.callback_query(F.data == "buy:categories")
    async def cb_categories(callback: CallbackQuery):
        ensure_user_from_callback(callback)
        cats = catalog.list_categories()
        await callback.message.answer(views.category_list(cats), reply_markup=categories_keyboard(cats), parse_mode="HTML")
        await callback.answer()

    @router.callback_query(F.data.startswith("cat:"))
    async def cb_category(callback: CallbackQuery):
        ensure_user_from_callback(callback)
        category_id = int(callback.data.split(":", 1)[1])
        products = catalog.list_products(category_id)
        await callback.message.answer(
            views.product_list(products, get_category_name(category_id)),
            reply_markup=products_keyboard(products, category_id),
            parse_mode="HTML",
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("prod:"))
    async def cb_product(callback: CallbackQuery):
        ensure_user_from_callback(callback)
        product_id = int(callback.data.split(":", 1)[1])
        product = get_product(product_id)
        if not product:
            await callback.message.answer("❌ Sản phẩm không tồn tại hoặc đã tắt bán.")
        else:
            await callback.message.answer(
                views.product_detail(product),
                reply_markup=product_detail_keyboard(product_id, int(product.get("available_stock") or 0)),
                parse_mode="HTML",
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("buyprod:"))
    async def cb_buy_product(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        product_id = int(callback.data.split(":", 1)[1])
        await create_order_and_show_payment(callback.message, user_id, product_id, 1)
        await callback.answer()

    @router.callback_query(F.data.startswith("buyqty:"))
    async def cb_buy_quantity(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        _, product_id_s, quantity_s = callback.data.split(":", 2)
        await create_order_and_show_payment(callback.message, user_id, int(product_id_s), int(quantity_s))
        await callback.answer()

    @router.callback_query(F.data.startswith("buycustom:"))
    async def cb_buy_custom_quantity(callback: CallbackQuery):
        ensure_user_from_callback(callback)
        product_id = int(callback.data.split(":", 1)[1])
        pending_quantity_product_by_tg[int(callback.from_user.id)] = product_id
        product = get_product(product_id)
        available = int((product or {}).get("available_stock") or 0)
        await callback.message.answer(
            f"✍️ Nhập số lượng muốn mua cho <b>{views.h((product or {}).get('name') or product_id)}</b>.\n"
            f"Tồn kho hiện tại: <b>{available}</b>.\n\n"
            f"Bạn có thể nhắn một số, ví dụ: <code>3</code>\n"
            f"Hoặc dùng lệnh: <code>/mua {product_id} 3</code>",
            parse_mode="HTML",
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("paywallet:"))
    async def cb_pay_wallet(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        order_id = int(callback.data.split(":", 1)[1])
        try:
            result = orders.pay_with_wallet(order_id, expected_user_id=user_id)
            await callback.message.answer(views.delivery(result["order"], result["delivery"]), parse_mode="HTML")
        except InsufficientFunds:
            await callback.message.answer("❌ Số dư ví không đủ. Hãy vào 💰 Ví để nạp thêm hoặc chọn chuyển khoản ngân hàng.")
        except (OrderStateError, OrderOwnershipError) as exc:
            await callback.message.answer(f"❌ Không thanh toán được: <code>{views.h(exc)}</code>", parse_mode="HTML")
        await callback.answer()

    @router.callback_query(F.data.startswith("paybank:"))
    async def cb_pay_bank(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        order_id = int(callback.data.split(":", 1)[1])
        try:
            intent = payments.create_order_payment_intent(order_id=order_id, provider="bank", expected_user_id=user_id)
            extra, _qr = payment_extra_for_bank(intent)
            await callback.message.answer(
                views.payment_instruction(intent, provider_label="ngân hàng", extra=extra),
                parse_mode="HTML",
            )
        except Exception as exc:
            await callback.message.answer(f"❌ Không tạo được thanh toán: <code>{views.h(exc)}</code>", parse_mode="HTML")
        await callback.answer()

    @router.callback_query(F.data.startswith("paybinance:"))
    async def cb_pay_binance(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        order_id = int(callback.data.split(":", 1)[1])
        try:
            intent = payments.create_order_payment_intent(order_id=order_id, provider="binance_pay", expected_user_id=user_id)
            order = orders.get_order(order_id)
            extra = (
                "Chuyển Binance Pay/USDT và ghi đúng mã thanh toán trong note/nội dung. "
                "Admin có thể xác nhận thủ công bằng /confirm nếu chưa cấu hình merchant API."
            )
            if settings.binance_pay_enabled and settings.binance_pay_api_key and settings.binance_pay_secret_key:
                client = BinancePayClient(BinancePayConfig(
                    api_key=settings.binance_pay_api_key,
                    secret_key=settings.binance_pay_secret_key,
                    base_url=settings.binance_pay_base_url,
                ))
                amount = format(from_minor(int(intent["amount_minor"]), intent["currency"]), "f")
                payload = client.create_order_payload(
                    merchant_trade_no=intent["public_code"],
                    product_name=order["product_name"],
                    amount=amount,
                    currency=intent["currency"],
                    return_url=settings.binance_pay_return_url or None,
                    webhook_url=settings.binance_pay_webhook_url or None,
                )
                response = await asyncio.to_thread(client.create_order, payload)
                data = response.get("data") or {}
                provider_ref = str(data.get("prepayId") or data.get("universalUrl") or data.get("checkoutUrl") or intent["public_code"])
                payments.attach_provider_reference(intent_id=int(intent["id"]), provider_ref=provider_ref, metadata={"binance_create_order_response": response})
                pay_link = data.get("universalUrl") or data.get("checkoutUrl") or data.get("qrcodeLink") or data.get("deeplink")
                extra = (
                    "✅ Đã tạo Binance Pay merchant order.\n"
                    f"Provider ref: <code>{views.h(provider_ref)}</code>\n"
                    + (f"Link/QR thanh toán: {views.h(pay_link)}\n" if pay_link else "")
                    + "Khi webhook hoặc admin xác nhận giao dịch, bot sẽ tự giao hàng/cộng ví."
                )
            await callback.message.answer(
                views.payment_instruction(intent, provider_label="Binance Pay/USDT", extra=extra),
                parse_mode="HTML",
            )
        except Exception as exc:
            await callback.message.answer(f"❌ Không tạo được thanh toán: <code>{views.h(exc)}</code>", parse_mode="HTML")
        await callback.answer()

    @router.callback_query(F.data.startswith("cancel:"))
    async def cb_cancel(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        order_id = int(callback.data.split(":", 1)[1])
        try:
            orders.cancel_order(order_id, "user_cancel", expected_user_id=user_id)
            await callback.message.answer("✅ Đã hủy đơn và trả hàng giữ tạm về kho.")
        except OrderOwnershipError:
            await callback.message.answer("❌ Bạn không có quyền hủy đơn này.")
        await callback.answer()

    @router.callback_query(F.data == "wallet:open")
    async def cb_wallet_open(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        await send_wallet(callback.message, user_id)
        await callback.answer()

    @router.callback_query(F.data == "topupcustom")
    async def cb_topup_custom(callback: CallbackQuery):
        ensure_user_from_callback(callback)
        await callback.message.answer(
            "✍️ Bạn muốn nạp bao nhiêu?\n\n"
            "Gửi lệnh theo mẫu: <code>/nap 150000</code>\n"
            "Có thể nhập số tiền tự do, không bị giới hạn bởi các mức có sẵn.",
            parse_mode="HTML",
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("topupbank:"))
    async def cb_topup_bank(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        amount_minor = int(callback.data.split(":", 1)[1])
        try:
            await send_topup_intent(callback.message, user_id, amount_minor)
        except Exception as exc:
            await callback.message.answer(f"❌ Không tạo được lệnh nạp: <code>{views.h(exc)}</code>", parse_mode="HTML")
        await callback.answer()

    @router.callback_query(F.data == "history")
    async def cb_history(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        await callback.message.answer(views.history(orders.order_history(user_id)), parse_mode="HTML")
        await callback.answer()

    @router.callback_query(F.data.startswith("lang:"))
    async def cb_language(callback: CallbackQuery):
        user_id = ensure_user_from_callback(callback)
        lang = callback.data.split(":", 1)[1]
        try:
            users.set_language(user_id, lang)
            await callback.message.answer(t(lang, "language_updated").format(language=language_display(lang)), parse_mode="HTML")
            await send_main_menu(callback.message, user_id)
        except Exception as exc:
            await callback.message.answer(f"❌ Không đổi được ngôn ngữ: <code>{views.h(exc)}</code>", parse_mode="HTML")
        await callback.answer()

    @router.callback_query(F.data.startswith("support:"))
    async def cb_support(callback: CallbackQuery):
        ensure_user_from_callback(callback)
        topic = callback.data.split(":", 1)[1]
        await callback.message.answer(
            f"💬 Bạn đã chọn hỗ trợ: <b>{views.h(topic)}</b>\n\n"
            "Hãy gửi tin nhắn kèm mã đơn. Admin sẽ kiểm tra và phản hồi.",
            parse_mode="HTML",
        )
        await callback.answer()

    @router.message(F.text)
    async def pending_search_text(message: Message):
        ensure_user_from_message(message)
        telegram_id = int(message.from_user.id)
        text = (message.text or "").strip()
        if telegram_id not in pending_search_by_tg or text.startswith("/"):
            return
        pending_search_by_tg.discard(telegram_id)
        await send_search_results(message, text)

    @router.errors()
    async def error_handler(event):  # pragma: no cover - runtime safety net
        print("Unhandled bot error:", event)
        traceback.print_exc()
        return True

    dp = Dispatcher()
    dp.include_router(router)
    return dp
