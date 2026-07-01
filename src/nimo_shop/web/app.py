from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from nimo_shop.db import Database
from nimo_shop.money import fmt_money, from_minor
from nimo_shop.services.catalog import CatalogService
from nimo_shop.services.orders import OrderService, OutOfStock
from nimo_shop.services.users import UserService
from nimo_shop.services.wallet import InsufficientFunds, WalletService
from nimo_shop.web.security import create_session, csrf_token, read_session, verify_csrf
from nimo_shop.web.service import AdminWebService, DEFAULT_SETTING_KEYS, STOCK_FORMATS

LANG = {
    "vi": {
        "dashboard": "Tổng quan", "orders": "Đơn hàng", "products": "Sản phẩm", "categories": "Danh mục",
        "stock": "Kho hàng", "users": "Người dùng", "wallets": "Ví", "finance": "Dòng tiền",
        "payments": "Thanh toán", "settings": "Cấu hình", "audit": "Kiểm tra hệ thống", "logs": "Nhật ký admin", "bots": "Quản lý bot", "notifications": "Thông báo bot", "backup": "Backup dữ liệu", "guide": "Hướng dẫn", "status": "Trạng thái", "imports": "Nhập/Xuất", "exports": "Báo cáo", "reconcile": "Đối soát", "coupons": "Mã giảm giá", "roles": "Phân quyền", "deliveries": "Lịch sử giao hàng", "low_stock": "Cảnh báo kho", "preorders": "Đặt trước",
        "login": "Đăng nhập", "logout": "Đăng xuất", "save": "Lưu thay đổi", "create": "Tạo mới",
        "import_stock": "Nhập kho", "confirm_payment": "Xác nhận thanh toán", "light": "Sáng", "dark": "Tối",
        "language": "Ngôn ngữ", "theme": "Giao diện", "welcome": "Trang quản lý NIMO Shop",
        "admin_panel": "Bảng quản trị", "search_note": "Quản lý sản phẩm, kho, đơn, ví, dòng tiền và cấu hình bot từ trình duyệt.",
        "add_product": "Thêm sản phẩm", "edit_product": "Sửa sản phẩm", "delete": "Xóa", "edit": "Sửa", "back": "Quay lại",
    },
    "en": {
        "dashboard": "Dashboard", "orders": "Orders", "products": "Products", "categories": "Categories",
        "stock": "Inventory", "users": "Users", "wallets": "Wallets", "finance": "Finance",
        "payments": "Payments", "settings": "Settings", "audit": "System Audit", "logs": "Admin Logs", "bots": "Bot Manager", "notifications": "Bot Notifications", "backup": "Data Backup", "guide": "Guide", "status": "Status", "imports": "Import/Export", "exports": "Reports", "reconcile": "Reconciliation", "coupons": "Coupons", "roles": "Roles", "deliveries": "Delivery Logs", "low_stock": "Low Stock", "preorders": "Preorders",
        "login": "Login", "logout": "Logout", "save": "Save changes", "create": "Create",
        "import_stock": "Import stock", "confirm_payment": "Confirm payment", "light": "Light", "dark": "Dark",
        "language": "Language", "theme": "Theme", "welcome": "NIMO Shop Admin",
        "admin_panel": "Admin Panel", "search_note": "Manage products, stock, orders, wallets, finance and bot configuration from the browser.",
        "add_product": "Add product", "edit_product": "Edit product", "delete": "Delete", "edit": "Edit", "back": "Back",
    },
}


NAV_GROUPS = [
    ("Tổng quan", [("/", "dashboard"), ("/status", "status"), ("/logs", "logs"), ("/audit", "audit")]),
    ("Bán hàng", [("/orders", "orders"), ("/preorders", "preorders"), ("/deliveries", "deliveries"), ("/coupons", "coupons"), ("/notifications", "notifications")]),
    ("Sản phẩm & kho", [("/categories", "categories"), ("/products", "products"), ("/stock", "stock"), ("/imports", "imports"), ("/low-stock", "low_stock")]),
    ("Thanh toán & ví", [("/wallets", "wallets"), ("/payments", "payments"), ("/finance", "finance"), ("/reconcile", "reconcile"), ("/exports", "exports")]),
    ("Khách hàng & API", [("/users", "users")]),
    ("Hệ thống", [("/settings", "settings"), ("/bots", "bots"), ("/backup", "backup"), ("/roles", "roles"), ("/guide", "guide")]),
]

NAV = [
    ("/", "dashboard"), ("/orders", "orders"), ("/preorders", "preorders"), ("/products", "products"), ("/categories", "categories"),
    ("/stock", "stock"), ("/users", "users"), ("/wallets", "wallets"), ("/finance", "finance"),
    ("/payments", "payments"), ("/reconcile", "reconcile"), ("/bots", "bots"), ("/notifications", "notifications"),
    ("/backup", "backup"), ("/imports", "imports"), ("/exports", "exports"), ("/coupons", "coupons"), ("/low-stock", "low_stock"),
    ("/deliveries", "deliveries"), ("/roles", "roles"), ("/status", "status"), ("/settings", "settings"), ("/guide", "guide"), ("/audit", "audit"), ("/logs", "logs"),
]

# Page/action permission map. Earlier versions stored admin roles but did not
# enforce them, which made every logged-in admin effectively an owner. Keep the
# policy simple and conservative for a commercial shop.
ROLE_SECTIONS = {
    "owner": {"*"},
    "finance": {"dashboard", "orders", "users", "wallets", "finance", "payments", "reconcile", "exports", "status", "audit", "guide"},
    "stock": {"dashboard", "orders", "products", "categories", "stock", "imports", "exports", "low_stock", "status", "audit", "guide"},
    "support": {"dashboard", "orders", "preorders", "users", "notifications", "deliveries", "status", "audit", "guide"},
    "viewer": {"dashboard", "orders", "products", "categories", "stock", "users", "finance", "payments", "reconcile", "preorders", "deliveries", "status", "audit", "guide"},
}

POST_SECTIONS = {
    "/categories/create": "categories",
    "/categories/update": "categories",
    "/preorders/cancel": "preorders",
    "/preorders/fulfill": "preorders",
    "/products/create": "products",
    "/products/update": "products",
    "/products/delete": "products",
    "/stock/import": "stock",
    "/orders/cancel": "orders",
    "/orders/refund": "orders",
    "/wallets/adjust": "wallets",
    "/payments/confirm": "payments",
    "/bots/create": "bots",
    "/bots/update": "bots",
    "/bots/delete": "bots",
    "/notifications/create": "notifications",
    "/backup/restore": "backup",
    "/status/check-token": "status",
    "/imports/catalog": "imports",
    "/reconcile/review": "reconcile",
    "/coupons/create": "coupons",
    "/coupons/update": "coupons",
    "/coupons/delete": "coupons",
    "/roles/create": "roles",
    "/roles/update": "roles",
    "/low-stock/notify": "low_stock",
    "/settings": "settings",
}

WRITE_ROLES = {
    "categories": {"owner", "stock"},
    "products": {"owner", "stock"},
    "stock": {"owner", "stock"},
    "imports": {"owner", "stock"},
    "low_stock": {"owner", "stock"},
    "wallets": {"owner", "finance"},
    "payments": {"owner", "finance"},
    "finance": {"owner", "finance"},
    "reconcile": {"owner", "finance"},
    "orders": {"owner", "finance", "support"},
    "preorders": {"owner", "support"},
    "notifications": {"owner", "support"},
    "settings": {"owner"},
    "bots": {"owner"},
    "backup": {"owner"},
    "roles": {"owner"},
    "coupons": {"owner", "finance"},
    "status": {"owner"},
}


def section_for_path(path: str) -> str:
    if path in {"", "/"}:
        return "dashboard"
    if path.startswith("/products/preview"):
        return "products"
    if path.startswith("/media/products/"):
        return "products"
    if path.startswith("/low-stock"):
        return "low_stock"
    first = path.strip("/").split("/", 1)[0].replace("-", "_")
    return {"logs": "logs"}.get(first, first or "dashboard")


def role_can_read(role: str, path: str) -> bool:
    allowed = ROLE_SECTIONS.get(role or "viewer", ROLE_SECTIONS["viewer"])
    return "*" in allowed or section_for_path(path) in allowed


def role_can_write(role: str, path: str) -> bool:
    if role == "owner":
        return True
    section = POST_SECTIONS.get(path, section_for_path(path))
    return role in WRITE_ROLES.get(section, set())

SETTING_GROUPS = [
    {
        "title": "1. Thông tin shop & admin Telegram",
        "desc": "Nhập thông tin hiển thị cho khách và danh sách Telegram ID được phép dùng lệnh /admin.",
        "keys": ["SHOP_NAME", "SUPPORT_CONTACT", "ADMIN_IDS"],
    },
    {
        "title": "2. Bot Telegram",
        "desc": "Token lấy từ @BotFather. Nếu token chưa đúng, hệ thống chỉ mở Web Setup và chưa chạy bot.",
        "keys": ["BOT_TOKEN", "DATABASE_PATH", "DEPOSIT_EXPIRES_MINUTES", "ORDER_EXPIRES_MINUTES"],
    },
    {
        "title": "3. Ngân hàng Việt Nam & SePay",
        "desc": "Dùng để tạo VietQR và tự quét giao dịch chuyển khoản. Có thể tắt ở giai đoạn test.",
        "keys": ["BANK_ENABLED", "BANK_BIN", "BANK_ACCOUNT", "BANK_OWNER", "BANK_NAME", "SEPAY_API_KEY", "SEPAY_POLL_SECONDS"],
    },
    {
        "title": "4. Binance Pay",
        "desc": "Chỉ bật khi bạn có Binance Pay merchant API key/secret và URL webhook/return hợp lệ.",
        "keys": ["BINANCE_PAY_ENABLED", "BINANCE_PAY_API_KEY", "BINANCE_PAY_SECRET_KEY", "BINANCE_PAY_BASE_URL", "BINANCE_PAY_RETURN_URL", "BINANCE_PAY_WEBHOOK_URL"],
    },
    {
        "title": "5. Giao hàng cho khách",
        "desc": "Chọn cách bot gửi tài khoản/key sau khi khách thanh toán. Nên dùng file nếu bán nhiều dòng hoặc muốn khách dễ lưu lại.",
        "keys": ["DELIVERY_OUTPUT_MODE", "DELIVERY_FILE_THRESHOLD", "LOW_STOCK_THRESHOLD", "PREORDER_DEPOSIT_PERCENT", "STOCK_DUPLICATE_POLICY"],
    },
    {
        "title": "6. Web Admin",
        "desc": "Tài khoản đăng nhập trang quản trị, giao diện mặc định và cổng chạy web.",
        "keys": ["WEB_ADMIN_USERNAME", "WEB_ADMIN_PASSWORD", "WEB_SESSION_SECRET", "WEB_HOST", "WEB_PORT", "WEB_DEFAULT_LANGUAGE", "WEB_DEFAULT_THEME", "API_PUBLIC_BASE_URL"],
    },
]

SETTING_META: dict[str, dict[str, str]] = {
    "SHOP_NAME": {"label": "Tên shop", "help": "Tên hiển thị trong bot và web admin.", "placeholder": "NIMO SHOP PREMIUM"},
    "SUPPORT_CONTACT": {"label": "Liên hệ hỗ trợ", "help": "Username Telegram hoặc link hỗ trợ gửi cho khách.", "placeholder": "@username_ho_tro"},
    "ADMIN_IDS": {"label": "Telegram ID admin", "help": "Nhập một hoặc nhiều ID, cách nhau bằng dấu phẩy. Ví dụ: 123456789,987654321.", "placeholder": "123456789"},
    "BOT_TOKEN": {"label": "Bot Token", "help": "Lấy từ @BotFather. Định dạng thường là 123456789:AA.... Không chia sẻ token này.", "placeholder": "123456789:AA..."},
    "DATABASE_PATH": {"label": "Đường dẫn database", "help": "Nên giữ mặc định data/shop.db. Không đổi nếu bạn không chắc.", "placeholder": "data/shop.db"},
    "DEPOSIT_EXPIRES_MINUTES": {"label": "Hết hạn mã nạp ví sau", "help": "Số phút mã nạp ví còn hiệu lực.", "placeholder": "15"},
    "ORDER_EXPIRES_MINUTES": {"label": "Hết hạn đơn hàng sau", "help": "Số phút bot giữ hàng chờ khách thanh toán.", "placeholder": "20"},
    "BANK_ENABLED": {"label": "Bật thanh toán ngân hàng", "help": "Bật để bot tạo VietQR/chuyển khoản ngân hàng.", "placeholder": "false"},
    "SEPAY_API_KEY": {"label": "SePay API key", "help": "Dùng để bot tự đọc biến động giao dịch ngân hàng. Để trống nếu chưa dùng auto.", "placeholder": "sepay_api_key"},
    "SEPAY_POLL_SECONDS": {"label": "Chu kỳ quét SePay", "help": "Số giây giữa mỗi lần bot kiểm tra giao dịch. Khuyến nghị 30.", "placeholder": "30"},
    "BANK_BIN": {"label": "Mã ngân hàng / Bank BIN", "help": "Mã VietQR của ngân hàng nhận tiền. Ví dụ MB: 970422, Vietcombank: 970436.", "placeholder": "970436"},
    "BANK_ACCOUNT": {"label": "Số tài khoản nhận tiền", "help": "Số tài khoản ngân hàng của bạn để khách chuyển khoản.", "placeholder": "0123456789"},
    "BANK_OWNER": {"label": "Tên chủ tài khoản", "help": "Tên chủ tài khoản, nên viết đúng như app ngân hàng hiển thị.", "placeholder": "PHAM XUAN TOI"},
    "BANK_NAME": {"label": "Tên ngân hàng", "help": "Tên dễ nhớ để hiển thị cho khách. Ví dụ: Vietcombank, MB Bank.", "placeholder": "Vietcombank"},
    "BINANCE_PAY_ENABLED": {"label": "Bật Binance Pay", "help": "Chỉ bật khi đã có Binance merchant key và webhook HTTPS.", "placeholder": "false"},
    "BINANCE_PAY_API_KEY": {"label": "Binance API key", "help": "API key của Binance Pay merchant.", "placeholder": ""},
    "BINANCE_PAY_SECRET_KEY": {"label": "Binance secret key", "help": "Secret key của Binance Pay merchant. Không chia sẻ.", "placeholder": ""},
    "BINANCE_PAY_BASE_URL": {"label": "Binance API URL", "help": "Giữ mặc định nếu dùng Binance production.", "placeholder": "https://bpay.binanceapi.com"},
    "BINANCE_PAY_RETURN_URL": {"label": "Binance return URL", "help": "Link khách quay lại sau thanh toán. Có thể để trống khi test thủ công.", "placeholder": "https://domain-cua-ban/return"},
    "BINANCE_PAY_WEBHOOK_URL": {"label": "Binance webhook URL", "help": "URL HTTPS public để Binance gửi trạng thái thanh toán. Không có domain thì để trống.", "placeholder": "https://domain-cua-ban/webhook/binance"},
    "WEB_ADMIN_USERNAME": {"label": "Tên đăng nhập web", "help": "Username dùng để đăng nhập Web Admin.", "placeholder": "admin"},
    "WEB_ADMIN_PASSWORD": {"label": "Mật khẩu web", "help": "Để trống nếu không muốn đổi. Nhập mật khẩu mới nếu muốn đổi đăng nhập web.", "placeholder": "Nhập mật khẩu mới nếu muốn đổi"},
    "WEB_SESSION_SECRET": {"label": "Khóa bảo mật phiên web", "help": "Chuỗi ngẫu nhiên dài để ký cookie đăng nhập. Có thể tạo bằng nút/lệnh random hoặc nhập chuỗi dài.", "placeholder": "chuoi-ngau-nhien-rat-dai"},
    "WEB_HOST": {"label": "Web host", "help": "0.0.0.0 để mở trong mạng LAN; 127.0.0.1 chỉ mở trên máy hiện tại.", "placeholder": "0.0.0.0"},
    "WEB_PORT": {"label": "Web port", "help": "Cổng chạy Web Admin. Mặc định 8080.", "placeholder": "8080"},
    "WEB_DEFAULT_LANGUAGE": {"label": "Ngôn ngữ mặc định", "help": "vi cho tiếng Việt, en cho tiếng Anh.", "placeholder": "vi"},
    "WEB_DEFAULT_THEME": {"label": "Giao diện mặc định", "help": "light hoặc dark.", "placeholder": "light"},
    "DELIVERY_OUTPUT_MODE": {"label": "Cách giao hàng", "help": "auto = đơn nhỏ hiện trong chat, đơn lớn gửi file; file_only = mọi đơn đều gửi file; inline_and_file = đơn nhỏ vừa hiện trong chat vừa gửi file.", "placeholder": "auto"},
    "DELIVERY_FILE_THRESHOLD": {"label": "Gửi file khi từ số lượng", "help": "Chỉ áp dụng cho chế độ tự động. Ví dụ 20 nghĩa là đơn từ 20 dòng trở lên sẽ gửi file TXT.", "placeholder": "20"},
    "PREORDER_DEPOSIT_PERCENT": {"label": "Phí đặt trước (%)", "help": "Khi sản phẩm hết hàng, khách có thể đặt trước và trả trước số % này bằng ví. Ví dụ 10 nghĩa là cọc 10% tổng tiền.", "placeholder": "10"},
    "LOW_STOCK_THRESHOLD": {"label": "Ngưỡng cảnh báo hết hàng", "help": "Web sẽ cảnh báo khi tồn kho sản phẩm nhỏ hơn hoặc bằng số này.", "placeholder": "5"},
}

CSS = r"""
:root{--bg:#f4f7fb;--panel:#ffffff;--panel2:#eef4ff;--text:#0f172a;--muted:#64748b;--brand:#2563eb;--brand2:#7c3aed;--line:#e2e8f0;--danger:#dc2626;--ok:#16a34a;--warn:#d97706;--shadow:0 18px 45px rgba(15,23,42,.09);--radius:20px;--input:#fbfdff}
[data-theme="dark"]{--bg:#07111f;--panel:#101827;--panel2:#16243a;--text:#e5e7eb;--muted:#9aa8bd;--brand:#60a5fa;--brand2:#a78bfa;--line:#26364d;--danger:#fb7185;--ok:#4ade80;--warn:#fbbf24;--shadow:0 20px 55px rgba(0,0,0,.38);--input:#0b1526}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top left,rgba(37,99,235,.13),transparent 35%),var(--bg);color:var(--text);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}a{color:inherit;text-decoration:none}button,.btn{border:0;background:linear-gradient(135deg,var(--brand),var(--brand2));color:white;padding:10px 14px;border-radius:13px;cursor:pointer;font-weight:800;box-shadow:0 10px 22px rgba(37,99,235,.22);display:inline-flex;align-items:center;gap:7px}.btn.secondary,button.secondary{background:var(--panel2);color:var(--text);box-shadow:none;border:1px solid var(--line)}.btn.danger,button.danger{background:var(--danger);box-shadow:none}.btn.ghost,button.ghost{background:transparent;color:var(--text);border:1px solid var(--line);box-shadow:none}.btn.small,button.small{padding:7px 10px;border-radius:10px;font-size:13px}.layout{display:grid;grid-template-columns:280px 1fr;min-height:100vh}.sidebar{padding:22px;background:rgba(255,255,255,.78);backdrop-filter:blur(16px);border-right:1px solid var(--line);position:sticky;top:0;height:100vh;overflow:auto}[data-theme="dark"] .sidebar{background:rgba(16,24,39,.84)}.brand{font-weight:900;font-size:22px;letter-spacing:.2px;margin-bottom:6px}.brand-badge{display:inline-flex;background:linear-gradient(135deg,var(--brand),var(--brand2));color:white;border-radius:12px;padding:6px 10px;font-size:12px;margin-bottom:12px}.subtitle{color:var(--muted);font-size:13px;margin-bottom:20px}.nav-heading{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:900;margin:14px 8px 6px}.nav-group{margin-bottom:7px}.nav a{display:flex;padding:12px 14px;border-radius:14px;color:var(--muted);margin:5px 0;font-weight:700}.nav a.active,.nav a:hover{background:var(--panel2);color:var(--text)}.main{padding:26px;max-width:1500px}.topbar{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:22px}.h1{font-size:30px;font-weight:900;margin:0}.toolbar{display:flex;gap:9px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px;margin-bottom:16px}.card h3,.card h2{margin-top:0}.metric{font-size:28px;font-weight:900}.muted{color:var(--muted)}.help{color:var(--muted);font-size:13px;margin-top:5px}.status{display:inline-flex;padding:5px 10px;border-radius:999px;background:var(--panel2);font-size:12px;font-weight:800}.status.delivered,.status.paid,.status.order_delivered,.status.wallet_credited,.status.active{color:var(--ok)}.status.cancelled,.status.refunded,.status.rejected,.status.unmatched,.status.inactive{color:var(--danger)}.status.awaiting_payment,.status.pending,.status.reserved{color:var(--warn)}.table-wrap{overflow:auto}.premium-table{min-width:900px}table{width:100%;border-collapse:collapse}th,td{padding:13px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}tr:hover td{background:rgba(37,99,235,.035)}label{font-weight:800;display:block}input,select,textarea{width:100%;border:1px solid var(--line);background:var(--input);color:var(--text);padding:11px 13px;border-radius:13px;font:inherit;margin-top:6px}textarea{min-height:110px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.form-grid .full{grid-column:1/-1}.alert{padding:13px 15px;border-radius:15px;margin-bottom:14px;background:var(--panel2);border:1px solid var(--line)}.alert.ok{border-color:rgba(22,163,74,.45)}.alert.err{border-color:rgba(220,38,38,.45);color:var(--danger)}.login{min-height:100vh;display:grid;place-items:center;padding:24px}.login-card{max-width:440px;width:100%}.pill{display:inline-flex;gap:8px;align-items:center;padding:7px 10px;border-radius:999px;background:var(--panel2);color:var(--muted);font-weight:800;font-size:12px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.product-title{font-weight:900}.action-row{display:flex;gap:7px;flex-wrap:wrap}.danger-zone{border:1px dashed rgba(220,38,38,.55);background:rgba(220,38,38,.04)}details.setup-section{border:1px solid var(--line);border-radius:18px;background:var(--panel);margin-bottom:14px;overflow:hidden}details.setup-section[open]{box-shadow:var(--shadow)}details.setup-section summary{cursor:pointer;padding:16px 18px;font-weight:900;list-style:none;display:flex;justify-content:space-between;gap:12px}details.setup-section summary::-webkit-details-marker{display:none}.setup-content{border-top:1px solid var(--line);padding:18px}.setting-field{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px}.setting-field.secret input::placeholder{color:var(--muted)}.hint-box,.info-box{border-left:4px solid var(--brand);background:var(--panel2);padding:13px 15px;border-radius:14px;margin-bottom:16px}.info-box{font-size:14px}.hide-mobile{display:inline}@media(max-width:980px){.layout{grid-template-columns:1fr}.sidebar{position:relative;height:auto}.main{padding:16px}.grid,.grid2,.form-grid{grid-template-columns:1fr}.hide-mobile{display:none}.topbar{display:block}.premium-table{min-width:760px}}
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def tr(lang: str, key: str) -> str:
    return LANG.get(lang, LANG["vi"]).get(key, key)


def money(value: Any, currency: str = "VND") -> str:
    try:
        return fmt_money(int(value or 0), currency or "VND")
    except Exception:
        return esc(value)


def status_badge(value: str) -> str:
    return f'<span class="status {esc(value)}">{esc(value)}</span>'


def amount_input(value: Any, currency: str = "VND") -> str:
    try:
        dec = from_minor(int(value or 0), currency or "VND")
        if currency == "VND":
            return str(int(dec))
        text = format(dec, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    except Exception:
        return esc(value)


def selected(current: Any, expected: Any) -> str:
    return "selected" if str(current) == str(expected) else ""


class AdminRequestHandler(BaseHTTPRequestHandler):
    server_version = "NIMOAdmin/1.5"

    @property
    def service(self) -> AdminWebService:
        return self.server.service  # type: ignore[attr-defined]

    @property
    def session_secret(self) -> str:
        return self.server.session_secret  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.getenv("WEB_DEBUG"):
            super().log_message(fmt, *args)

    def _cookies(self) -> SimpleCookie:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        return cookie

    def _cookie_value(self, key: str) -> str | None:
        morsel = self._cookies().get(key)
        return morsel.value if morsel else None

    def _theme_lang(self) -> tuple[str, str]:
        query = parse_qs(urlparse(self.path).query)
        lang = query.get("lang", [self._cookie_value("nimo_lang") or "vi"])[0]
        theme = query.get("theme", [self._cookie_value("nimo_theme") or "light"])[0]
        if lang not in {"vi", "en"}:
            lang = "vi"
        if theme not in {"light", "dark"}:
            theme = "light"
        return lang, theme

    def _session(self):
        return read_session(self.session_secret, self._cookie_value("nimo_session"))

    def _send(self, body: str | bytes, status: int = 200, content_type: str = "text/html; charset=utf-8", headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str, cookies: list[str] | None = None) -> None:
        headers = {"Location": location}
        if cookies:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            return
        self._send(b"", int(HTTPStatus.SEE_OTHER), headers=headers)

    def _read_limited_body(self, *, max_bytes: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > max_bytes:
            raise ValueError(f"request body too large: max {max_bytes} bytes")
        return self.rfile.read(length) if length else b""

    def _read_form(self) -> dict[str, Any]:
        raw_bytes = self._read_limited_body(max_bytes=10 * 1024 * 1024)
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            form: dict[str, Any] = {}
            boundary = ""
            for item in content_type.split(";"):
                item = item.strip()
                if item.startswith("boundary="):
                    boundary = item.split("=", 1)[1].strip().strip('"')
            if not boundary:
                return form
            marker = ("--" + boundary).encode()
            for part in raw_bytes.split(marker):
                part = part.strip(b"\r\n")
                if not part or part == b"--":
                    continue
                if b"\r\n\r\n" not in part:
                    continue
                header_blob, body = part.split(b"\r\n\r\n", 1)
                body = body.rstrip(b"\r\n")
                headers = header_blob.decode("utf-8", errors="replace").split("\r\n")
                disp = next((h for h in headers if h.lower().startswith("content-disposition:")), "")
                attrs: dict[str, str] = {}
                for segment in disp.split(";"):
                    if "=" in segment:
                        k, v = segment.strip().split("=", 1)
                        attrs[k.lower()] = v.strip().strip('"')
                name = attrs.get("name")
                if not name:
                    continue
                filename = attrs.get("filename")
                if filename:
                    form[name + "_filename"] = filename
                    form[name + "_bytes"] = body
                    try:
                        form[name] = body.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        form[name] = body.decode("utf-8", errors="replace")
                else:
                    form[name] = body.decode("utf-8", errors="replace")
            return form
        raw = raw_bytes.decode("utf-8") if raw_bytes else ""
        return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _require_login(self):
        session = self._session()
        if not session:
            self._redirect("/login")
            return None
        return session

    def _csrf(self) -> str:
        token = self._cookie_value("nimo_session") or ""
        return csrf_token(self.session_secret, token) if token else ""

    def _verify_post(self, form: dict[str, str]) -> bool:
        return verify_csrf(self.session_secret, self._cookie_value("nimo_session"), form.get("csrf"))

    def _form_csrf(self) -> str:
        return f'<input type="hidden" name="csrf" value="{esc(self._csrf())}">'

    def _page(self, title_key: str, body: str, *, active: str | None = None, alert: str = "", error: str = "") -> str:
        lang, theme = self._theme_lang()
        session = self._session()
        role = session.role if session else "viewer"
        nav_parts = []
        for group_title, items in NAV_GROUPS:
            links = []
            for path, key in items:
                if role_can_read(role, path):
                    links.append(f'<a class="{("active" if active == path else "")}" href="{path}">{tr(lang, key)}</a>')
            if links:
                nav_parts.append(f'<div class="nav-group"><div class="nav-heading">{esc(group_title)}</div>' + "".join(links) + '</div>')
        nav = "".join(nav_parts)
        toolbar_qs_light = urlencode({"theme": "light", "lang": lang})
        toolbar_qs_dark = urlencode({"theme": "dark", "lang": lang})
        switch_lang = "en" if lang == "vi" else "vi"
        toolbar_qs_lang = urlencode({"theme": theme, "lang": switch_lang})
        msg = ""
        if alert:
            msg += f'<div class="alert ok">{esc(alert)}</div>'
        if error:
            msg += f'<div class="alert err">{esc(error)}</div>'
        user = esc(session.username if session else "")
        return f"""<!doctype html><html lang="{esc(lang)}" data-theme="{esc(theme)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(tr(lang,title_key))} - NIMO</title><style>{CSS}</style></head><body><div class="layout"><aside class="sidebar"><div class="brand-badge">Premium Admin</div><div class="brand">NIMO Shop</div><div class="subtitle">{tr(lang,'admin_panel')} · {user}</div><nav class="nav">{nav}</nav><div style="margin-top:18px" class="toolbar"><a class="btn secondary small" href="?{toolbar_qs_light}">{tr(lang,'light')}</a><a class="btn secondary small" href="?{toolbar_qs_dark}">{tr(lang,'dark')}</a><a class="btn secondary small" href="?{toolbar_qs_lang}">{switch_lang.upper()}</a><a class="btn danger small" href="/logout">{tr(lang,'logout')}</a></div></aside><main class="main"><div class="topbar"><div><h1 class="h1">{esc(tr(lang,title_key))}</h1><div class="muted">{esc(tr(lang,'search_note'))}</div></div></div>{msg}{body}</main></div></body></html>"""

    def _login_page(self, error: str = "") -> str:
        lang, theme = self._theme_lang()
        err = f'<div class="alert err">{esc(error)}</div>' if error else ""
        return f"""<!doctype html><html lang="{esc(lang)}" data-theme="{esc(theme)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Đăng nhập - NIMO</title><style>{CSS}</style></head><body><div class="login"><div class="card login-card"><div class="brand-badge">Premium Admin</div><div class="brand">NIMO Shop Admin</div><p class="muted">Đăng nhập để quản lý sản phẩm, kho hàng, đơn, ví và cấu hình thanh toán.</p>{err}<form method="post" action="/login"><label>Tên đăng nhập<input name="username" autocomplete="username" placeholder="admin"></label><br><label>Mật khẩu<input type="password" name="password" autocomplete="current-password" placeholder="mật khẩu đã cấu hình"></label><br><br><button>{esc(tr(lang,'login'))}</button></form></div></div></body></html>"""


    def _send_json(self, payload: dict[str, Any] | list[Any], status: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False), status=status, content_type="application/json; charset=utf-8")

    def _read_json_body(self) -> dict[str, Any]:
        raw = self._read_limited_body(max_bytes=256 * 1024).decode("utf-8")
        if not raw.strip():
            return {}
        if self.headers.get("Content-Type", "").startswith("application/json") or raw.strip().startswith("{"):
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _webhook_secret(self) -> str:
        try:
            setting = self.service.get_settings().get("WEBHOOK_SHARED_SECRET")
            if isinstance(setting, dict):
                value = setting.get("value")
            else:
                value = setting
            return str(value or os.getenv("WEBHOOK_SHARED_SECRET") or "").strip()
        except Exception:
            return str(os.getenv("WEBHOOK_SHARED_SECRET") or "").strip()

    def _verify_webhook_request(self, raw_body: str) -> bool:
        secret = self._webhook_secret()
        if not secret:
            return False
        direct = self.headers.get("X-NIMO-Webhook-Secret", "").strip()
        if direct and hmac.compare_digest(direct, secret):
            return True
        signature = self.headers.get("X-NIMO-Signature", "").strip()
        expected = hmac.new(secret.encode("utf-8"), raw_body.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) or hmac.compare_digest(signature, f"sha256={expected}")

    def _api_key_from_request(self) -> str:
        auth = self.headers.get("Authorization", "").strip()
        if auth.lower().startswith("bearer "):
            return auth.split(None, 1)[1].strip()
        return self.headers.get("X-API-Key", "").strip()

    def _api_user(self) -> dict[str, Any] | None:
        return UserService(self.service.db).find_by_api_key(self._api_key_from_request())

    def _api_guide(self) -> str:
        guide = (
            '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>NIMO Buyer API Guide</title><style>' + CSS + '</style></head><body>'
            '<div class="main"><div class="card"><h1>🔗 NIMO Telegram Buyer API</h1>'
            '<p class="muted">API cho khách mua hàng tự động bằng số dư ví. Gửi key bằng header '
            '<code>X-API-Key</code> hoặc <code>Authorization: Bearer &lt;key&gt;</code>.</p>'
            '<h2>1. Liệt kê sản phẩm</h2><pre>GET /api/telegram-buyer/products\nX-API-Key: tgb_xxx</pre>'
            '<h2>2. Mua sản phẩm bằng ví</h2><pre>POST /api/telegram-buyer/purchase\nContent-Type: application/json\nX-API-Key: tgb_xxx\n\n{"product_id":1,"quantity":2}</pre>'
            '<p>Response thành công trả về mã đơn và danh sách dữ liệu đã giao. Bạn cần nạp ví trước khi gọi API mua hàng.</p>'
            '<h2>Lưu ý bảo mật</h2><ul><li>Không chia sẻ API key cho người khác.</li>'
            '<li>Nếu lộ key, vào bot → 🔗 API → 🔄 Tạo key mới.</li>'
            '<li>API mua hàng sẽ trừ ví ngay và giao hàng nếu đủ tiền/tồn kho.</li></ul>'
            '</div></div></body></html>'
        )
        return guide

    def _api_products(self) -> None:
        user = self._api_user()
        if not user:
            self._send_json({"ok": False, "error": "invalid_api_key"}, status=401)
            return
        rows = CatalogService(self.service.db).list_products()
        products = []
        for p in rows:
            products.append({
                "id": int(p["id"]),
                "category_id": p.get("category_id"),
                "category_name": p.get("category_name"),
                "name": p["name"],
                "description": p.get("description") or "",
                "currency": p["currency"],
                "price_minor": int(p["price_minor"]),
                "price_display": fmt_money(int(p["price_minor"]), p["currency"]),
                "available_stock": int(p.get("available_stock") or 0),
                "is_available": int(p.get("available_stock") or 0) > 0,
            })
        self._send_json({"ok": True, "products": products})

    def _api_purchase(self) -> None:
        user = self._api_user()
        if not user:
            self._send_json({"ok": False, "error": "invalid_api_key"}, status=401)
            return
        try:
            payload = self._read_json_body()
            product_id = int(payload.get("product_id") or 0)
            quantity = int(payload.get("quantity") or 1)
            if product_id <= 0 or quantity <= 0:
                raise ValueError("product_id and quantity must be positive")
            product = self.service.get_product(product_id)
            if not int(product.get("is_active") or 0):
                raise ValueError("product is inactive")
            available = int(product.get("available_stock") or 0)
            if quantity > available:
                self._send_json({"ok": False, "error": "out_of_stock", "available_stock": available}, status=409)
                return
            total = int(product["price_minor"]) * quantity
            balances = WalletService(self.service.db).get_balances(int(user["id"]))
            balance = int(balances.get(product["currency"], 0))
            if balance < total:
                self._send_json({"ok": False, "error": "insufficient_balance", "currency": product["currency"], "required_minor": total, "balance_minor": balance}, status=402)
                return
            idem_key = str(payload.get("idempotency_key") or self.headers.get("Idempotency-Key") or "").strip()
            if idem_key:
                with self.service.db.connect() as conn:
                    row = conn.execute("SELECT response_json FROM buyer_api_idempotency WHERE user_id=? AND idempotency_key=?", (int(user["id"]), idem_key)).fetchone()
                    if row and row["response_json"]:
                        self._send_json(json.loads(row["response_json"]))
                        return
            order_service = OrderService(self.service.db, order_expires_minutes=self.service.order_expires_minutes())
            order = order_service.create_order(user_id=int(user["id"]), product_id=product_id, quantity=quantity)
            result = order_service.pay_with_wallet(int(order["id"]), expected_user_id=int(user["id"]))
            delivery = [str(r["delivered_content"]) for r in result.get("delivery") or []]
            response_payload = {
                "ok": True,
                "order": {
                    "id": int(result["order"]["id"]),
                    "public_code": result["order"]["public_code"],
                    "status": result["order"]["status"],
                    "product_id": product_id,
                    "product_name": result["order"]["product_name"],
                    "quantity": quantity,
                    "currency": result["order"]["currency"],
                    "total_amount_minor": int(result["order"]["total_amount_minor"]),
                    "total_display": fmt_money(int(result["order"]["total_amount_minor"]), result["order"]["currency"]),
                },
                "delivery": delivery,
            }
            if idem_key:
                with self.service.db.transaction() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO buyer_api_idempotency(user_id,idempotency_key,response_json) VALUES(?,?,?)",
                        (int(user["id"]), idem_key, json.dumps(response_payload, ensure_ascii=False)),
                    )
            self._send_json(response_payload)
        except InsufficientFunds:
            self._send_json({"ok": False, "error": "insufficient_balance"}, status=402)
        except OutOfStock:
            self._send_json({"ok": False, "error": "out_of_stock"}, status=409)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/t/api-guide", "/api-guide"}:
            self._send(self._api_guide())
            return
        if parsed.path == "/api/telegram-buyer/products":
            self._api_products()
            return
        if parsed.query:
            query = parse_qs(parsed.query)
            cookies = []
            if "theme" in query:
                cookies.append(f"nimo_theme={query['theme'][0]}; Path=/; SameSite=Lax")
            if "lang" in query:
                cookies.append(f"nimo_lang={query['lang'][0]}; Path=/; SameSite=Lax")
            if cookies and parsed.path in {"/", "/orders", "/products", "/categories", "/stock", "/users", "/wallets", "/finance", "/payments", "/preorders", "/settings", "/audit", "/logs", "/bots", "/notifications", "/backup", "/guide", "/status", "/imports", "/exports", "/reconcile", "/coupons", "/roles", "/deliveries", "/low-stock"}:
                self._redirect(parsed.path, cookies)
                return
        if parsed.path == "/static/style.css":
            self._send(CSS, content_type="text/css; charset=utf-8")
            return
        if parsed.path == "/login":
            self._send(self._login_page())
            return
        if parsed.path == "/logout":
            self._redirect("/login", ["nimo_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"])
            return
        session = self._require_login()
        if not session:
            return
        if not role_can_read(session.role, parsed.path):
            self._send(self._page("dashboard", "", active="/", error="Bạn không có quyền truy cập trang này."), status=403)
            return
        if parsed.path.startswith("/media/products/"):
            rel = parsed.path.lstrip("/")
            file_path = (self.service.project_root / rel).resolve()
            media_root = (self.service.project_root / "media" / "products").resolve()
            if not file_path.is_relative_to(media_root) or not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
            ext = file_path.suffix.lower()
            ctype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(ext, "application/octet-stream")
            self._send(file_path.read_bytes(), content_type=ctype)
            return
        if parsed.path == "/backup/download":
            try:
                include_env = parse_qs(parsed.query).get("include_env", ["0"])[0] == "1"
                backup_path = self.service.create_backup(include_env=include_env, admin_id=session.admin_id)
                data = backup_path.read_bytes()
                self._send(data, content_type="application/zip", headers={"Content-Disposition": f"attachment; filename={backup_path.name}"})
            except Exception as exc:
                self._send(self._page("backup", "", active="/backup", error=str(exc)), status=500)
            return
        if parsed.path == "/exports/download":
            try:
                kind = parse_qs(parsed.query).get("kind", ["orders"])[0]
                filename, data = self.service.export_report(kind)
                self._send(data, content_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={filename}"})
            except Exception as exc:
                self._send(self._page("exports", "", active="/exports", error=str(exc)), status=500)
            return
        try:
            html_body = self._route_get(parsed.path, parse_qs(parsed.query))
            self._send(html_body)
        except Exception as exc:
            self._send(self._page("dashboard", "", active=parsed.path, error=str(exc)), status=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/telegram-buyer/purchase":
            self._api_purchase()
            return
        if parsed.path in {"/webhook/sepay", "/webhook/binance"}:
            try:
                raw = self._read_limited_body(max_bytes=128 * 1024).decode("utf-8")
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), status=413, content_type="application/json; charset=utf-8")
                return
            if not self._verify_webhook_request(raw):
                self._send(json.dumps({"ok": False, "error": "invalid_webhook_signature"}, ensure_ascii=False), status=401, content_type="application/json; charset=utf-8")
                return
            try:
                payload = json.loads(raw) if raw.strip().startswith("{") else {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}
                # Internal payment intents use provider ids "bank" and
                # "binance_pay". The public URL names are /webhook/sepay and
                # /webhook/binance, so map them here before reconciliation.
                provider = "bank" if parsed.path.endswith("sepay") else "binance_pay"
                result = self.service.ingest_webhook_event(
                    provider=provider,
                    tx_id=str(payload.get("tx_id") or payload.get("id") or payload.get("transaction_id") or payload.get("provider_tx_id") or ""),
                    amount=str(payload.get("amount") or payload.get("transferAmount") or payload.get("amountIn") or "0"),
                    currency=str(payload.get("currency") or "VND"),
                    description=str(payload.get("description") or payload.get("content") or payload.get("note") or payload.get("remark") or ""),
                    raw=payload,
                )
                self._send(json.dumps(result, ensure_ascii=False), content_type="application/json; charset=utf-8")
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), status=400, content_type="application/json; charset=utf-8")
            return
        if parsed.path == "/login":
            form = self._read_form()
            admin = self.service.authenticate(form.get("username", ""), form.get("password", ""))
            if not admin:
                self._send(self._login_page("Sai tài khoản hoặc mật khẩu"), status=401)
                return
            token = create_session(self.session_secret, admin_id=int(admin["id"]), username=admin["username"], role=admin["role"])
            secure = "; Secure" if str(os.getenv("WEB_COOKIE_SECURE", "")).lower() in {"1", "true", "yes", "on"} else ""
            self._redirect("/", [f"nimo_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200{secure}"])
            return
        session = self._require_login()
        if not session:
            return
        form = self._read_form()
        if not self._verify_post(form):
            self._send(self._page("dashboard", "", active="/", error="CSRF token không hợp lệ. Hãy tải lại trang."), status=403)
            return
        if not role_can_write(session.role, parsed.path):
            self._send(self._page("dashboard", "", active="/", error="Bạn không có quyền thực hiện thao tác này."), status=403)
            return
        try:
            redirect_to = self._route_post(parsed.path, form, session.admin_id)
            self._redirect(redirect_to)
        except Exception as exc:
            self._send(self._page("dashboard", "", active="/", error=str(exc)), status=400)

    def _route_get(self, path: str, query: dict[str, list[str]]) -> str:
        if path == "/":
            return self._page("dashboard", self._dashboard(), active="/")
        if path == "/categories":
            return self._page("categories", self._categories(), active="/categories")
        if path == "/products":
            return self._page("products", self._products(query), active="/products")
        if path == "/products/preview":
            product_id = int(query.get("id", ["0"])[0] or 0)
            return self._page("products", self._product_preview(product_id), active="/products")
        if path == "/stock":
            return self._page("stock", self._stock(query), active="/stock")
        if path == "/orders":
            return self._page("orders", self._orders(query), active="/orders")
        if path == "/preorders":
            return self._page("preorders", self._preorders(query), active="/preorders")
        if path == "/users":
            return self._page("users", self._users(), active="/users")
        if path == "/wallets":
            return self._page("wallets", self._wallets(), active="/wallets")
        if path == "/finance":
            return self._page("finance", self._finance(), active="/finance")
        if path == "/payments":
            return self._page("payments", self._payments(), active="/payments")
        if path == "/bots":
            return self._page("bots", self._bots(), active="/bots")
        if path == "/notifications":
            return self._page("notifications", self._notifications(), active="/notifications")
        if path == "/backup":
            return self._page("backup", self._backup(), active="/backup")
        if path == "/guide":
            return self._page("guide", self._guide(), active="/guide")
        if path == "/status":
            return self._page("status", self._status_page(), active="/status")
        if path == "/imports":
            return self._page("imports", self._imports_page(), active="/imports")
        if path == "/exports":
            return self._page("exports", self._exports_page(), active="/exports")
        if path == "/reconcile":
            return self._page("reconcile", self._reconcile_page(query), active="/reconcile")
        if path == "/coupons":
            return self._page("coupons", self._coupons_page(), active="/coupons")
        if path == "/roles":
            return self._page("roles", self._roles_page(), active="/roles")
        if path == "/deliveries":
            return self._page("deliveries", self._deliveries_page(), active="/deliveries")
        if path == "/low-stock":
            return self._page("low_stock", self._low_stock_page(query), active="/low-stock")
        if path == "/settings":
            return self._page("settings", self._settings(), active="/settings")
        if path == "/audit":
            return self._page("audit", self._audit(), active="/audit")
        if path == "/logs":
            return self._page("logs", self._logs(), active="/logs")
        self.send_error(404)
        return ""

    def _route_post(self, path: str, form: dict[str, str], admin_id: int) -> str:
        if path == "/categories/create":
            self.service.create_category(form.get("name", ""), int(form.get("sort_order") or 100), category_icon=form.get("category_icon", "📁"), admin_id=admin_id)
            return "/categories"
        if path == "/categories/update":
            self.service.update_category(int(form["id"]), name=form.get("name", ""), category_icon=form.get("category_icon", "📁"), sort_order=int(form.get("sort_order") or 100), is_active=form.get("is_active") == "on", admin_id=admin_id)
            return "/categories"
        if path == "/preorders/cancel":
            self.service.cancel_preorder(int(form["preorder_id"]), admin_id=admin_id)
            return "/preorders"
        if path == "/preorders/fulfill":
            self.service.fulfill_preorder(int(form["preorder_id"]), admin_id=admin_id)
            return "/preorders"
        if path == "/products/create":
            product_id = self.service.create_product(form, admin_id=admin_id)
            image_bytes = form.get("product_image_bytes")
            if isinstance(image_bytes, (bytes, bytearray)) and image_bytes:
                self.service.save_product_image(product_id, filename=str(form.get("product_image_filename") or ""), data=bytes(image_bytes), admin_id=admin_id)
            return "/products"
        if path == "/products/update":
            product_id = int(form["id"])
            self.service.update_product(product_id, form, admin_id=admin_id)
            if str(form.get("clear_product_image") or "").lower() in {"1", "true", "on", "yes"}:
                self.service.clear_product_image(product_id, admin_id=admin_id)
            image_bytes = form.get("product_image_bytes")
            if isinstance(image_bytes, (bytes, bytearray)) and image_bytes:
                self.service.save_product_image(product_id, filename=str(form.get("product_image_filename") or ""), data=bytes(image_bytes), admin_id=admin_id)
            return "/products"
        if path == "/products/delete":
            self.service.delete_product(int(form["id"]), admin_id=admin_id)
            return "/products"
        if path == "/stock/import":
            product_id = int(form["product_id"])
            parser_mode = str(form.get("parser_mode") or "auto")
            file_bytes = form.get("stock_file_bytes")
            file_name = str(form.get("stock_file_filename") or "")
            contents = str(form.get("contents") or "")
            if isinstance(file_bytes, (bytes, bytearray)) and file_bytes:
                self.service.add_stock_upload(product_id, filename=file_name, data=bytes(file_bytes), raw_text=contents, parser_mode=parser_mode, admin_id=admin_id)
            else:
                self.service.add_stock(product_id, contents, parser_mode=parser_mode, admin_id=admin_id)
            return f"/stock?product_id={product_id}"
        if path == "/orders/cancel":
            self.service.cancel_order(int(form["order_id"]), admin_id=admin_id)
            return "/orders"
        if path == "/orders/refund":
            self.service.refund_order(int(form["order_id"]), admin_id=admin_id)
            return "/orders"
        if path == "/wallets/adjust":
            self.service.manual_wallet_adjust(user_ref=form["user_ref"], direction=form["direction"], currency=form["currency"], amount=form["amount"], reason=form.get("reason", "web_adjust"), admin_id=admin_id)
            return "/wallets"
        if path == "/payments/confirm":
            self.service.confirm_payment(payment_code=form["payment_code"], tx_id=form["tx_id"], amount=form["amount"], currency=form["currency"], provider=form["provider"], admin_id=admin_id)
            return "/payments"
        if path == "/bots/create":
            self.service.create_managed_bot(form, admin_id=admin_id)
            return "/bots"
        if path == "/bots/update":
            self.service.update_managed_bot(int(form["id"]), form, admin_id=admin_id)
            return "/bots"
        if path == "/bots/delete":
            self.service.delete_managed_bot(int(form["id"]), admin_id=admin_id)
            return "/bots"
        if path == "/notifications/create":
            pid = int(form["product_id"]) if str(form.get("product_id") or "").isdigit() else None
            self.service.create_notification(title=form.get("title", ""), message=form.get("message", ""), product_id=pid, admin_id=admin_id)
            return "/notifications"
        if path == "/backup/restore":
            self.service.restore_backup(form.get("backup_path", ""), admin_id=admin_id)
            return "/backup"
        if path == "/status/check-token":
            token = form.get("token") or self.service.get_settings().get("BOT_TOKEN", "")
            result = self.service.check_bot_token(token)
            self.service.log(admin_id, "bot.token_check", "bot", "", result)
            return "/status"
        if path == "/imports/catalog":
            self.service.import_catalog_csv(form.get("csv_text", ""), admin_id=admin_id)
            return "/imports"
        if path == "/reconcile/review":
            self.service.mark_payment_event_reviewed(int(form["event_id"]), form.get("note", ""), admin_id=admin_id)
            return "/reconcile"
        if path == "/coupons/create":
            self.service.create_coupon(form, admin_id=admin_id)
            return "/coupons"
        if path == "/coupons/update":
            self.service.update_coupon(int(form["id"]), form, admin_id=admin_id)
            return "/coupons"
        if path == "/coupons/delete":
            self.service.delete_coupon(int(form["id"]), admin_id=admin_id)
            return "/coupons"
        if path == "/roles/create":
            self.service.create_admin_account(username=form.get("username", ""), password=form.get("password", ""), role=form.get("role", "viewer"), admin_id=admin_id)
            return "/roles"
        if path == "/roles/update":
            self.service.update_admin_account(int(form["id"]), role=form.get("role", "viewer"), is_active=form.get("is_active") == "on", password=form.get("password", ""), admin_id=admin_id)
            return "/roles"
        if path == "/low-stock/notify":
            self.service.queue_low_stock_notifications(int(form.get("threshold") or 5), admin_id=admin_id)
            return "/low-stock"
        if path == "/settings":
            values = {key: form.get(key, "") for key in DEFAULT_SETTING_KEYS}
            self.service.update_settings(values, admin_id=admin_id, write_env=form.get("write_env") == "on")
            return "/settings"
        raise ValueError("unknown action")

    def _dashboard(self) -> str:
        data = self.service.dashboard()
        c = data["counts"]
        metric_names = {
            "users": "Người dùng", "products": "Sản phẩm đang bán", "available_stock": "Hàng còn",
            "pending_orders": "Đơn chờ", "delivered_orders": "Đã giao", "unmatched_payments": "GD cần đối soát",
        }
        metrics = "".join(f'<div class="card"><div class="muted">{esc(metric_names.get(k,k))}</div><div class="metric">{esc(v)}</div></div>' for k, v in c.items())
        audit = data["audit"]
        audit_html = '<span class="status delivered">Hệ thống sạch</span><p class="muted">Không phát hiện lệch ví, kho, đơn hoặc dòng tiền.</p>' if not audit else "".join(f'<div class="alert err">{esc(i.code)}: {esc(i.message)}</div>' for i in audit)
        orders = self._orders_table(data["recent_orders"], compact=True)
        return f'<div class="grid">{metrics}</div><div class="grid2"><div class="card"><h3>Kiểm tra nhanh</h3>{audit_html}</div><div class="card"><h3>Đơn gần đây</h3>{orders}</div></div>'

    def _categories(self) -> str:
        rows = self.service.list_categories()
        table_rows = "".join(
            f'<tr><form method="post" action="/categories/update">{self._form_csrf()}<input type="hidden" name="id" value="{r["id"]}"><td>{r["id"]}</td><td><input name="category_icon" value="{esc(r.get("category_icon") or "📁")}" placeholder="🤖" style="max-width:90px"></td><td><input name="name" value="{esc(r["name"])}"><div class="help">Bot sẽ hiện: {esc(r.get("category_icon") or "📁")} {esc(r["name"])} · còn {int(r.get("available_stock") or 0)}</div></td><td><input name="sort_order" value="{r["sort_order"]}"></td><td><span class="pill">Còn {int(r.get("available_stock") or 0)}</span><br><span class="muted">SP: {int(r.get("active_products") or 0)}</span></td><td><input type="checkbox" name="is_active" {"checked" if r["is_active"] else ""}></td><td><button class="small">Lưu</button></td></form></tr>'
            for r in rows
        )
        return f'<div class="card"><h3>Tạo danh mục</h3><p class="muted">Danh mục giúp khách chọn nhóm sản phẩm như ChatGPT, Gemini, Canva, CapCut. Icon hiển thị ngoài bot; trạng thái xanh/đỏ sẽ tự theo tồn kho.</p><form method="post" action="/categories/create" class="form-grid">{self._form_csrf()}<label>Icon danh mục<input name="category_icon" value="📁" placeholder="🤖 / ▶️ / 🟣"></label><label>Tên danh mục<input name="name" placeholder="Ví dụ: ChatGPT" required></label><label>Thứ tự hiển thị<input name="sort_order" value="100"></label><button>{tr(self._theme_lang()[0],"create")}</button></form></div><div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Icon</th><th>Tên</th><th>Thứ tự</th><th>Kho</th><th>Đang bật</th><th></th></tr>{table_rows}</table></div>'

    def _category_options(self, current: Any = "") -> str:
        categories = self.service.list_categories()
        return '<option value="">Không có</option>' + "".join(f'<option value="{c["id"]}" {selected(current, c["id"])}>{esc(c["name"])}</option>' for c in categories)

    def _stock_format_options(self, current: Any = "auto") -> str:
        cur = str(current or "auto")
        return "".join(f'<option value="{key}" {selected(cur, key)}>{esc(str(meta["name"]))}</option>' for key, meta in STOCK_FORMATS.items())

    def _products(self, query: dict[str, list[str]]) -> str:
        if query.get("new", [""])[0]:
            return self._product_form(None)
        edit_id = query.get("edit", [""])[0]
        if edit_id:
            return self._product_form(self.service.get_product(int(edit_id)))
        products = self.service.list_products()
        rows = []
        for p in products:
            is_active = bool(p["is_active"])
            status = '<span class="status active">Đang bán</span>' if is_active else '<span class="status inactive">Đã ẩn</span>'
            delete_label = "Xóa" if int(p.get("sold_stock") or 0) == 0 else "Ẩn"
            thumb = self._product_thumb(p, size=48)
            icon = esc(p.get("product_icon") or "📦")
            row = (
                f'<tr><td><div style="display:flex;gap:10px;align-items:center">{thumb}<div>'
                f'<div class="product-title">{icon} {esc(p["name"])}</div>'
                f'<div class="muted">ID #{p["id"]} · {esc(p.get("category_name") or "Chưa có danh mục")}</div></div></div></td>'
                f'<td>{money(p["price_minor"], p["currency"])}<br><span class="muted">Vốn: {money(p["cost_minor"], p["currency"])}</span></td>'
                f'<td><span class="pill">Còn {p["available_stock"]}</span> <span class="pill">Giữ {p["reserved_stock"]}</span> <span class="pill">Bán {p["sold_stock"]}</span></td>'
                f'<td>{status}</td><td><div class="action-row">'
                f'<a class="btn small secondary" href="/products?edit={p["id"]}">Sửa</a>'
                f'<a class="btn small ghost" href="/products/preview?id={p["id"]}">Preview</a>'
                f'<form method="post" action="/products/delete" style="display:inline" onsubmit="return confirm(\'Bạn chắc chắn muốn xóa/ẩn sản phẩm này?\');">'
                f'{self._form_csrf()}<input type="hidden" name="id" value="{p["id"]}"><button class="small danger">{delete_label}</button></form>'
                f'<a class="btn small ghost" href="/stock?product_id={p["id"]}">Nhập kho</a></div></td></tr>'
            )
            rows.append(row)
        empty = '<tr><td colspan="5"><div class="alert">Chưa có sản phẩm. Bấm “Thêm sản phẩm” để tạo sản phẩm đầu tiên.</div></td></tr>' if not rows else ""
        return (
            '<div class="card"><div class="section-head"><div><h2>Danh sách sản phẩm</h2>'
            '<p class="muted">Bấm Thêm/Sửa để upload ảnh, icon, custom emoji ID và cấu hình cách khách nhìn thấy sản phẩm trên bot.</p></div>'
            '<a class="btn" href="/products?new=1">＋ Thêm sản phẩm</a></div></div>'
            f'<div class="card table-wrap"><table class="premium-table"><tr><th>Sản phẩm</th><th>Giá</th><th>Kho</th><th>Trạng thái</th><th>Thao tác</th></tr>{empty}{"".join(rows)}</table></div>'
        )

    def _media_url(self, product: dict) -> str:
        rel = str(product.get("product_image_path") or "").strip()
        if not rel or not rel.startswith("media/products/"):
            return ""
        return "/" + rel

    def _product_thumb(self, product: dict, *, size: int = 56) -> str:
        url = self._media_url(product)
        if not url:
            icon = esc(product.get("product_icon") or "📦")
            return f'<div style="width:{size}px;height:{size}px;border-radius:14px;display:grid;place-items:center;background:var(--panel2);font-size:24px">{icon}</div>'
        return f'<img src="{esc(url)}" alt="Ảnh sản phẩm" style="width:{size}px;height:{size}px;object-fit:cover;border-radius:14px;border:1px solid var(--line)">'

    def _product_form(self, product: dict | None = None) -> str:
        is_edit = product is not None
        action = "/products/update" if is_edit else "/products/create"
        title = tr(self._theme_lang()[0], "edit_product") if is_edit else tr(self._theme_lang()[0], "add_product")
        product = product or {
            "id": "", "category_id": "", "name": "", "currency": "VND", "price_minor": 0, "cost_minor": 0,
            "description": "", "warranty_text": "", "is_active": 1, "stock_format": "auto", "stock_format_labels": "",
            "stock_format_example": "", "delivery_format": "auto", "product_icon": "📦", "product_custom_emoji_id": "",
            "product_image_path": "", "product_image_file_id": "", "product_short_description": "", "product_long_description": "",
        }
        active = bool(product.get("is_active", 1))
        hidden_id = f'<input type="hidden" name="id" value="{product["id"]}">' if is_edit else ""
        preview_link = f'<a class="btn ghost" href="/products/preview?id={product["id"]}">👁 Xem trước</a>' if is_edit else ""
        fmt_options = ''.join(f'<option value="{key}" {selected(product.get("stock_format"), key)}>{esc(meta["name"])}</option>' for key, meta in STOCK_FORMATS.items())
        delivery_options = ''.join([
            f'<option value="auto" {selected(product.get("delivery_format"), "auto")}>Tự động theo định dạng kho</option>',
            f'<option value="raw" {selected(product.get("delivery_format"), "raw")}>Giao nguyên dòng</option>',
            f'<option value="labeled" {selected(product.get("delivery_format"), "labeled")}>Giao có nhãn cột</option>',
        ])
        image_block = ""
        if is_edit:
            if product.get("product_image_path"):
                image_block = (
                    f'<div class="info-box full"><b>Ảnh hiện tại</b><br>{self._product_thumb(product, size=120)}'
                    f'<br><code>{esc(product.get("product_image_path"))}</code><br>'
                    '<label style="margin-top:10px"><input type="checkbox" name="clear_product_image" value="1" style="width:auto;margin-right:7px"> Xóa ảnh sản phẩm hiện tại</label></div>'
                )
            else:
                image_block = '<div class="info-box full">Sản phẩm này chưa có ảnh. Upload ảnh JPG/PNG/WebP để bot hiển thị card sản phẩm đẹp hơn.</div>'
        notify = '<label class="full"><input type="checkbox" name="notify_users" value="1" style="width:auto;margin-right:8px"> Gửi thông báo cập nhật sản phẩm trên bot</label>' if is_edit else ''
        return f'''<div class="card"><div class="section-head"><div><h2>{title}</h2><p class="muted">Nhập thông tin khách sẽ nhìn thấy khi mua. Icon/ảnh giúp danh sách sản phẩm nhìn giống shop premium.</p></div><div class="action-row"><a class="btn secondary" href="/products">← Quay lại danh sách</a>{preview_link}</div></div>
        <form method="post" action="{action}" class="form-grid" enctype="multipart/form-data">{self._form_csrf()}{hidden_id}
        <label>Danh mục<select name="category_id">{self._category_options(product.get("category_id") or "")}</select><div class="help">Chọn nhóm sản phẩm hiển thị trong menu Mua ngay.</div></label>
        <label>Tên sản phẩm<input name="name" value="{esc(product.get("name"))}" placeholder="ChatGPT Plus 1 tháng" required><div class="help">Tên càng rõ càng ít khách hỏi lại.</div></label>
        <label>Icon thường<input name="product_icon" value="{esc(product.get("product_icon") or "")}" placeholder="🤖 / ▶️ / 🟣 / 🔑"><div class="help">Dùng trong nút danh sách sản phẩm. Dùng emoji thường là ổn định nhất.</div></label>
        <label>Custom Emoji ID<input name="product_custom_emoji_id" value="{esc(product.get("product_custom_emoji_id") or "")}" placeholder="5368324170671202286"><div class="help">Dành cho bot/account Premium. Hiển thị trong mô tả bằng tag tg-emoji nếu Telegram hỗ trợ.</div></label>
        <label>Tiền tệ<select name="currency"><option {selected(product.get("currency"),"VND")}>VND</option><option {selected(product.get("currency"),"USDT")}>USDT</option><option {selected(product.get("currency"),"USD")}>USD</option></select></label>
        <label>Giá bán<input name="price" inputmode="decimal" value="{amount_input(product.get("price_minor"), product.get("currency") or "VND")}" placeholder="150000" required><div class="help">VND nhập số tiền thường, ví dụ 150000.</div></label>
        <label>Giá vốn<input name="cost" inputmode="decimal" value="{amount_input(product.get("cost_minor"), product.get("currency") or "VND")}" placeholder="100000"><div class="help">Dùng để tính lợi nhuận, có thể để 0.</div></label>
        <label>Trạng thái<select name="is_active"><option value="1" {"selected" if active else ""}>Đang bán</option><option value="0" {"selected" if not active else ""}>Tạm ẩn</option></select></label>
        <label class="full">Ảnh sản phẩm<input type="file" name="product_image" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"><div class="help">Chỉ JPG/PNG/WebP, tối đa 5MB. Bot sẽ dùng ảnh này ở màn chi tiết sản phẩm và lưu Telegram file_id sau lần gửi đầu.</div></label>
        {image_block}
        <label class="full">Mô tả ngắn<input name="product_short_description" value="{esc(product.get("product_short_description") or "")}" placeholder="Dùng 30 ngày, bảo hành 24h"><div class="help">Hiện trong card chi tiết và preview.</div></label>
        <label class="full">Mô tả chi tiết<textarea name="product_long_description" placeholder="Mô tả quyền lợi, điều kiện bảo hành, cách sử dụng sau mua...">{esc(product.get("product_long_description") or "")}</textarea><div class="help">Có thể để trống, bot sẽ dùng mô tả cũ bên dưới.</div></label>
        <label class="full">Mô tả cũ / ghi chú sản phẩm<textarea name="description" placeholder="Tài khoản dùng 30 ngày, không đổi email...">{esc(product.get("description") or "")}</textarea></label>
        <label class="full">Bảo hành<textarea name="warranty_text" placeholder="1 đổi 1 trong 24h nếu lỗi đăng nhập">{esc(product.get("warranty_text") or "")}</textarea></label>
        <label>Định dạng dữ liệu kho<select name="stock_format">{fmt_options}</select><div class="help">Mỗi sản phẩm có thể có format riêng: Email|Pass|2FA, Email / Pass, UID|Pass|Cookie|Token...</div></label>
        <label>Kiểu giao hàng<select name="delivery_format">{delivery_options}</select><div class="help">Giao có nhãn giúp file giao hàng dễ đọc hơn.</div></label>
        <label class="full">Nhãn cột dữ liệu<input name="stock_format_labels" value="{esc(product.get("stock_format_labels") or "")}" placeholder="Email|Mật khẩu|2FA"><div class="help">Dùng khi giao có nhãn. Cách nhau bằng |.</div></label>
        <label class="full">Ví dụ nhập kho<input name="stock_format_example" value="{esc(product.get("stock_format_example") or "")}" placeholder="email@example.com|password|2FA"></label>
        {notify}
        <button>{tr(self._theme_lang()[0],"save")}</button></form></div>'''

    def _product_preview(self, product_id: int) -> str:
        p = self.service.get_product(product_id)
        icon = esc(p.get("product_icon") or "📦")
        custom = esc(p.get("product_custom_emoji_id") or "")
        img = self._product_thumb(p, size=220)
        short = esc(p.get("product_short_description") or p.get("description") or "Chưa có mô tả ngắn")
        long = esc(p.get("product_long_description") or p.get("description") or "Chưa có mô tả chi tiết").replace("\n", "<br>")
        return f'''<div class="card"><div class="section-head"><div><h2>👁 Preview sản phẩm trên bot</h2><p class="muted">Đây là bản xem trước gần giống card mà khách thấy khi bấm vào sản phẩm.</p></div><div class="action-row"><a class="btn secondary" href="/products?edit={p["id"]}">Sửa sản phẩm</a><a class="btn ghost" href="/products">Danh sách</a></div></div>
        <div class="grid2"><div class="card" style="max-width:460px">{img}<h2>{icon} {esc(p["name"])}</h2><p><b>Giá:</b> {money(p["price_minor"], p["currency"])} · <b>Tồn:</b> {int(p.get("available_stock") or 0)}</p><p>{short}</p><hr><p>{long}</p><p><b>Bảo hành:</b> {esc(p.get("warranty_text") or "Theo chính sách shop")}</p><p class="muted">Custom emoji ID: <code>{custom or "chưa đặt"}</code><br>Image path: <code>{esc(p.get("product_image_path") or "chưa có")}</code><br>Telegram file_id: <code>{esc(p.get("product_image_file_id") or "chưa có")}</code></p></div><div class="card"><h3>Gợi ý hiển thị trong danh sách</h3><p><code>{icon} {esc(p["name"])} | {money(p["price_minor"], p["currency"])} | 📦 {int(p.get("available_stock") or 0)}</code></p><p class="muted">Ảnh thật chỉ hiện ở màn chi tiết sản phẩm, không nhét trực tiếp vào từng nút danh sách để tránh nặng và loạn chat.</p></div></div></div>'''

    def _stock(self, query: dict[str, list[str]]) -> str:
        products = self.service.list_products()
        selected_product = query.get("product_id", [""])[0]
        opts = "".join(f'<option value="{p["id"]}" {selected(selected_product, p["id"])}>{esc(p["name"])} · còn {p["available_stock"]}</option>' for p in products)
        product_profile = {}
        profile_box = ""
        if selected_product.isdigit():
            product_profile = self.service.product_stock_format(int(selected_product))
            fmt = str(product_profile.get("stock_format") or "auto")
            fmt_name = str(STOCK_FORMATS.get(fmt, STOCK_FORMATS["auto"])["name"])
            labels = product_profile.get("stock_format_labels") or "|".join(STOCK_FORMATS.get(fmt, STOCK_FORMATS["auto"])["labels"])
            example = product_profile.get("stock_format_example") or STOCK_FORMATS.get(fmt, STOCK_FORMATS["auto"])["example"]
            profile_box = f'<div class="full info-box"><b>Định dạng của sản phẩm này:</b> {esc(fmt_name)}<br><b>Nhãn cột:</b> <code>{esc(labels)}</code><br><b>Ví dụ:</b> <code>{esc(example)}</code><br><span class="muted">Khi chọn Theo cấu hình sản phẩm, hệ thống dùng đúng kiểu này để nhận diện và giao hàng cho khách.</span></div>'
        mode_opts = ''.join(f'<option value="{key}">{esc(str(meta["name"]))}</option>' for key, meta in STOCK_FORMATS.items())
        form = f'''<div class="card"><div class="section-head"><div><h3>Nhập kho key/tài khoản</h3><p class="muted">Mỗi sản phẩm có thể có định dạng riêng. Ví dụ ChatGPT có thể là Email|Pass|2FA, sản phẩm khác là Email / Pass, clone Facebook là UID|Pass|Cookie|Token. Hệ thống sẽ lưu mỗi dòng thành một hàng giao riêng.</p></div><a class="btn secondary" href="/imports">Import nhiều sản phẩm</a></div><form method="post" action="/stock/import" class="form-grid" enctype="multipart/form-data">{self._form_csrf()}<label>Sản phẩm<select name="product_id">{opts}</select><div class="help">Chọn đúng sản phẩm trước khi nhập kho. Mỗi sản phẩm có thể có định dạng dữ liệu riêng trong phần Sửa sản phẩm.</div></label><label>Kiểu nhận diện<select name="parser_mode"><option value="product">Theo cấu hình sản phẩm</option><option value="auto">Tự nhận diện</option>{mode_opts}</select><div class="help">Khuyên dùng Theo cấu hình sản phẩm. Nếu sản phẩm chưa cấu hình, chọn kiểu phù hợp với file đang nhập.</div></label>{profile_box}<label class="full">Tải file dữ liệu<input type="file" name="stock_file" accept=".txt,.csv,.docx,text/plain,text/csv,application/vnd.openxmlformats-officedocument.wordprocessingml.document"><div class="help">Dùng khi có 100, 1000 hoặc 10000 tài khoản. File .txt/.csv là nhẹ nhất; .docx dùng được nếu bạn soạn trong Word.</div></label><label class="full">Hoặc dán trực tiếp danh sách key/account<textarea name="contents" placeholder="Ví dụ 1: email@gmail.com|MatKhau|2FA&#10;Ví dụ 2: email@gmail.com / 111111&#10;Ví dụ 3: UID|MatKhau|Cookie|Token"></textarea><div class="help">Mỗi dòng là một hàng giao. Không nhập trùng trong cùng sản phẩm; nếu trùng hệ thống sẽ báo lỗi để tránh bán trùng.</div></label><div class="full info-box"><b>Cách xử lý dữ liệu:</b><br>• Email|Pass|2FA → lưu thành 1 dòng, giao có nhãn Email/Mật khẩu/2FA nếu sản phẩm bật Giao có nhãn.<br>• Email / Pass → tự chuẩn hóa thành Email|Pass.<br>• UID|Pass|Cookie|Token → giữ đủ 4 trường, preview/log che cookie/token, giao file vẫn đủ dữ liệu cho khách.<br>• Key/license/link mỗi dòng → giữ nguyên từng dòng.</div><button>{tr(self._theme_lang()[0],"import_stock")}</button></form></div>'''
        items = self.service.list_stock_items(product_id=int(selected_product) if selected_product.isdigit() else None, status=query.get("status", [""])[0] or None)
        rows = "".join(f'<tr><td>{i["id"]}</td><td>{esc(i["product_name"])}</td><td>{status_badge(i["status"])}</td><td><code>{esc(i["content"])}</code></td><td>{esc(i["created_at"])}</td></tr>' for i in items)
        return form + '<div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Sản phẩm</th><th>Trạng thái</th><th>Nội dung</th><th>Ngày nhập</th></tr>' + rows + '</table></div>'

    def _orders_table(self, orders: list[dict], compact: bool = False) -> str:
        actions = "" if compact else "<th>Thao tác</th>"
        rows = []
        for o in orders:
            buttons = ""
            if not compact:
                buttons = f'''<td><div class="action-row"><form method="post" action="/orders/cancel">{self._form_csrf()}<input type="hidden" name="order_id" value="{o["id"]}"><button class="small secondary">Hủy</button></form><form method="post" action="/orders/refund">{self._form_csrf()}<input type="hidden" name="order_id" value="{o["id"]}"><button class="small danger">Hoàn tiền</button></form></div></td>'''
            rows.append(f'<tr><td>#{o["id"]}<br><span class="muted">{esc(o["public_code"])}</span></td><td>{esc(o.get("username") or o.get("telegram_id"))}</td><td>{esc(o["product_name"])}</td><td>{money(o["total_amount_minor"], o["currency"])}</td><td>{status_badge(o["status"])}</td><td>{esc(o["created_at"])}</td>{buttons}</tr>')
        return '<div class="table-wrap"><table class="premium-table"><tr><th>Đơn</th><th>Khách</th><th>Sản phẩm</th><th>Tiền</th><th>TT</th><th>Ngày</th>' + actions + '</tr>' + "".join(rows) + '</table></div>'

    def _orders(self, query: dict[str, list[str]]) -> str:
        status = query.get("status", [""])[0] or None
        filters = '<div class="toolbar"><a class="btn secondary small" href="/orders">Tất cả</a><a class="btn secondary small" href="/orders?status=awaiting_payment">Chờ thanh toán</a><a class="btn secondary small" href="/orders?status=delivered">Đã giao</a><a class="btn secondary small" href="/orders?status=cancelled">Đã hủy</a><a class="btn secondary small" href="/orders?status=refunded">Đã hoàn tiền</a></div>'
        return '<div class="card">' + filters + self._orders_table(self.service.list_orders(status=status, limit=200)) + '</div>'

    def _preorders(self, query: dict[str, list[str]]) -> str:
        status = query.get("status", [""])[0] or None
        filters = '<div class="toolbar"><a class="btn secondary small" href="/preorders">Tất cả</a><a class="btn secondary small" href="/preorders?status=awaiting_deposit">Chờ cọc</a><a class="btn secondary small" href="/preorders?status=active">Đang đặt</a><a class="btn secondary small" href="/preorders?status=fulfilled">Đã xử lý</a><a class="btn secondary small" href="/preorders?status=cancelled">Đã hủy</a></div>'
        rows = []
        for pr in self.service.list_preorders(status=status, limit=300):
            actions = ''
            if pr["status"] in {"awaiting_deposit", "active"}:
                actions = (
                    f'<div class="action-row"><form method="post" action="/preorders/fulfill">{self._form_csrf()}'
                    f'<input type="hidden" name="preorder_id" value="{pr["id"]}"><button class="small secondary">Đánh dấu đã xử lý</button></form>'
                    f'<form method="post" action="/preorders/cancel" onsubmit="return confirm(\'Hủy đơn đặt trước này?\');">{self._form_csrf()}'
                    f'<input type="hidden" name="preorder_id" value="{pr["id"]}"><button class="small danger">Hủy</button></form></div>'
                )
            rows.append(
                f'<tr><td>#{pr["id"]}<br><code>{esc(pr["public_code"])}</code></td>'
                f'<td>{esc(pr.get("username") or pr.get("telegram_id"))}</td>'
                f'<td>{esc(pr["product_name"])}<br><span class="muted">SL: {int(pr["quantity"])} · Cọc {int(pr["deposit_percent"])}%</span></td>'
                f'<td>{money(pr["deposit_amount_minor"], pr["currency"])}<br><span class="muted">Tổng dự kiến: {money(pr["total_amount_minor"], pr["currency"])}</span></td>'
                f'<td>{status_badge(pr["status"])}</td><td>{esc(pr["created_at"])}</td><td>{actions}</td></tr>'
            )
        empty = '<tr><td colspan="7"><div class="alert">Chưa có đơn đặt trước.</div></td></tr>' if not rows else ''
        return '<div class="card"><div class="section-head"><div><h2>🧾 Đơn đặt trước</h2><p class="muted">Khách đặt trước khi sản phẩm hết hàng. Phí đặt trước chỉnh tại Cấu hình → Giao hàng cho khách → Phí đặt trước (%).</p></div></div>' + filters + '</div><div class="card table-wrap"><table class="premium-table"><tr><th>Mã</th><th>Khách</th><th>Sản phẩm</th><th>Tiền cọc</th><th>Trạng thái</th><th>Ngày</th><th>Thao tác</th></tr>' + empty + ''.join(rows) + '</table></div>'

    def _users(self) -> str:
        rows = "".join(f'<tr><td>{u["id"]}</td><td>{esc(u["telegram_id"])}</td><td>@{esc(u["username"] or "")}</td><td>{esc(u["full_name"] or "")}</td><td>{u["order_count"]}</td><td>{money(u["spent_minor"], "VND")}</td><td>{esc(u["created_at"])}</td></tr>' for u in self.service.list_users())
        return '<div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Telegram</th><th>Username</th><th>Tên</th><th>Đơn</th><th>Đã mua</th><th>Ngày</th></tr>' + rows + '</table></div>'

    def _wallets(self) -> str:
        rows = "".join(f'<tr><td>{w["user_id"]}</td><td>@{esc(w["username"] or "")}</td><td>{esc(w["currency"])}</td><td>{money(w["balance_minor"], w["currency"])}</td><td>{esc(w["updated_at"])}</td></tr>' for w in self.service.list_wallets())
        form = f'<div class="card"><h3>Cộng/trừ ví thủ công</h3><p class="muted">Có thể nhập Telegram ID khách, @username hoặc ID nội bộ. Nếu khách chưa bấm /start, cộng ví bằng Telegram ID sẽ tự tạo hồ sơ tối thiểu để không lỗi FOREIGN KEY.</p><form method="post" action="/wallets/adjust" class="form-grid">{self._form_csrf()}<label>Người dùng<input name="user_ref" required placeholder="Telegram ID / @username / ID nội bộ"><div class="help">Khuyến nghị dán Telegram ID khách từ hồ sơ/bot, không cần tìm ID nội bộ.</div></label><label>Loại<select name="direction"><option value="credit">Cộng tiền</option><option value="debit">Trừ tiền</option></select></label><label>Tiền tệ<select name="currency"><option>VND</option><option>USDT</option><option>USD</option></select></label><label>Số tiền<input name="amount" required placeholder="100000"><div class="help">VND nhập 100000; USDT/USD có thể nhập 10.5.</div></label><label class="full">Lý do<input name="reason" value="web_admin_adjust"></label><button>Lưu thay đổi ví</button></form></div>'
        return form + '<div class="card table-wrap"><table class="premium-table"><tr><th>User ID</th><th>Username</th><th>Tiền tệ</th><th>Số dư</th><th>Cập nhật</th></tr>' + rows + '</table></div>'

    def _finance(self) -> str:
        s = self.service.dashboard()["finance"]
        cash_rows = "".join(f'<tr><td>{esc(r["currency"])}</td><td>{esc(r["provider"])}</td><td>{esc(r["direction"])}</td><td>{money(r["amount_minor"], r["currency"])}</td><td>{money(r["fee_minor"], r["currency"])}</td><td>{r["count"]}</td></tr>' for r in s["cash"])
        wallet_rows = "".join(f'<tr><td>{esc(r["currency"])}</td><td>{money(r["liability_minor"], r["currency"])}</td><td>{r["wallets"]}</td></tr>' for r in s["wallet_liabilities"])
        sales_rows = "".join(f'<tr><td>{esc(r["currency"])}</td><td>{money(r["revenue_minor"], r["currency"])}</td><td>{money(r["cost_minor"], r["currency"])}</td><td>{money(int(r["revenue_minor"] or 0)-int(r["cost_minor"] or 0), r["currency"])}</td><td>{r["orders"]}</td></tr>' for r in s["sales"])
        return f'<div class="grid2"><div class="card"><h3>Sổ dòng tiền</h3><table><tr><th>Tiền</th><th>Cổng</th><th>Hướng</th><th>Số tiền</th><th>Phí</th><th>Số GD</th></tr>{cash_rows}</table></div><div class="card"><h3>Tiền đang nằm trong ví khách</h3><table><tr><th>Tiền</th><th>Tổng ví</th><th>Số ví</th></tr>{wallet_rows}</table></div></div><div class="card"><h3>Doanh thu / lãi gộp</h3><table><tr><th>Tiền</th><th>Doanh thu</th><th>Giá vốn</th><th>Lãi gộp</th><th>Đơn</th></tr>{sales_rows}</table></div>'

    def _payments(self) -> str:
        form = f'<div class="card"><h3>Xác nhận thanh toán thủ công</h3><p class="muted">Dùng khi khách đã chuyển khoản nhưng SePay/Binance chưa tự nhận. Nhập đúng mã ORD... hoặc NAP...</p><form method="post" action="/payments/confirm" class="form-grid">{self._form_csrf()}<label>Mã thanh toán<input name="payment_code" placeholder="ORD... / NAP..." required></label><label>Mã giao dịch / TX ID<input name="tx_id" required placeholder="Mã duy nhất, không nhập trùng"></label><label>Số tiền<input name="amount" required placeholder="150000"></label><label>Tiền tệ<select name="currency"><option>VND</option><option>USDT</option><option>USD</option></select></label><label>Cổng thanh toán<input name="provider" value="bank" placeholder="bank / sepay / binance"></label><button>{tr(self._theme_lang()[0],"confirm_payment")}</button></form></div>'
        events = "".join(f'<tr><td>{e["id"]}</td><td>{esc(e["provider"])}</td><td>{esc(e["provider_tx_id"])}</td><td>{esc(e["payment_code"])}</td><td>{money(e["amount_minor"], e["currency"])}</td><td>{status_badge(e["status"])}</td><td>{esc(e["created_at"])}</td></tr>' for e in self.service.list_payment_events())
        intents = "".join(f'<tr><td>{i["id"]}</td><td>{esc(i["public_code"])}</td><td>{esc(i["provider"])}</td><td>{money(i["amount_minor"], i["currency"])}</td><td>{status_badge(i["status"])}</td><td>{esc(i["created_at"])}</td></tr>' for i in self.service.list_payment_intents())
        return form + f'<div class="grid2"><div class="card"><h3>Mã thanh toán đã tạo</h3><table><tr><th>ID</th><th>Mã</th><th>Cổng</th><th>Tiền</th><th>Trạng thái</th><th>Ngày</th></tr>{intents}</table></div><div class="card"><h3>Giao dịch nhận từ cổng thanh toán</h3><table><tr><th>ID</th><th>Cổng</th><th>TX</th><th>Mã</th><th>Tiền</th><th>Trạng thái</th><th>Ngày</th></tr>{events}</table></div></div>'

    def _setting_input(self, key: str, item: dict[str, Any]) -> str:
        meta = SETTING_META.get(key, {"label": key, "help": "", "placeholder": ""})
        is_secret = bool(item.get("is_secret"))
        value = str(item.get("value") or "")
        label = esc(meta["label"])
        help_text = esc(meta.get("help", ""))
        placeholder = esc(meta.get("placeholder", ""))
        if key.endswith("ENABLED"):
            return f'''<div class="setting-field"><label>{label}<select name="{key}"><option value="false" {selected(value,"false")}>Tắt</option><option value="true" {selected(value,"true")}>Bật</option></select></label><div class="help">{help_text}</div></div>'''
        if key in {"WEB_DEFAULT_LANGUAGE"}:
            return f'''<div class="setting-field"><label>{label}<select name="{key}"><option value="vi" {selected(value,"vi")}>Tiếng Việt</option><option value="en" {selected(value,"en")}>English</option></select></label><div class="help">{help_text}</div></div>'''
        if key in {"WEB_DEFAULT_THEME"}:
            return f'''<div class="setting-field"><label>{label}<select name="{key}"><option value="light" {selected(value,"light")}>Sáng</option><option value="dark" {selected(value,"dark")}>Tối</option></select></label><div class="help">{help_text}</div></div>'''
        if key == "DELIVERY_OUTPUT_MODE":
            return f'''<div class="setting-field full"><label>{label}<select name="{key}">
                <option value="auto" {selected(value,"auto")}>Tự động: đơn nhỏ hiện chat, đơn lớn gửi file</option>
                <option value="file_only" {selected(value,"file_only")}>Luôn gửi file TXT cho mọi đơn</option>
                <option value="inline_and_file" {selected(value,"inline_and_file")}>Hiện trong chat và gửi kèm file</option>
            </select></label><div class="help">{help_text}</div></div>'''
        shown_value = "" if is_secret else esc(value)
        secret_note = "<div class=\"help\">Đã có giá trị cũ. Để trống nếu không muốn đổi.</div>" if is_secret and value else ""
        klass = "setting-field secret" if is_secret else "setting-field"
        typ = "password" if is_secret else "text"
        return f'''<div class="{klass}"><label>{label}<input type="{typ}" name="{key}" value="{shown_value}" placeholder="{placeholder}"></label><div class="help">{help_text}</div>{secret_note}<div class="help"><code>{key}</code></div></div>'''

    def _settings(self) -> str:
        settings = self.service.get_settings()
        groups = []
        for idx, group in enumerate(SETTING_GROUPS):
            fields = "".join(self._setting_input(key, settings.get(key, {"value": DEFAULT_SETTING_KEYS[key][0], "is_secret": DEFAULT_SETTING_KEYS[key][1]})) for key in group["keys"])
            open_attr = " open" if idx == 0 else ""
            groups.append(f'''<details class="setup-section"{open_attr}><summary><span>{esc(group["title"])}</span><span class="muted">Mở/đóng</span></summary><div class="setup-content"><p class="muted">{esc(group["desc"])}</p><div class="form-grid">{fields}</div></div></details>''')
        return f'''<div class="hint-box"><b>Hướng dẫn cấu hình:</b><br>1) Nhập Bot Token và Telegram ID admin trước. 2) Nếu dùng ngân hàng, bật Bank và nhập Bank BIN/Số tài khoản/Chủ tài khoản/SePay API key. 3) Trong mục <b>Giao hàng cho khách</b>, chọn đơn nhỏ hiện trực tiếp hay mọi đơn đều gửi file. 4) Tick “Ghi ra file .env”. 5) Lưu xong restart bot/web để áp dụng biến môi trường.</div><div class="card"><form method="post" action="/settings">{self._form_csrf()}{"".join(groups)}<label style="display:flex;gap:10px;align-items:center;font-weight:800"><input style="width:auto;margin:0" type="checkbox" name="write_env"> Ghi ra file .env để áp dụng sau khi restart bot/web</label><br><button>{tr(self._theme_lang()[0],"save")}</button></form></div>'''

    def _bots(self) -> str:
        rows = []
        for b in self.service.list_managed_bots():
            primary = '<span class="status delivered">Bot chính</span>' if b["is_primary"] else '<span class="status pending">Bot phụ</span>'
            enabled = '<span class="status active">Đang bật</span>' if b["is_enabled"] else '<span class="status inactive">Đã tắt</span>'
            rows.append(f"""
            <details class="setup-section"><summary><span>🤖 {esc(b['name'])} {primary} {enabled}</span><span class="muted">Sửa</span></summary>
            <div class="setup-content">
            <form method="post" action="/bots/update" class="form-grid">{self._form_csrf()}<input type="hidden" name="id" value="{b['id']}">
                <label>Tên bot<input name="name" value="{esc(b['name'])}" required></label>
                <label>Loại bot<select name="bot_type"><option value="shop" {selected(b['bot_type'],'shop')}>Bot shop bán hàng</option><option value="binance" {selected(b['bot_type'],'binance')}>Bot Binance/crypto</option><option value="support" {selected(b['bot_type'],'support')}>Bot hỗ trợ</option></select></label>
                <label class="full">Bot token<input type="password" name="token" placeholder="Để trống nếu không đổi token"><div class="help">Lấy từ @BotFather. Để trống nếu không đổi token cũ.</div></label>
                <label>Username bot<input name="username" value="{esc(b['username'])}" placeholder="tenbot_bot"></label>
                <label>Admin liên hệ<input name="admin_contact" value="{esc(b['admin_contact'])}" placeholder="@username"></label>
                <label class="checkbox-row"><input type="checkbox" name="is_primary" {'checked' if b['is_primary'] else ''}> Dùng làm bot chính đang chạy</label>
                <label class="checkbox-row"><input type="checkbox" name="is_enabled" {'checked' if b['is_enabled'] else ''}> Bật bot này</label>
                <label class="full">Ghi chú<textarea name="notes" placeholder="Bot bán ChatGPT, bot Binance, bot hỗ trợ...">{esc(b['notes'])}</textarea></label>
                <div class="full action-row"><button>Lưu bot</button></form><form method="post" action="/bots/delete" onsubmit="return confirm('Xóa bot này khỏi danh sách quản lý?');">{self._form_csrf()}<input type="hidden" name="id" value="{b['id']}"><button class="danger">Xóa khỏi quản lý</button></form></div>
            </div></details>""")
        form = f"""
        <div class="hint-box"><b>Quản lý nhiều bot:</b><br>
        Bạn có thể lưu nhiều bot token ở đây: bot shop chính, bot Binance/crypto, bot hỗ trợ. Bot được đánh dấu <b>Bot chính</b> sẽ ghi token vào cấu hình BOT_TOKEN khi lưu và chạy bằng lệnh <code>run_all</code>.</div>
        <div class="card"><h2>＋ Thêm bot mới</h2><form method="post" action="/bots/create" class="form-grid">{self._form_csrf()}
            <label>Tên bot<input name="name" placeholder="NIMO Shop Bot" required><div class="help">Tên để bạn dễ phân biệt trong quản trị.</div></label>
            <label>Loại bot<select name="bot_type"><option value="shop">Bot shop bán hàng</option><option value="binance">Bot Binance/crypto</option><option value="support">Bot hỗ trợ</option></select></label>
            <label class="full">Bot token<input type="password" name="token" placeholder="123456789:AA..." required><div class="help">Vào @BotFather → /newbot hoặc /mybots → API Token.</div></label>
            <label>Username bot<input name="username" placeholder="nimoshop_bot"></label>
            <label>Admin liên hệ<input name="admin_contact" placeholder="@xuantoi"></label>
            <label class="checkbox-row"><input type="checkbox" name="is_primary"> Đặt làm bot chính</label>
            <label class="checkbox-row"><input type="checkbox" name="is_enabled" checked> Bật bot</label>
            <label class="full">Ghi chú<textarea name="notes" placeholder="Bot này dùng cho shop chính / Binance / hỗ trợ..."></textarea></label>
            <button>Thêm bot</button>
        </form></div>"""
        return form + ''.join(rows)

    def _notifications(self) -> str:
        products = self.service.list_products()
        opts = '<option value="">Tất cả / không gắn sản phẩm</option>' + ''.join(f'<option value="{p["id"]}">#{p["id"]} {esc(p["name"])}</option>' for p in products)
        form = f"""
        <div class="card"><h2>📣 Tạo thông báo bot</h2><p class="muted">Thông báo sẽ vào hàng chờ. Tiến trình bot Telegram đang chạy sẽ gửi cho các user đã từng /start.</p>
        <form method="post" action="/notifications/create" class="form-grid">{self._form_csrf()}
            <label>Tiêu đề<input name="title" placeholder="Cập nhật sản phẩm / Khuyến mãi" required></label>
            <label>Gắn với sản phẩm<select name="product_id">{opts}</select></label>
            <label class="full">Nội dung gửi trên bot<textarea name="message" placeholder="🎁 Khuyến mãi hôm nay..." required></textarea><div class="help">Có thể dùng HTML Telegram cơ bản như &lt;b&gt;...&lt;/b&gt;, &lt;code&gt;...&lt;/code&gt;.</div></label>
            <button>Tạo thông báo</button>
        </form></div>"""
        rows = ''.join(f'<tr><td>{n["id"]}</td><td>{esc(n["kind"])}</td><td>{esc(n["title"])}</td><td>{status_badge(n["status"])}</td><td>{n["sent_count"]}</td><td>{esc(n["error"] or "")}</td><td>{esc(n["created_at"])}</td></tr>' for n in self.service.list_notifications())
        return form + '<div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Loại</th><th>Tiêu đề</th><th>Trạng thái</th><th>Đã gửi</th><th>Lỗi</th><th>Ngày</th></tr>' + rows + '</table></div>'

    def _backup(self) -> str:
        rows = ''.join(f'<tr><td>{esc(b["name"])}</td><td>{b["size"]//1024} KB</td><td>{esc(b["created_at"])}</td><td><code>{esc(b["path"])}</code></td></tr>' for b in self.service.list_backups())
        return f"""
        <div class="hint-box"><b>Backup/Restore dữ liệu:</b><br>
        Backup chứa <code>data/shop.db</code> và có thể chứa <code>.env</code>. Dùng để chuyển từ điện thoại sang máy tính hoặc ngược lại. Không chia sẻ file backup nếu có .env vì chứa token/API key.</div>
        <div class="grid2"><div class="card"><h2>⬇️ Tải backup</h2><p class="muted">Tạo file ZIP mới từ database đang chạy bằng SQLite backup API, an toàn hơn copy nóng file DB.</p><div class="action-row"><a class="btn" href="/backup/download?include_env=1">Tải backup gồm .env</a><a class="btn secondary" href="/backup/download?include_env=0">Chỉ tải database</a></div></div>
        <div class="card danger-zone"><h2>♻️ Restore backup</h2><p class="muted">Chỉ dùng khi bạn chắc chắn. Hệ thống sẽ tự tạo safety backup trước khi ghi đè.</p><form method="post" action="/backup/restore">{self._form_csrf()}<label>Đường dẫn file backup trên máy/điện thoại<input name="backup_path" placeholder="backups/nimo-backup-YYYYMMDD-HHMMSS.zip" required></label><br><button class="danger">Restore từ file này</button></form></div></div>
        <div class="card table-wrap"><h3>Backup đã tạo</h3><table class="premium-table"><tr><th>File</th><th>Dung lượng</th><th>Ngày</th><th>Đường dẫn</th></tr>{rows}</table></div>"""

    def _guide(self) -> str:
        return '''
        <div class="card"><h2>1. Tạo bot với BotFather</h2><ol><li>Mở Telegram → tìm <b>@BotFather</b>.</li><li>Gửi <code>/newbot</code>, đặt tên và username kết thúc bằng <code>bot</code>.</li><li>Copy token dạng <code>123456789:AA...</code>.</li><li>Vào <b>Quản lý bot</b> hoặc <b>Cấu hình</b> dán token, lưu và restart lệnh chạy.</li></ol></div>
        <div class="card"><h2>2. Liên kết ngân hàng Việt Nam / SePay</h2><ol><li>Chọn ngân hàng nhận tiền, nhập Bank BIN, số tài khoản, tên chủ tài khoản.</li><li>Nếu chỉ muốn xác nhận thủ công, không cần SePay API key.</li><li>Nếu muốn tự nhận tiền, tạo tài khoản SePay, liên kết ngân hàng, lấy API key và dán vào Cấu hình.</li><li>Test bằng giao dịch nhỏ 10.000đ trước khi mở bán.</li></ol></div>
        <div class="card"><h2>3. Binance / USDT</h2><ol><li>Nếu chưa có merchant API, tạm để Binance tắt và dùng xác nhận thủ công.</li><li>Nếu có Binance Pay merchant, nhập API key/secret, return URL và webhook HTTPS.</li><li>Chạy bot trên điện thoại thì chưa nên bật webhook public nếu không có domain HTTPS.</li></ol></div>
        <div class="card"><h2>4. Chuyển dữ liệu điện thoại ↔ máy tính</h2><ol><li>Vào <b>Backup dữ liệu</b> → tải backup.</li><li>Copy file ZIP sang máy mới.</li><li>Cài dự án, mở Web Admin → Backup → Restore bằng đường dẫn file.</li><li>Chạy Audit sau khi restore.</li></ol></div>
        <div class="card"><h2>5. Vận hành hằng ngày</h2><ol><li>Chạy một lệnh: <code>PYTHONPATH=src python -m nimo_shop.run_all --host 0.0.0.0 --port 8080</code></li><li>Thêm/sửa sản phẩm, nhập kho, tạo thông báo từ Web Admin.</li><li>Cuối ngày vào Audit và Backup.</li></ol></div>
        '''

    def _audit(self) -> str:
        issues = self.service.audit()
        if not issues:
            return '<div class="card"><span class="status delivered">Hệ thống sạch</span><p class="muted">Không phát hiện lệch ví, đơn, kho, giao hàng hoặc dòng tiền.</p></div>'
        return '<div class="card">' + "".join(f'<div class="alert err"><b>{esc(i["code"])}</b><br>{esc(i["message"])}</div>' for i in issues) + '</div>'

    def _logs(self) -> str:
        rows = "".join(f'<tr><td>{l["id"]}</td><td>{esc(l["admin_username"] or l["admin_id"])}</td><td>{esc(l["action"])}</td><td>{esc(l["target_type"])}</td><td>{esc(l["target_id"])}</td><td><code>{esc(l["metadata_json"])}</code></td><td>{esc(l["created_at"])}</td></tr>' for l in self.service.audit_logs())
        return '<div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Admin</th><th>Hành động</th><th>Loại</th><th>ID</th><th>Dữ liệu</th><th>Thời gian</th></tr>' + rows + '</table></div>'


    def _status_page(self) -> str:
        st = self.service.system_status()
        def ok(v: object) -> str:
            return '<span class="status delivered">OK</span>' if v else '<span class="status rejected">Cần xử lý</span>'
        lows = ''.join(f'<li>{esc(i["name"])}: còn {int(i["available"])} dòng</li>' for i in st.get('low_stock_items', [])) or '<li>Không có sản phẩm dưới ngưỡng</li>'
        return f'''<div class="grid2"><div class="card"><h2>Trạng thái hệ thống</h2><table><tr><td>Database</td><td>{ok(st['database_ok'])}</td></tr><tr><td>Bot token</td><td>{ok(st['bot_token_ok'])}</td></tr><tr><td>Ngân hàng/SePay</td><td>{ok(st['bank_enabled'] and st['sepay_configured'])}</td></tr><tr><td>Binance</td><td>{ok((not st['binance_enabled']) or st['binance_configured'])}</td></tr><tr><td>Backup dir</td><td><code>{esc(st['backup_dir'])}</code></td></tr></table></div><div class="card"><h2>Kiểm tra token BotFather</h2><form method="post" action="/status/check-token">{self._form_csrf()}<label>Dán token để kiểm tra định dạng<input name="token" placeholder="123456789:AA..."></label><br><button>Kiểm tra token</button></form><p class="muted">Kiểm tra này không gọi Telegram live để tránh lộ token trong log. Khi chạy bot, lệnh getMe sẽ xác nhận live.</p></div></div><div class="card"><h2>Cảnh báo kho thấp</h2><ul>{lows}</ul><a class="btn secondary" href="/low-stock">Mở trang cảnh báo kho</a></div>'''

    def _imports_page(self) -> str:
        sample = 'category,name,price,currency,cost,description,warranty_text,stock\nChatGPT,ChatGPT Plus 1 tháng,150000,VND,100000,Tài khoản 30 ngày,1 đổi 1,key1;key2'
        return f'''<div class="grid2"><div class="card"><h2>Import sản phẩm/kho bằng CSV</h2><p class="muted">Dùng khi nhập nhiều sản phẩm. Cột bắt buộc: category,name,price. Cột stock có thể phân tách bằng dấu chấm phẩy.</p><form method="post" action="/imports/catalog">{self._form_csrf()}<label>Nội dung CSV<textarea name="csv_text" placeholder="{esc(sample)}" required></textarea></label><br><button>Import CSV</button></form></div><div class="card"><h2>Hướng dẫn nhanh</h2><ol><li>Mở Google Sheet/Excel.</li><li>Tạo cột category,name,price,currency,cost,description,warranty_text,stock.</li><li>Export CSV hoặc copy dòng dán vào ô bên trái.</li><li>Chạy Audit sau khi import.</li></ol></div></div>'''

    def _exports_page(self) -> str:
        links = ''.join(f'<a class="btn" href="/exports/download?kind={k}">Tải {label}.csv</a>' for k, label in [('orders','Đơn hàng'),('products','Sản phẩm'),('stock','Kho'),('wallets','Ví khách'),('finance','Dòng tiền'),('users','Người dùng')])
        return f'<div class="card"><h2>Xuất báo cáo CSV</h2><p class="muted">Dùng để đối soát, lưu kế toán hoặc chuyển dữ liệu sang máy khác.</p><div class="action-row">{links}</div></div>'

    def _reconcile_page(self, query: dict[str, list[str]]) -> str:
        status = query.get('status', ['unmatched'])[0]
        rows = ''
        for r in self.service.list_reconciliation_events(status=status if status != 'all' else '', limit=200):
            rows += f'''<tr><td>{r['id']}</td><td>{esc(r['provider'])}</td><td>{esc(r['provider_tx_id'])}</td><td>{esc(r['payment_code'])}</td><td>{money(r['amount_minor'], r['currency'])}</td><td>{status_badge(r['status'])}</td><td><code>{esc(r['raw_json'])}</code></td><td><form method="post" action="/reconcile/review">{self._form_csrf()}<input type="hidden" name="event_id" value="{r['id']}"><input name="note" placeholder="Ghi chú xử lý"><button class="small">Đã kiểm tra</button></form></td></tr>'''
        return f'''<div class="card"><div class="section-head"><div><h2>Đối soát giao dịch lỗi/sai nội dung</h2><p class="muted">Theo dõi giao dịch không khớp mã đơn, sai nội dung, hoặc cần xử lý thủ công.</p></div><div class="action-row"><a class="btn secondary" href="/reconcile?status=unmatched">Unmatched</a><a class="btn secondary" href="/reconcile?status=all">Tất cả</a></div></div><div class="table-wrap"><table class="premium-table"><tr><th>ID</th><th>Provider</th><th>TX ID</th><th>Mã</th><th>Số tiền</th><th>Trạng thái</th><th>Raw</th><th>Xử lý</th></tr>{rows}</table></div></div>'''

    def _coupons_page(self) -> str:
        rows = ''
        for c in self.service.list_coupons():
            rows += f'''<tr><form method="post" action="/coupons/update">{self._form_csrf()}<input type="hidden" name="id" value="{c['id']}"><td><input name="code" value="{esc(c['code'])}"></td><td><select name="discount_type"><option value="fixed" {selected(c['discount_type'],'fixed')}>Giảm tiền</option><option value="percent" {selected(c['discount_type'],'percent')}>Giảm %</option></select></td><td><input name="discount_value" value="{esc(c['discount_value'])}"></td><td><input name="currency" value="{esc(c['currency'])}"></td><td><input name="max_uses" value="{esc(c['max_uses'])}"></td><td><input type="checkbox" name="is_active" {'checked' if c['is_active'] else ''}></td><td><button class="small">Lưu</button></form><form method="post" action="/coupons/delete" onsubmit="return confirm('Xóa coupon?')">{self._form_csrf()}<input type="hidden" name="id" value="{c['id']}"><button class="danger small">Xóa</button></form></td></tr>'''
        form = f'''<div class="card"><h2>Tạo mã giảm giá</h2><form method="post" action="/coupons/create" class="form-grid">{self._form_csrf()}<label>Mã coupon<input name="code" placeholder="SALE10" required></label><label>Loại giảm<select name="discount_type"><option value="fixed">Giảm tiền</option><option value="percent">Giảm phần trăm</option></select></label><label>Giá trị<input name="discount_value" placeholder="10000 hoặc 10"></label><label>Tiền tệ<input name="currency" value="VND"></label><label>Giới hạn lượt dùng<input name="max_uses" value="0"></label><label>Hết hạn<input name="expires_at" placeholder="2026-12-31T23:59:00+00:00"></label><label class="full">Ghi chú<input name="note"></label><label><input style="width:auto" type="checkbox" name="is_active" checked> Đang bật</label><button>Tạo coupon</button></form></div>'''
        return form + '<div class="card table-wrap"><h2>Danh sách coupon</h2><table class="premium-table"><tr><th>Mã</th><th>Loại</th><th>Giá trị</th><th>Tiền tệ</th><th>Giới hạn</th><th>Bật</th><th>Thao tác</th></tr>' + rows + '</table></div>'

    def _roles_page(self) -> str:
        def role_options(cur: str) -> str:
            return ''.join(f'<option value="{r}" {selected(cur,r)}>{r}</option>' for r in ['owner','finance','stock','support','viewer'])
        rows = ''
        for a in self.service.list_admin_accounts():
            rows += f'''<tr><form method="post" action="/roles/update">{self._form_csrf()}<input type="hidden" name="id" value="{a['id']}"><td>{a['id']}</td><td>{esc(a['username'])}</td><td><select name="role">{role_options(a['role'])}</select></td><td><input type="checkbox" name="is_active" {'checked' if a['is_active'] else ''}></td><td><input type="password" name="password" placeholder="Để trống nếu không đổi"></td><td><button class="small">Lưu</button></td></form></tr>'''
        form = f'''<div class="card"><h2>Thêm admin/phân quyền</h2><p class="muted">Owner toàn quyền; Finance xử lý tiền; Stock nhập kho; Support xem/hỗ trợ; Viewer chỉ xem.</p><form method="post" action="/roles/create" class="form-grid">{self._form_csrf()}<label>Username<input name="username" required></label><label>Mật khẩu<input type="password" name="password" required></label><label>Vai trò<select name="role">{role_options('viewer')}</select></label><button>Tạo admin</button></form></div>'''
        return form + '<div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Username</th><th>Vai trò</th><th>Bật</th><th>Mật khẩu mới</th><th>Lưu</th></tr>' + rows + '</table></div>'

    def _deliveries_page(self) -> str:
        rows = ''.join(f'<tr><td>{d["id"]}</td><td>{esc(d.get("public_code") or d.get("order_id"))}</td><td>{esc(d.get("telegram_id") or "")}</td><td>{esc(d["source"])}</td><td>{esc(d["filename"])}</td><td>{esc(d["created_at"])}</td></tr>' for d in self.service.list_delivery_downloads())
        return '<div class="card"><h2>Nhật ký tải/gửi file đơn hàng</h2><p class="muted">Theo dõi khách/admin tải lại file giao hàng, giúp kiểm tra khi khách báo mất file.</p><div class="table-wrap"><table class="premium-table"><tr><th>ID</th><th>Đơn</th><th>Telegram ID</th><th>Nguồn</th><th>File</th><th>Thời gian</th></tr>' + rows + '</table></div></div>'

    def _low_stock_page(self, query: dict[str, list[str]]) -> str:
        threshold = int(query.get('threshold', ['5'])[0] or 5)
        rows = ''.join(f'<tr><td>{i["product_id"]}</td><td>{esc(i["name"])}</td><td>{int(i["available"])}</td><td><a class="btn small secondary" href="/stock?product_id={i["product_id"]}">Nhập thêm</a></td></tr>' for i in self.service.low_stock_items(threshold))
        return f'''<div class="card"><h2>Cảnh báo hết hàng</h2><form method="get" action="/low-stock" class="form-grid"><label>Ngưỡng cảnh báo<input name="threshold" value="{threshold}"></label><button>Lọc</button></form><br><form method="post" action="/low-stock/notify">{self._form_csrf()}<input type="hidden" name="threshold" value="{threshold}"><button>Gửi thông báo cho admin/bot queue</button></form></div><div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Sản phẩm</th><th>Còn</th><th>Thao tác</th></tr>{rows}</table></div>'''


def create_server(db_path: str | Path, *, host: str = "127.0.0.1", port: int = 8080, session_secret: str | None = None, project_root: str | Path | None = None, bootstrap_username: str = "admin", bootstrap_password: str | None = None) -> ThreadingHTTPServer:
    import secrets

    db = Database(db_path)
    service = AdminWebService(db, project_root=project_root)
    service.init(bootstrap_username=bootstrap_username, bootstrap_password=bootstrap_password)
    server = ThreadingHTTPServer((host, port), AdminRequestHandler)
    server.service = service  # type: ignore[attr-defined]
    secret = session_secret or os.getenv("WEB_SESSION_SECRET") or ""
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if not secret:
        if host in local_hosts:
            secret = secrets.token_urlsafe(32)
        else:
            raise ValueError("WEB_SESSION_SECRET is required when Web Admin is exposed outside localhost")
    if secret == "change-this-web-session-secret" or (host not in local_hosts and len(secret) < 32):
        raise ValueError("WEB_SESSION_SECRET is too weak; use a random string of at least 32 characters")
    server.session_secret = secret  # type: ignore[attr-defined]
    return server
