from __future__ import annotations

from collections.abc import Iterable
from html import escape
import os
import re

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


def shop_home(shop_name: str, categories: list[dict], balances: dict[str, int] | None = None, lang: str | None = "vi") -> str:
    lang = normalize_lang(lang)
    lines = [
        "🎉 <b>WELCOME</b>",
        f"Chào mừng bạn đến với <b>{h(shop_name)}</b>",
        "<b>UY TÍN TẠO NÊN THƯƠNG HIỆU</b>",
        "",
        "Cảm ơn bạn đã tin tưởng và sử dụng hệ thống mua hàng tự động của chúng tôi.",
        "",
        "⚡ <b>TẠI ĐÂY</b>",
        "• Mua hàng tự động 24/7",
        "• Thanh toán QR - xác nhận tức thì",
        "• Nhận file ngay sau khi thanh toán",
        "",
        _wallet_block(balances or {}, lang),
        "",
        "Đây là bot tự động order 24/7",
        "👇 Vui lòng chọn danh mục/sản phẩm bên dưới 👇",
    ]
    if categories:
        lines.extend(["", "<b>Danh mục</b>"])
        for cat in categories[:30]:
            stock = int(cat.get("available_stock") or 0)
            status = "🟢" if stock > 0 else "🔴"
            icon = str(cat.get("category_icon") or "📁").strip() or "📁"
            lines.append(f"{status} {h(icon)} {h(cat['name'])} [{stock}]")
    return "\n".join(lines)


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
    lines = ["🛒 <b>Chọn danh mục sản phẩm</b>", "", "🟢 còn hàng · 🔴 hết hàng/tạm chưa có hàng", ""]
    for cat in categories[:30]:
        stock = int(cat.get("available_stock") or 0)
        status = "🟢" if stock > 0 else "🔴"
        icon = str(cat.get("category_icon") or "📁").strip() or "📁"
        lines.append(f"{status} {h(icon)} <b>{h(cat['name'])}</b> · còn <b>{stock}</b>")
    lines.append("\nBấm vào một danh mục bên dưới để xem sản phẩm.")
    return "\n".join(lines)

def _product_icon_html(product: dict) -> str:
    icon = str(product.get("product_icon") or "📦").strip() or "📦"
    custom_id = str(product.get("product_custom_emoji_id") or "").strip()
    if custom_id.isdigit():
        return f'<tg-emoji emoji-id="{h(custom_id)}">{h(icon[:2])}</tg-emoji>'
    return h(icon)


def product_button_label(product: dict) -> str:
    icon = str(product.get("product_icon") or "📦").strip() or "📦"
    stock = int(product.get("available_stock") or 0)
    return f"{icon} {product['name']} | {fmt_money(int(product['price_minor']), product['currency'])} | 📦 {stock}"


def product_image_path(product: dict) -> str:
    return str(product.get("product_image_path") or "").strip()


def product_has_image(product: dict) -> bool:
    return bool(product_image_path(product))


def product_list(products: list[dict], category_name: str | None = None) -> str:
    if not products:
        return "📦 Danh mục này hiện chưa có sản phẩm đang bán hoặc đã hết hàng."
    title = f"📦 <b>{h(category_name)}</b>" if category_name else "📦 <b>Sản phẩm đang bán</b>"
    lines = [title, ""]
    for product in products[:100]:
        lines.append(
            f"{_product_icon_html(product)} <b>{h(product['name'])}</b> | "
            f"{fmt_money(int(product['price_minor']), product['currency'])} | "
            f"📦 {int(product.get('available_stock') or 0)}"
        )
    lines.append("\nBấm vào sản phẩm bên dưới để xem ảnh, mô tả và chọn số lượng.")
    return "\n".join(lines)

def product_detail(product: dict) -> str:
    stock = int(product.get("available_stock") or 0)
    status = "✅ Còn hàng" if stock > 0 else "❌ Hết hàng"
    title = f"{_product_icon_html(product)} <b>{h(product['name'])}</b>"
    short = str(product.get("product_short_description") or "").strip()
    long_desc = str(product.get("product_long_description") or "").strip()
    desc = long_desc or str(product.get("description") or "").strip() or "Chưa có mô tả"
    parts = [
        title,
        "",
        f"Giá: <b>{fmt_money(int(product['price_minor']), product['currency'])}</b>",
        f"Tồn kho: <b>{stock}</b>",
        f"Trạng thái: {status}",
    ]
    if short:
        parts.extend(["", f"⭐ <b>Tóm tắt</b>\n{h(short)}"])
    parts.extend([
        "",
        f"📌 <b>Mô tả</b>\n{h(desc)}",
        "",
        f"🛡 <b>Bảo hành</b>\n{h(product.get('warranty_text') or 'Theo chính sách shop')}",
        "",
    ])
    if stock > 0:
        parts.append("👇 Chọn số lượng muốn mua bên dưới. Nếu muốn mua số lượng khác, bấm <b>Nhập số lượng khác</b>.")
    else:
        parts.append("🧾 Sản phẩm đang hết hàng. Bạn có thể bấm <b>Đặt trước</b>; phí đặt trước tính theo % shop cấu hình và được trừ khi admin xử lý đơn.")
    return "\n".join(parts)

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


def preorder_created(preorder: dict, balances: dict[str, int] | None = None) -> str:
    current_balance = int((balances or {}).get(preorder["currency"], 0))
    deposit = int(preorder["deposit_amount_minor"] or 0)
    missing = max(0, deposit - current_balance)
    balance_line = f"Số dư ví hiện tại: <b>{fmt_money(current_balance, preorder['currency'])}</b>"
    if missing:
        balance_line += f"\nCòn thiếu để đặt trước: <b>{fmt_money(missing, preorder['currency'])}</b>"
    else:
        balance_line += "\n✅ Ví đủ tiền để thanh toán phí đặt trước."
    return (
        f"🧾 <b>Đơn đặt trước {h(preorder['public_code'])}</b>\n\n"
        f"Sản phẩm: <b>{h(preorder['product_name'])}</b>\n"
        f"Số lượng: <b>{int(preorder['quantity'])}</b>\n"
        f"Đơn giá dự kiến: <b>{fmt_money(int(preorder['unit_amount_minor']), preorder['currency'])}</b>\n"
        f"Tổng dự kiến: <b>{fmt_money(int(preorder['total_amount_minor']), preorder['currency'])}</b>\n"
        f"Phí đặt trước: <b>{int(preorder['deposit_percent'])}%</b> = <b>{fmt_money(deposit, preorder['currency'])}</b>\n"
        f"{balance_line}\n"
        f"Trạng thái: <b>Chờ thanh toán phí đặt trước</b>\n\n"
        "Sau khi thanh toán phí đặt trước, admin sẽ thấy đơn trong Web Admin → Đặt trước và xử lý khi có hàng."
    )


def preorder_paid(preorder: dict) -> str:
    return (
        f"✅ <b>Đã nhận đặt trước {h(preorder['public_code'])}</b>\n\n"
        f"Sản phẩm: <b>{h(preorder['product_name'])}</b>\n"
        f"Số lượng: <b>{int(preorder['quantity'])}</b>\n"
        f"Phí đã cọc: <b>{fmt_money(int(preorder['deposit_amount_minor']), preorder['currency'])}</b>\n"
        f"Trạng thái: <b>Đang đặt trước</b>\n\n"
        "Khi shop nhập thêm hàng, admin sẽ xử lý đơn đặt trước của bạn."
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




def binance_id_instruction(intent: dict, *, binance_id: str, note: str = "") -> str:
    memo = note.strip() or "Gửi đúng số tiền, sau đó gửi ID giao dịch hoặc liên hệ admin để xác minh."
    return (
        f"🟡 <b>Thanh toán Binance</b>\n\n"
        f"Mã thanh toán: <code>{h(intent['public_code'])}</code>\n"
        f"Binance ID: <code>{h(binance_id or 'Chưa cấu hình')}</code>\n"
        f"Số tiền cần chuyển: <b>{fmt_money(int(intent['amount_minor']), intent['currency'])}</b>\n"
        f"Hết hạn: <code>{h(intent['expires_at'])}</code>\n\n"
        f"{h(memo)}\n\n"
        "Sau khi thanh toán, admin hoặc webhook sẽ xác nhận và bot tự giao hàng/cộng ví."
    )


def usdt_bep20_instruction(intent: dict, *, address: str, tolerance: str = "0.02") -> str:
    amount = fmt_money(int(intent['amount_minor']), intent['currency'])
    return (
        f"🌕 <b>Thanh toán USDT (BEP20)</b>\n\n"
        f"Mã thanh toán: <code>{h(intent['public_code'])}</code>\n"
        f"Vui lòng chuyển đúng: <b>{amount}</b>\n"
        f"Địa chỉ BEP20:\n<code>{h(address or 'Chưa cấu hình USDT_BEP20_ADDRESS')}</code>\n"
        f"Hết hạn: <code>{h(intent['expires_at'])}</code>\n\n"
        f"⚠️ Sai số cho phép: <b>{h(tolerance)} USDT</b>. Phí mạng không được trừ vào số tiền shop nhận.\n"
        "Sau khi chuyển, gửi TXID/hash cho admin hoặc bấm làm mới nếu đã có webhook/đối soát."
    )


def usdt_qr_url(address: str) -> str:
    import urllib.parse
    data = urllib.parse.quote(address or "")
    return f"https://api.qrserver.com/v1/create-qr-code/?size=420x420&data={data}"


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


def _delivery_mode() -> str:
    mode = os.getenv("DELIVERY_OUTPUT_MODE", "auto").strip().lower()
    return mode if mode in {"auto", "file_only", "inline_and_file"} else "auto"


def _delivery_threshold() -> int:
    try:
        value = int(os.getenv("DELIVERY_FILE_THRESHOLD", str(DELIVERY_INLINE_LIMIT)))
    except ValueError:
        value = DELIVERY_INLINE_LIMIT
    return max(1, min(value, 1000000))


def _stock_labels(order: dict) -> list[str]:
    labels_raw = str(order.get("stock_format_labels") or "").strip()
    if labels_raw:
        labels = [x.strip() for x in re.split(r"[|,;/\n]+", labels_raw) if x.strip()]
        if labels:
            return labels
    stock_format = str(order.get("stock_format") or "auto")
    defaults = {
        "email_pass_pipe": ["Email", "Mật khẩu"],
        "email_pass_slash": ["Email", "Mật khẩu"],
        "email_pass_2fa_pipe": ["Email", "Mật khẩu", "2FA/Recovery"],
        "uid_pass_cookie_token": ["UID", "Mật khẩu", "Cookie", "Token"],
        "pipe": ["Cột 1", "Cột 2", "Cột 3", "Cột 4"],
        "csv": ["Cột 1", "Cột 2", "Cột 3", "Cột 4"],
    }
    return defaults.get(stock_format, [])


def _format_delivery_content(order: dict, content: str, *, html_mode: bool = False) -> str:
    delivery_format = str(order.get("delivery_format") or "auto")
    if delivery_format == "raw" or "|" not in content:
        return h(content) if html_mode else content
    labels = _stock_labels(order)
    if not labels and delivery_format != "labeled":
        return h(content) if html_mode else content
    parts = [part.strip() for part in content.split("|")]
    lines = []
    for idx, part in enumerate(parts):
        label = labels[idx] if idx < len(labels) else f"Cột {idx + 1}"
        if html_mode:
            lines.append(f"<b>{h(label)}:</b> <code>{h(part)}</code>")
        else:
            lines.append(f"{label}: {part}")
    return "\n".join(lines)


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
        item = _format_delivery_content(order, str(row['delivered_content']), html_mode=False)
        if "\n" in item:
            lines.append(f"{idx}.")
            lines.extend("   " + part for part in item.splitlines())
        else:
            lines.append(f"{idx}. {item}")
    lines.append("")
    lines.append("Vui long luu file nay. Neu hang loi, gui ma don cho admin de duoc ho tro.")
    return "\n".join(lines)


def delivery_filename(order: dict) -> str:
    code = str(order.get("public_code") or "order").replace("/", "_").replace("\\", "_")
    return f"{code}_delivery.txt"


def _delivery_inline_text(order: dict, rows: list[dict]) -> str:
    lines = [
        f"✅ <b>Đã giao hàng cho đơn {h(order['public_code'])}</b>",
        f"Sản phẩm: <b>{h(order['product_name'])}</b>",
        f"Số lượng: <b>{len(rows)}</b>",
        "",
        "🔐 <b>Thông tin hàng</b>",
    ]
    raw_lines: list[str] = []
    for idx, row in enumerate(rows, start=1):
        raw = str(row['delivered_content'])
        item = _format_delivery_content(order, raw, html_mode=True)
        lines.append(f"{idx}. {item}")
        raw_lines.append(raw)
    if raw_lines:
        copy_block = "\n".join(raw_lines)
        if len(copy_block) <= 1200:
            lines.extend(["", "📋 <b>Bản copy nhanh</b>", f"<pre>{h(copy_block)}</pre>"])
    lines.append("\nVui lòng lưu lại thông tin. Nếu hàng lỗi, vào 💬 Hỗ trợ để liên hệ admin.")
    return "\n".join(lines)


def delivery_needs_file(order: dict, delivery_rows: Iterable[dict]) -> bool:
    rows = list(delivery_rows)
    mode = _delivery_mode()
    if mode in {"file_only", "inline_and_file"}:
        return True
    if len(rows) >= _delivery_threshold():
        return True
    return len(_delivery_inline_text(order, rows)) > DELIVERY_TEXT_LIMIT


def delivery_file_summary(order: dict, delivery_rows: Iterable[dict]) -> str:
    rows = list(delivery_rows)
    return (
        f"✅ <b>Đã giao hàng cho đơn {h(order['public_code'])}</b>\n\n"
        f"Sản phẩm: <b>{h(order['product_name'])}</b>\n"
        f"Số lượng: <b>{len(rows)}</b>\n"
        f"Tổng tiền: <b>{fmt_money(int(order['total_amount_minor']), order['currency'])}</b>\n\n"
        "📎 Bot đã gửi file TXT để bạn tải xuống và lưu lại. "
        "Nếu hàng lỗi, gửi mã đơn cho admin để được hỗ trợ."
    )


def delivery(order: dict, delivery_rows: Iterable[dict]) -> str:
    rows = list(delivery_rows)
    mode = _delivery_mode()
    inline = _delivery_inline_text(order, rows)
    if mode == "file_only":
        return delivery_file_summary(order, rows)
    if mode == "inline_and_file":
        if len(inline) <= DELIVERY_TEXT_LIMIT and len(rows) < _delivery_threshold():
            return inline + "\n\n📎 Bot cũng gửi kèm file TXT để bạn dễ lưu lại."
        return delivery_file_summary(order, rows)
    if len(rows) >= _delivery_threshold() or len(inline) > DELIVERY_TEXT_LIMIT:
        return delivery_file_summary(order, rows)
    return inline

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
    for product in products[:100]:
        stock = int(product.get("available_stock") or 0)
        lines.append(
            f"#{product['id']} — <b>{h(product['name'])}</b>\n"
            f"Giá: <b>{fmt_money(int(product['price_minor']), product['currency'])}</b> · Còn: <b>{stock}</b>"
        )
    lines.append("\nBấm nút sản phẩm bên dưới để xem chi tiết/mua hàng.")
    return "\n\n".join(lines)



def api_link(api_key: str, base_url: str) -> str:
    base = (base_url or "http://127.0.0.1:8080").rstrip("/")
    return (
        "🔗 <b>Liên kết API</b>\n\n"
        "API Key của bạn là:\n"
        f"<code>{h(api_key)}</code>\n"
        "(chạm để copy)\n\n"
        "⚠️ Giữ key an toàn — đừng chia sẻ với ai. Nếu lộ, bấm 🔄 Tạo key mới; key cũ sẽ ngừng hoạt động.\n\n"
        "<b>Tài liệu đầy đủ tại:</b>\n"
        f"{h(base)}/t/api-guide\n\n"
        "<b>Danh sách API:</b>\n"
        "• <code>GET /api/telegram-buyer/products</code>: Liệt kê sản phẩm hiện có.\n"
        "• <code>POST /api/telegram-buyer/purchase</code>: Mua sản phẩm bằng số dư ví.\n\n"
        "Lưu ý: Bạn cần nạp ví trước khi gọi API mua hàng. Gửi key bằng header: <code>X-API-Key</code> hoặc <code>Authorization: Bearer ...</code>."
    )
