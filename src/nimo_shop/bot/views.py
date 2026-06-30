from __future__ import annotations

from collections.abc import Iterable
from html import escape

from nimo_shop.money import fmt_money
from nimo_shop.bot.i18n import SUPPORTED_LANGUAGES, language_display, t, normalize_lang


def h(value: object) -> str:
    return escape(str(value), quote=False)


def _wallet_block(balances: dict[str, int] | None, lang: str | None = "vi") -> str:
    title = t(lang, "wallet_block_title")
    if balances:
        lines = [title]
        for cur, amount in balances.items():
            lines.append(f"- {h(cur)}: <b>{fmt_money(int(amount), cur)}</b>")
        return "\n".join(lines)
    return f"{title}\n- {h(t(lang, 'no_balance'))}"


def welcome(shop_name: str, balances: dict[str, int] | None = None, lang: str | None = "vi") -> str:
    lang = normalize_lang(lang)
    return t(lang, "welcome").format(
        shop_name=h(shop_name),
        wallet_block=_wallet_block(balances or {}, lang),
    )


def profile(profile: dict, telegram_id: int | str, username: str | None) -> str:
    balances = profile.get("balances", {})
    if balances:
        balance_text = "\n".join(f"- {h(cur)}: <b>{fmt_money(int(amount), cur)}</b>" for cur, amount in balances.items())
    else:
        balance_text = "- Chưa có số dư"
    return (
        "👤 <b>Hồ sơ của bạn</b>\n\n"
        f"Telegram ID: <code>{h(telegram_id)}</code>\n"
        f"Username: @{h(username or 'không có')}\n"
        f"Tổng đơn đã mua: <b>{int(profile.get('order_count', 0))}</b>\n"
        f"Tổng đã chi: <b>{fmt_money(int(profile.get('total_spent_minor', 0)), 'VND')}</b>\n\n"
        f"💰 <b>Số dư ví</b>\n{balance_text}"
    )


def category_list(categories: list[dict]) -> str:
    if not categories:
        return "🛒 Hiện chưa có danh mục sản phẩm nào. Admin cần thêm danh mục trước."
    return "🛒 <b>Chọn danh mục sản phẩm</b>\n\nBấm vào một danh mục bên dưới để xem sản phẩm."


def product_list(products: list[dict], category_name: str | None = None) -> str:
    if not products:
        return "📦 Danh mục này hiện chưa có sản phẩm đang bán hoặc đã hết hàng."
    title = f"📦 <b>{h(category_name)}</b>" if category_name else "📦 <b>Sản phẩm đang bán</b>"
    lines = [title, ""]
    for product in products[:20]:
        lines.append(
            f"#{product['id']} — <b>{h(product['name'])}</b> | "
            f"{fmt_money(int(product['price_minor']), product['currency'])} | "
            f"Còn: {int(product.get('available_stock') or 0)}"
        )
    lines.append("\nBấm vào sản phẩm bên dưới để xem chi tiết.")
    return "\n".join(lines)


def product_detail(product: dict) -> str:
    stock = int(product.get("available_stock") or 0)
    status = "✅ Còn hàng" if stock > 0 else "❌ Hết hàng"
    return (
        f"🛒 <b>{h(product['name'])}</b>\n\n"
        f"Giá: <b>{fmt_money(int(product['price_minor']), product['currency'])}</b>\n"
        f"Tồn kho: <b>{stock}</b>\n"
        f"Trạng thái: {status}\n\n"
        f"📌 <b>Mô tả</b>\n{h(product.get('description') or 'Chưa có mô tả')}\n\n"
        f"🛡 <b>Bảo hành</b>\n{h(product.get('warranty_text') or 'Theo chính sách shop')}\n\n"
        "👇 Chọn số lượng muốn mua bên dưới. Nếu muốn mua số lượng khác, bấm <b>Nhập số lượng khác</b>."
    )


def order_created(order: dict, balances: dict[str, int] | None = None) -> str:
    current_balance = int((balances or {}).get(order["currency"], 0))
    total = int(order["total_amount_minor"])
    missing = max(0, total - current_balance)
    balance_line = f"Số dư ví hiện tại: <b>{fmt_money(current_balance, order['currency'])}</b>"
    if missing:
        balance_line += f"\nCòn thiếu nếu thanh toán bằng ví: <b>{fmt_money(missing, order['currency'])}</b>"
    else:
        balance_line += "\n✅ Ví đủ tiền để thanh toán đơn này."
    return (
        f"📦 <b>Đơn hàng {h(order['public_code'])}</b>\n\n"
        f"Sản phẩm: <b>{h(order['product_name'])}</b>\n"
        f"Số lượng: <b>{int(order['quantity'])}</b>\n"
        f"Đơn giá: <b>{fmt_money(int(order['unit_amount_minor']), order['currency'])}</b>\n"
        f"Tổng tiền: <b>{fmt_money(total, order['currency'])}</b>\n"
        f"{balance_line}\n"
        f"Trạng thái: <b>Chờ thanh toán</b>\n"
        f"Hết hạn: <code>{h(order['expires_at'])}</code>\n\n"
        "Chọn phương thức thanh toán bên dưới. Nếu quá hạn, hàng giữ tạm sẽ tự trả về kho."
    )


def payment_instruction(intent: dict, *, provider_label: str, extra: str = "") -> str:
    return (
        f"💳 <b>Thanh toán qua {h(provider_label)}</b>\n\n"
        f"Mã thanh toán: <code>{h(intent['public_code'])}</code>\n"
        f"Số tiền: <b>{fmt_money(int(intent['amount_minor']), intent['currency'])}</b>\n"
        f"Hết hạn: <code>{h(intent['expires_at'])}</code>\n\n"
        f"{extra.strip()}\n\n"
        "Sau khi hệ thống nhận tiền qua API/ngân hàng/Binance, bot sẽ tự cộng ví hoặc giao hàng. "
        "Nếu chuyển sai nội dung, admin vẫn có thể đối soát trong dòng tiền."
    ).strip()


def wallet(balances: dict[str, int]) -> str:
    lines = ["💰 <b>Ví của bạn</b>", ""]
    if balances:
        for cur, amount in balances.items():
            lines.append(f"- {h(cur)}: <b>{fmt_money(int(amount), cur)}</b>")
    else:
        lines.append("- Chưa có số dư")
    lines.append("\nBạn có thể nạp ví rồi dùng số dư ví để mua nhanh.")
    return "\n".join(lines)


def history(orders: list[dict]) -> str:
    if not orders:
        return "📜 Bạn chưa có đơn hàng nào."
    lines = ["📜 <b>Lịch sử mua hàng</b>", ""]
    for order in orders[:20]:
        lines.append(
            f"{h(order['public_code'])} — <b>{h(order['product_name'])}</b>\n"
            f"Trạng thái: <b>{h(order['status'])}</b> | "
            f"Tổng: {fmt_money(int(order['total_amount_minor']), order['currency'])} | "
            f"Ngày: <code>{h(order['created_at'])}</code>"
        )
    return "\n\n".join(lines)


DELIVERY_INLINE_LIMIT = 20
DELIVERY_TEXT_LIMIT = 3300


def delivery_file_text(order: dict, delivery_rows: Iterable[dict]) -> str:
    rows = list(delivery_rows)
    lines = [
        f"Don hang: {order['public_code']}",
        f"San pham: {order['product_name']}",
        f"So luong: {len(rows)}",
        f"Tong tien: {fmt_money(int(order['total_amount_minor']), order['currency'])}",
        "",
        "===== THONG TIN HANG =====",
    ]
    for idx, row in enumerate(rows, start=1):
        lines.append(f"{idx}. {row['delivered_content']}")
    lines.append("")
    lines.append("Vui long luu file nay. Neu hang loi, gui ma don cho admin de duoc ho tro.")
    return "\n".join(lines)


def delivery_filename(order: dict) -> str:
    code = str(order.get("public_code") or "order").replace("/", "_").replace("\\", "_")
    return f"{code}_delivery.txt"


def delivery_needs_file(order: dict, delivery_rows: Iterable[dict]) -> bool:
    rows = list(delivery_rows)
    if len(rows) > DELIVERY_INLINE_LIMIT:
        return True
    return len(delivery(order, rows)) > DELIVERY_TEXT_LIMIT


def delivery_file_summary(order: dict, delivery_rows: Iterable[dict]) -> str:
    rows = list(delivery_rows)
    return (
        f"✅ <b>Đã giao hàng cho đơn {h(order['public_code'])}</b>\n\n"
        f"Sản phẩm: <b>{h(order['product_name'])}</b>\n"
        f"Số lượng: <b>{len(rows)}</b>\n"
        f"Tổng tiền: <b>{fmt_money(int(order['total_amount_minor']), order['currency'])}</b>\n\n"
        "📎 Đơn này có nhiều dòng hàng nên bot đã gửi file TXT để bạn tải xuống và lưu lại. "
        "Nếu hàng lỗi, gửi mã đơn cho admin để được hỗ trợ."
    )


def delivery(order: dict, delivery_rows: Iterable[dict]) -> str:
    rows = list(delivery_rows)
    if len(rows) > DELIVERY_INLINE_LIMIT:
        return delivery_file_summary(order, rows)
    lines = [
        f"✅ <b>Đã giao hàng cho đơn {h(order['public_code'])}</b>",
        f"Sản phẩm: <b>{h(order['product_name'])}</b>",
        f"Số lượng: <b>{len(rows)}</b>",
        "",
        "🔐 <b>Thông tin hàng</b>",
    ]
    for idx, row in enumerate(rows, start=1):
        lines.append(f"{idx}. <code>{h(row['delivered_content'])}</code>")
    lines.append("\nVui lòng lưu lại thông tin. Nếu hàng lỗi, vào 💬 Hỗ trợ để liên hệ admin.")
    text = "\n".join(lines)
    if len(text) > DELIVERY_TEXT_LIMIT:
        return delivery_file_summary(order, rows)
    return text


def support(admin_contact: str | None = None) -> str:
    contact = f"\n\nAdmin: {h(admin_contact)}" if admin_contact else ""
    return (
        "💬 <b>Trung tâm hỗ trợ</b>\n\n"
        "Bạn có thể nhắn nội dung cần hỗ trợ kèm mã đơn hàng.\n"
        "Ví dụ: <code>Hỗ trợ ORD1234ABCD tài khoản không đăng nhập được</code>"
        f"{contact}"
    )


def language() -> str:
    lines = ["🌐 <b>Chọn ngôn ngữ</b>", ""]
    lines.append("Bot hiện hỗ trợ các ngôn ngữ phổ biến dưới đây. Sau khi chọn, menu chính sẽ đổi theo ngôn ngữ đó.")
    lines.append("")
    for code in SUPPORTED_LANGUAGES:
        lines.append(f"- {language_display(code)}")
    return "\n".join(lines)


def admin_help() -> str:
    return (
        "🛡 <b>Menu Admin</b>\n\n"
        "Lệnh quản trị nhanh:\n"
        "<code>/orders</code> | <code>/finance</code> | <code>/stock</code> | <code>/users</code>\n"
        "<code>/newcategory Tên danh mục</code>\n"
        "<code>/addproduct category_id | tên | giá_vnd | giá_vốn_vnd | mô tả | bảo hành</code>\n"
        "<code>/addstock product_id\nkey1\nkey2</code>\n"
        "<code>/confirm PAYMENT_CODE TX_ID AMOUNT [CURRENCY] [PROVIDER]</code>\n"
        "<code>/cancel ORDER_ID</code>\n"
        "<code>/refund ORDER_ID</code>\n"
        "<code>/sweep</code>\n"
        "<code>/audit</code>"
    )


def finance(summary: dict) -> str:
    lines = ["💵 <b>Quản lý dòng tiền</b>", ""]
    lines.append("<b>Tiền vào/ra theo provider</b>")
    if summary.get("cash"):
        for row in summary["cash"]:
            fee = int(row.get("fee_minor") or 0)
            fee_text = f", phí {fmt_money(fee, row['currency'])}" if fee else ""
            lines.append(
                f"- {h(row['currency'])} / {h(row['provider'])} / {h(row['direction'])}: "
                f"{fmt_money(int(row['amount_minor'] or 0), row['currency'])}{fee_text} ({int(row['count'])} GD)"
            )
    else:
        lines.append("- Chưa có giao dịch")
    lines.append("\n<b>Nợ ví khách đang giữ</b>")
    if summary.get("wallet_liabilities"):
        for row in summary["wallet_liabilities"]:
            lines.append(f"- {h(row['currency'])}: {fmt_money(int(row['liability_minor'] or 0), row['currency'])} / {int(row['wallets'])} ví")
    else:
        lines.append("- Chưa có số dư ví")
    lines.append("\n<b>Doanh thu & giá vốn</b>")
    if summary.get("sales"):
        for row in summary["sales"]:
            revenue = int(row.get("revenue_minor") or 0)
            cost = int(row.get("cost_minor") or 0)
            profit = revenue - cost
            lines.append(
                f"- {h(row['currency'])}: doanh thu {fmt_money(revenue, row['currency'])}, "
                f"giá vốn {fmt_money(cost, row['currency'])}, lãi gộp {fmt_money(profit, row['currency'])}, "
                f"{int(row['orders'])} đơn"
            )
    else:
        lines.append("- Chưa có đơn đã giao")
    lines.append("\n<b>Đơn hàng theo trạng thái</b>")
    for row in summary.get("orders_by_status") or []:
        lines.append(f"- {h(row['status'])}: {int(row['count'])}")
    return "\n".join(lines)


def stock_summary(rows: list[dict]) -> str:
    if not rows:
        return "📦 Chưa có sản phẩm/kho hàng."
    lines = ["📦 <b>Tồn kho</b>", ""]
    for row in rows:
        lines.append(
            f"#{row['product_id']} — <b>{h(row['name'])}</b>: "
            f"còn {int(row.get('available') or 0)}, giữ {int(row.get('reserved') or 0)}, đã bán {int(row.get('sold') or 0)}"
        )
    return "\n".join(lines)


def pending_orders(rows: list[dict]) -> str:
    if not rows:
        return "📦 Không có đơn chờ thanh toán."
    lines = ["📦 <b>Đơn chờ thanh toán</b>", ""]
    for row in rows[:30]:
        lines.append(
            f"#{row['id']} / {h(row['public_code'])} — user {h(row['telegram_id'])} — "
            f"{h(row['product_name'])} — {fmt_money(int(row['total_amount_minor']), row['currency'])} — "
            f"hết hạn <code>{h(row['expires_at'])}</code>"
        )
    return "\n".join(lines)


def search_prompt() -> str:
    return (
        "🔎 <b>Tìm kiếm sản phẩm</b>\n\n"
        "Nhập tên sản phẩm, danh mục hoặc từ khóa cần tìm.\n"
        "Ví dụ: <code>chatgpt</code>, <code>canva</code>, <code>capcut</code>.\n\n"
        "Bạn cũng có thể dùng lệnh: <code>/search chatgpt</code>"
    )


def search_results(query: str, products: list[dict]) -> str:
    if not products:
        return (
            f"🔎 <b>Kết quả tìm kiếm</b>\n\n"
            f"Từ khóa: <code>{h(query)}</code>\n\n"
            "Không tìm thấy sản phẩm phù hợp. Hãy thử từ khóa ngắn hơn hoặc bấm 🛒 Mua ngay để xem tất cả danh mục."
        )
    lines = ["🔎 <b>Kết quả tìm kiếm</b>", "", f"Từ khóa: <code>{h(query)}</code>", ""]
    for product in products[:20]:
        stock = int(product.get("available_stock") or 0)
        lines.append(
            f"#{product['id']} — <b>{h(product['name'])}</b>\n"
            f"Giá: <b>{fmt_money(int(product['price_minor']), product['currency'])}</b> · Còn: <b>{stock}</b>"
        )
    lines.append("\nBấm nút sản phẩm bên dưới để xem chi tiết/mua hàng.")
    return "\n\n".join(lines)
