from __future__ import annotations

MAIN_MENU = [
    ["🛒 Mua ngay", "👤 Hồ sơ"],
    ["📜 Lịch sử mua", "💰 Ví"],
    ["💬 Hỗ trợ", "🌐 Ngôn ngữ"],
]

ADMIN_MENU = [
    ["📦 Đơn chờ duyệt", "💵 Dòng tiền"],
    ["➕ Thêm sản phẩm", "📥 Nhập kho"],
    ["👥 Khách hàng", "📊 Thống kê"],
]

TOPUP_AMOUNTS_VND = [50_000, 100_000, 200_000, 500_000]


def build_reply_keyboard(rows: list[list[str]]):
    try:
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install aiogram to run Telegram UI: pip install -r requirements.txt") from exc
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in rows],
        resize_keyboard=True,
        input_field_placeholder="Chọn chức năng...",
    )


def build_inline_keyboard(rows: list[list[tuple[str, str]]]):
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install aiogram to run Telegram UI: pip install -r requirements.txt") from exc
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows]
    )


def categories_keyboard(categories: list[dict]):
    rows = [[(f"📁 {cat['name']}", f"cat:{cat['id']}")] for cat in categories]
    rows.append([("⬅️ Menu chính", "menu:main")])
    return build_inline_keyboard(rows)


def products_keyboard(products: list[dict], category_id: int | None = None):
    rows = [[(f"{p['name']} — còn {int(p.get('available_stock') or 0)}", f"prod:{p['id']}")] for p in products]
    if category_id is not None:
        rows.append([("⬅️ Danh mục", "buy:categories")])
    rows.append([("⬅️ Menu chính", "menu:main")])
    return build_inline_keyboard(rows)


def product_detail_keyboard(product_id: int, has_stock: bool):
    rows: list[list[tuple[str, str]]] = []
    if has_stock:
        rows.append([("✅ Mua ngay", f"buyprod:{product_id}")])
    rows.append([("⬅️ Danh mục", "buy:categories"), ("🏠 Menu", "menu:main")])
    return build_inline_keyboard(rows)


def order_payment_keyboard(order_id: int):
    return build_inline_keyboard([
        [("💰 Thanh toán bằng ví", f"paywallet:{order_id}")],
        [("🏦 Chuyển khoản ngân hàng", f"paybank:{order_id}")],
        [("🟡 Binance Pay/USDT", f"paybinance:{order_id}")],
        [("❌ Hủy đơn", f"cancel:{order_id}")],
    ])


def wallet_keyboard():
    rows = [[(f"➕ Nạp {amount:,}đ".replace(",", "."), f"topupbank:{amount}")] for amount in TOPUP_AMOUNTS_VND]
    rows.append([("📜 Lịch sử mua", "history"), ("🏠 Menu", "menu:main")])
    return build_inline_keyboard(rows)


def language_keyboard():
    return build_inline_keyboard([
        [("🇻🇳 Tiếng Việt", "lang:vi"), ("🇺🇸 English", "lang:en")],
        [("🏠 Menu", "menu:main")],
    ])


def support_keyboard():
    return build_inline_keyboard([
        [("📦 Lỗi đơn hàng", "support:order"), ("🔐 Lỗi tài khoản/key", "support:key")],
        [("💰 Lỗi thanh toán", "support:payment"), ("👨‍💻 Gặp admin", "support:admin")],
        [("🏠 Menu", "menu:main")],
    ])
