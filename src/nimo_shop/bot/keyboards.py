from __future__ import annotations

from nimo_shop.bot.i18n import SUPPORTED_LANGUAGES, language_display

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


def build_reply_keyboard(rows: list[list[str]], *, placeholder: str = "Chọn chức năng..."):
    try:
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install aiogram to run Telegram UI: pip install -r requirements.txt") from exc
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in rows],
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


def build_inline_keyboard(rows: list[list[tuple[str, str]]]):
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install aiogram to run Telegram UI: pip install -r requirements.txt") from exc
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows]
    )


def main_inline_keyboard_rows(lang: str = "vi") -> list[list[tuple[str, str]]]:
    from nimo_shop.bot.i18n import t

    return [
        [(t(lang, "buy"), "buy:categories"), (t(lang, "search"), "search:menu")],
        [(t(lang, "profile"), "nav:profile"), (t(lang, "history"), "history")],
        [(t(lang, "wallet"), "wallet:open"), (t(lang, "support"), "support:main")],
        [(t(lang, "language"), "lang:menu")],
    ]


def main_inline_keyboard(lang: str = "vi"):
    return build_inline_keyboard(main_inline_keyboard_rows(lang))


def categories_keyboard(categories: list[dict]):
    rows = [[(f"📁 {cat['name']}", f"cat:{cat['id']}")] for cat in categories]
    rows.append([("⬅️ Menu chính", "menu:main")])
    return build_inline_keyboard(rows)


def _product_button_label(p: dict) -> str:
    from nimo_shop.money import fmt_money
    icon = str(p.get("product_icon") or "📦").strip() or "📦"
    return f"{icon} {p['name']} | {fmt_money(int(p['price_minor']), p['currency'])} | 📦 {int(p.get('available_stock') or 0)}"


def products_keyboard(products: list[dict], category_id: int | None = None):
    rows = [[(_product_button_label(p), f"prod:{p['id']}")] for p in products]
    if category_id is not None:
        rows.append([("⬅️ Danh mục", "buy:categories")])
    rows.append([("⬅️ Menu chính", "menu:main")])
    return build_inline_keyboard(rows)


def product_detail_keyboard_rows(product_id: int, available_stock: int) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    stock = max(0, int(available_stock or 0))
    if stock > 0:
        quick = [("✅ Mua 1", f"buyqty:{product_id}:1")]
        if stock >= 2:
            quick.append(("Mua 2", f"buyqty:{product_id}:2"))
        rows.append(quick)
        more: list[tuple[str, str]] = []
        if stock >= 3:
            more.append(("Mua 3", f"buyqty:{product_id}:3"))
        if stock >= 5:
            more.append(("Mua 5", f"buyqty:{product_id}:5"))
        if more:
            rows.append(more)
        rows.append([("✍️ Nhập số lượng khác", f"buycustom:{product_id}")])
    rows.append([("⬅️ Danh mục", "buy:categories"), ("🏠 Menu", "menu:main")])
    return rows


def product_detail_keyboard(product_id: int, available_stock: int):
    return build_inline_keyboard(product_detail_keyboard_rows(product_id, available_stock))


def order_payment_keyboard(order_id: int):
    return build_inline_keyboard([
        [("💰 Thanh toán bằng ví", f"paywallet:{order_id}")],
        [("➕ Nạp ví", "wallet:open"), ("🏦 Chuyển khoản ngân hàng", f"paybank:{order_id}")],
        [("🟡 Binance Pay/USDT", f"paybinance:{order_id}")],
        [("❌ Hủy đơn", f"cancel:{order_id}")],
    ])


def wallet_keyboard_rows() -> list[list[tuple[str, str]]]:
    rows = [[(f"➕ Nạp {amount:,}đ".replace(",", "."), f"topupbank:{amount}")] for amount in TOPUP_AMOUNTS_VND]
    rows.append([("✍️ Nạp số tiền khác", "topupcustom")])
    rows.append([("📜 Lịch sử mua", "history"), ("🏠 Menu", "menu:main")])
    return rows


def wallet_keyboard():
    return build_inline_keyboard(wallet_keyboard_rows())


def language_keyboard_rows() -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    items = [(language_display(code), f"lang:{code}") for code in SUPPORTED_LANGUAGES]
    for idx in range(0, len(items), 2):
        rows.append(items[idx:idx + 2])
    rows.append([("🏠 Menu", "menu:main")])
    return rows


def language_keyboard():
    return build_inline_keyboard(language_keyboard_rows())


def support_keyboard():
    return build_inline_keyboard([
        [("📦 Lỗi đơn hàng", "support:order"), ("🔐 Lỗi tài khoản/key", "support:key")],
        [("💰 Lỗi thanh toán", "support:payment"), ("👨‍💻 Gặp admin", "support:admin")],
        [("🏠 Menu", "menu:main")],
    ])


def search_results_keyboard(products: list[dict]):
    rows = [[(_product_button_label(p), f"prod:{p['id']}")] for p in products[:20]]
    rows.append([("🛒 Xem danh mục", "buy:categories"), ("🏠 Menu", "menu:main")])
    return build_inline_keyboard(rows)


def search_results_keyboard_rows(products: list[dict]) -> list[list[tuple[str, str]]]:
    rows = [[(_product_button_label(p), f"prod:{p['id']}")] for p in products[:20]]
    rows.append([("🛒 Xem danh mục", "buy:categories"), ("🏠 Menu", "menu:main")])
    return rows
