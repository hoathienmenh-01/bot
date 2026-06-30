from __future__ import annotations

import html
import os
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from nimo_shop.db import Database
from nimo_shop.money import fmt_money, from_minor
from nimo_shop.web.security import create_session, csrf_token, read_session, verify_csrf
from nimo_shop.web.service import AdminWebService, DEFAULT_SETTING_KEYS

LANG = {
    "vi": {
        "dashboard": "Tổng quan", "orders": "Đơn hàng", "products": "Sản phẩm", "categories": "Danh mục",
        "stock": "Kho hàng", "users": "Người dùng", "wallets": "Ví", "finance": "Dòng tiền",
        "payments": "Thanh toán", "settings": "Cấu hình", "audit": "Kiểm tra hệ thống", "logs": "Nhật ký admin",
        "login": "Đăng nhập", "logout": "Đăng xuất", "save": "Lưu thay đổi", "create": "Tạo mới",
        "import_stock": "Nhập kho", "confirm_payment": "Xác nhận thanh toán", "light": "Sáng", "dark": "Tối",
        "language": "Ngôn ngữ", "theme": "Giao diện", "welcome": "Trang quản lý NIMO Shop",
        "admin_panel": "Bảng quản trị", "search_note": "Quản lý sản phẩm, kho, đơn, ví, dòng tiền và cấu hình bot từ trình duyệt.",
        "add_product": "Thêm sản phẩm", "edit_product": "Sửa sản phẩm", "delete": "Xóa", "edit": "Sửa", "back": "Quay lại",
    },
    "en": {
        "dashboard": "Dashboard", "orders": "Orders", "products": "Products", "categories": "Categories",
        "stock": "Inventory", "users": "Users", "wallets": "Wallets", "finance": "Finance",
        "payments": "Payments", "settings": "Settings", "audit": "System Audit", "logs": "Admin Logs",
        "login": "Login", "logout": "Logout", "save": "Save changes", "create": "Create",
        "import_stock": "Import stock", "confirm_payment": "Confirm payment", "light": "Light", "dark": "Dark",
        "language": "Language", "theme": "Theme", "welcome": "NIMO Shop Admin",
        "admin_panel": "Admin Panel", "search_note": "Manage products, stock, orders, wallets, finance and bot configuration from the browser.",
        "add_product": "Add product", "edit_product": "Edit product", "delete": "Delete", "edit": "Edit", "back": "Back",
    },
}

NAV = [
    ("/", "dashboard"), ("/orders", "orders"), ("/products", "products"), ("/categories", "categories"),
    ("/stock", "stock"), ("/users", "users"), ("/wallets", "wallets"), ("/finance", "finance"),
    ("/payments", "payments"), ("/settings", "settings"), ("/audit", "audit"), ("/logs", "logs"),
]

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
        "title": "5. Web Admin",
        "desc": "Tài khoản đăng nhập trang quản trị, giao diện mặc định và cổng chạy web.",
        "keys": ["WEB_ADMIN_USERNAME", "WEB_ADMIN_PASSWORD", "WEB_SESSION_SECRET", "WEB_HOST", "WEB_PORT", "WEB_DEFAULT_LANGUAGE", "WEB_DEFAULT_THEME"],
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
}

CSS = r"""
:root{--bg:#f4f7fb;--panel:#ffffff;--panel2:#eef4ff;--text:#0f172a;--muted:#64748b;--brand:#2563eb;--brand2:#7c3aed;--line:#e2e8f0;--danger:#dc2626;--ok:#16a34a;--warn:#d97706;--shadow:0 18px 45px rgba(15,23,42,.09);--radius:20px;--input:#fbfdff}
[data-theme="dark"]{--bg:#07111f;--panel:#101827;--panel2:#16243a;--text:#e5e7eb;--muted:#9aa8bd;--brand:#60a5fa;--brand2:#a78bfa;--line:#26364d;--danger:#fb7185;--ok:#4ade80;--warn:#fbbf24;--shadow:0 20px 55px rgba(0,0,0,.38);--input:#0b1526}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top left,rgba(37,99,235,.13),transparent 35%),var(--bg);color:var(--text);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}a{color:inherit;text-decoration:none}button,.btn{border:0;background:linear-gradient(135deg,var(--brand),var(--brand2));color:white;padding:10px 14px;border-radius:13px;cursor:pointer;font-weight:800;box-shadow:0 10px 22px rgba(37,99,235,.22);display:inline-flex;align-items:center;gap:7px}.btn.secondary,button.secondary{background:var(--panel2);color:var(--text);box-shadow:none;border:1px solid var(--line)}.btn.danger,button.danger{background:var(--danger);box-shadow:none}.btn.ghost,button.ghost{background:transparent;color:var(--text);border:1px solid var(--line);box-shadow:none}.btn.small,button.small{padding:7px 10px;border-radius:10px;font-size:13px}.layout{display:grid;grid-template-columns:280px 1fr;min-height:100vh}.sidebar{padding:22px;background:rgba(255,255,255,.78);backdrop-filter:blur(16px);border-right:1px solid var(--line);position:sticky;top:0;height:100vh;overflow:auto}[data-theme="dark"] .sidebar{background:rgba(16,24,39,.84)}.brand{font-weight:900;font-size:22px;letter-spacing:.2px;margin-bottom:6px}.brand-badge{display:inline-flex;background:linear-gradient(135deg,var(--brand),var(--brand2));color:white;border-radius:12px;padding:6px 10px;font-size:12px;margin-bottom:12px}.subtitle{color:var(--muted);font-size:13px;margin-bottom:20px}.nav a{display:flex;padding:12px 14px;border-radius:14px;color:var(--muted);margin:5px 0;font-weight:700}.nav a.active,.nav a:hover{background:var(--panel2);color:var(--text)}.main{padding:26px;max-width:1500px}.topbar{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:22px}.h1{font-size:30px;font-weight:900;margin:0}.toolbar{display:flex;gap:9px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px;margin-bottom:16px}.card h3,.card h2{margin-top:0}.metric{font-size:28px;font-weight:900}.muted{color:var(--muted)}.help{color:var(--muted);font-size:13px;margin-top:5px}.status{display:inline-flex;padding:5px 10px;border-radius:999px;background:var(--panel2);font-size:12px;font-weight:800}.status.delivered,.status.paid,.status.order_delivered,.status.wallet_credited,.status.active{color:var(--ok)}.status.cancelled,.status.refunded,.status.rejected,.status.unmatched,.status.inactive{color:var(--danger)}.status.awaiting_payment,.status.pending,.status.reserved{color:var(--warn)}.table-wrap{overflow:auto}.premium-table{min-width:900px}table{width:100%;border-collapse:collapse}th,td{padding:13px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}tr:hover td{background:rgba(37,99,235,.035)}label{font-weight:800;display:block}input,select,textarea{width:100%;border:1px solid var(--line);background:var(--input);color:var(--text);padding:11px 13px;border-radius:13px;font:inherit;margin-top:6px}textarea{min-height:110px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.form-grid .full{grid-column:1/-1}.alert{padding:13px 15px;border-radius:15px;margin-bottom:14px;background:var(--panel2);border:1px solid var(--line)}.alert.ok{border-color:rgba(22,163,74,.45)}.alert.err{border-color:rgba(220,38,38,.45);color:var(--danger)}.login{min-height:100vh;display:grid;place-items:center;padding:24px}.login-card{max-width:440px;width:100%}.pill{display:inline-flex;gap:8px;align-items:center;padding:7px 10px;border-radius:999px;background:var(--panel2);color:var(--muted);font-weight:800;font-size:12px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.product-title{font-weight:900}.action-row{display:flex;gap:7px;flex-wrap:wrap}.danger-zone{border:1px dashed rgba(220,38,38,.55);background:rgba(220,38,38,.04)}details.setup-section{border:1px solid var(--line);border-radius:18px;background:var(--panel);margin-bottom:14px;overflow:hidden}details.setup-section[open]{box-shadow:var(--shadow)}details.setup-section summary{cursor:pointer;padding:16px 18px;font-weight:900;list-style:none;display:flex;justify-content:space-between;gap:12px}details.setup-section summary::-webkit-details-marker{display:none}.setup-content{border-top:1px solid var(--line);padding:18px}.setting-field{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px}.setting-field.secret input::placeholder{color:var(--muted)}.hint-box{border-left:4px solid var(--brand);background:var(--panel2);padding:13px 15px;border-radius:14px;margin-bottom:16px}.hide-mobile{display:inline}@media(max-width:980px){.layout{grid-template-columns:1fr}.sidebar{position:relative;height:auto}.main{padding:16px}.grid,.grid2,.form-grid{grid-template-columns:1fr}.hide-mobile{display:none}.topbar{display:block}.premium-table{min-width:760px}}
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

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
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
        nav = "".join(f'<a class="{("active" if active == path else "")}" href="{path}">{tr(lang, key)}</a>' for path, key in NAV)
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
        return f"""<!doctype html><html lang="{esc(lang)}" data-theme="{esc(theme)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Đăng nhập - NIMO</title><style>{CSS}</style></head><body><div class="login"><div class="card login-card"><div class="brand-badge">Premium Admin</div><div class="brand">NIMO Shop Admin</div><p class="muted">Đăng nhập để quản lý sản phẩm, kho hàng, đơn, ví và cấu hình thanh toán.</p>{err}<form method="post" action="/login"><label>Tên đăng nhập<input name="username" autocomplete="username" placeholder="admin"></label><br><label>Mật khẩu<input type="password" name="password" autocomplete="current-password" placeholder="admin12345"></label><br><br><button>{esc(tr(lang,'login'))}</button></form></div></div></body></html>"""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.query:
            query = parse_qs(parsed.query)
            cookies = []
            if "theme" in query:
                cookies.append(f"nimo_theme={query['theme'][0]}; Path=/; SameSite=Lax")
            if "lang" in query:
                cookies.append(f"nimo_lang={query['lang'][0]}; Path=/; SameSite=Lax")
            if cookies and parsed.path in {"/", "/orders", "/products", "/categories", "/stock", "/users", "/wallets", "/finance", "/payments", "/settings", "/audit", "/logs"}:
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
        try:
            html_body = self._route_get(parsed.path, parse_qs(parsed.query))
            self._send(html_body)
        except Exception as exc:
            self._send(self._page("dashboard", "", active=parsed.path, error=str(exc)), status=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            form = self._read_form()
            admin = self.service.authenticate(form.get("username", ""), form.get("password", ""))
            if not admin:
                self._send(self._login_page("Sai tài khoản hoặc mật khẩu"), status=401)
                return
            token = create_session(self.session_secret, admin_id=int(admin["id"]), username=admin["username"], role=admin["role"])
            self._redirect("/", [f"nimo_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200"])
            return
        session = self._require_login()
        if not session:
            return
        form = self._read_form()
        if not self._verify_post(form):
            self._send(self._page("dashboard", "", active="/", error="CSRF token không hợp lệ. Hãy tải lại trang."), status=403)
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
        if path == "/stock":
            return self._page("stock", self._stock(query), active="/stock")
        if path == "/orders":
            return self._page("orders", self._orders(query), active="/orders")
        if path == "/users":
            return self._page("users", self._users(), active="/users")
        if path == "/wallets":
            return self._page("wallets", self._wallets(), active="/wallets")
        if path == "/finance":
            return self._page("finance", self._finance(), active="/finance")
        if path == "/payments":
            return self._page("payments", self._payments(), active="/payments")
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
            self.service.create_category(form.get("name", ""), int(form.get("sort_order") or 100), admin_id=admin_id)
            return "/categories"
        if path == "/categories/update":
            self.service.update_category(int(form["id"]), name=form.get("name", ""), sort_order=int(form.get("sort_order") or 100), is_active=form.get("is_active") == "on", admin_id=admin_id)
            return "/categories"
        if path == "/products/create":
            self.service.create_product(form, admin_id=admin_id)
            return "/products"
        if path == "/products/update":
            self.service.update_product(int(form["id"]), form, admin_id=admin_id)
            return "/products"
        if path == "/products/delete":
            self.service.delete_product(int(form["id"]), admin_id=admin_id)
            return "/products"
        if path == "/stock/import":
            self.service.add_stock(int(form["product_id"]), form.get("contents", ""), admin_id=admin_id)
            return "/stock"
        if path == "/orders/cancel":
            self.service.cancel_order(int(form["order_id"]), admin_id=admin_id)
            return "/orders"
        if path == "/orders/refund":
            self.service.refund_order(int(form["order_id"]), admin_id=admin_id)
            return "/orders"
        if path == "/wallets/adjust":
            self.service.manual_wallet_adjust(user_id=int(form["user_id"]), direction=form["direction"], currency=form["currency"], amount=form["amount"], reason=form.get("reason", "web_adjust"), admin_id=admin_id)
            return "/wallets"
        if path == "/payments/confirm":
            self.service.confirm_payment(payment_code=form["payment_code"], tx_id=form["tx_id"], amount=form["amount"], currency=form["currency"], provider=form["provider"], admin_id=admin_id)
            return "/payments"
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
            f'<tr><form method="post" action="/categories/update">{self._form_csrf()}<input type="hidden" name="id" value="{r["id"]}"><td>{r["id"]}</td><td><input name="name" value="{esc(r["name"])}"></td><td><input name="sort_order" value="{r["sort_order"]}"></td><td><input type="checkbox" name="is_active" {"checked" if r["is_active"] else ""}></td><td><button class="small">Lưu</button></td></form></tr>'
            for r in rows
        )
        return f'<div class="card"><h3>Tạo danh mục</h3><p class="muted">Danh mục giúp khách chọn nhóm sản phẩm như ChatGPT, Gemini, Canva, CapCut.</p><form method="post" action="/categories/create" class="form-grid">{self._form_csrf()}<label>Tên danh mục<input name="name" placeholder="Ví dụ: ChatGPT" required></label><label>Thứ tự hiển thị<input name="sort_order" value="100"></label><button>{tr(self._theme_lang()[0],"create")}</button></form></div><div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Tên</th><th>Thứ tự</th><th>Đang bật</th><th></th></tr>{table_rows}</table></div>'

    def _category_options(self, current: Any = "") -> str:
        categories = self.service.list_categories()
        return '<option value="">Không có</option>' + "".join(f'<option value="{c["id"]}" {selected(current, c["id"])}>{esc(c["name"])}</option>' for c in categories)

    def _product_form(self, product: dict | None = None) -> str:
        is_edit = product is not None
        action = "/products/update" if is_edit else "/products/create"
        title = "Sửa sản phẩm" if is_edit else "Thêm sản phẩm mới"
        button = "Lưu sản phẩm" if is_edit else "Tạo sản phẩm"
        product = product or {"id": "", "category_id": "", "name": "", "currency": "VND", "price_minor": 0, "cost_minor": 0, "description": "", "warranty_text": "", "is_active": 1}
        active = bool(product.get("is_active", 1))
        hidden_id = f'<input type="hidden" name="id" value="{product["id"]}">' if is_edit else ""
        return f'''<div class="card"><div class="section-head"><div><h2>{title}</h2><p class="muted">Nhập thông tin khách sẽ nhìn thấy khi mua. Giá vốn chỉ dùng để tính lãi, khách không thấy.</p></div><a class="btn secondary" href="/products">← Quay lại danh sách</a></div><form method="post" action="{action}" class="form-grid">{self._form_csrf()}{hidden_id}<label>Danh mục<select name="category_id">{self._category_options(product.get("category_id") or "")}</select><div class="help">Chọn nhóm sản phẩm hiển thị trong menu Mua ngay.</div></label><label>Tên sản phẩm<input name="name" value="{esc(product.get("name"))}" placeholder="ChatGPT Plus 1 tháng" required><div class="help">Tên càng rõ càng ít khách hỏi lại.</div></label><label>Tiền tệ<select name="currency"><option {selected(product.get("currency"),"VND")}>VND</option><option {selected(product.get("currency"),"USDT")}>USDT</option><option {selected(product.get("currency"),"USD")}>USD</option></select></label><label>Giá bán<input name="price" inputmode="decimal" value="{amount_input(product.get("price_minor"), product.get("currency") or "VND")}" placeholder="150000" required><div class="help">VND nhập số tiền thường, ví dụ 150000.</div></label><label>Giá vốn<input name="cost" inputmode="decimal" value="{amount_input(product.get("cost_minor"), product.get("currency") or "VND")}" placeholder="100000"><div class="help">Dùng để tính lợi nhuận, có thể để 0.</div></label><label>Trạng thái<select name="is_active"><option value="1" {"selected" if active else ""}>Đang bán</option><option value="0" {"selected" if not active else ""}>Ẩn khỏi bot</option></select><div class="help">Ẩn sản phẩm nếu hết hàng hoặc ngừng bán.</div></label><label class="full">Mô tả sản phẩm<textarea name="description" placeholder="Tài khoản dùng 30 ngày, giao tự động sau khi thanh toán...">{esc(product.get("description") or "")}</textarea></label><label class="full">Chính sách bảo hành<textarea name="warranty_text" placeholder="Bảo hành 1 đổi 1 trong 30 ngày nếu lỗi do shop...">{esc(product.get("warranty_text") or "")}</textarea></label><div class="full toolbar"><button>{button}</button><a class="btn secondary" href="/products">Hủy</a></div></form></div>'''

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
            rows.append(f'''<tr><td><div class="product-title">{esc(p["name"])}</div><div class="muted">ID #{p["id"]} · {esc(p.get("category_name") or "Chưa có danh mục")}</div></td><td>{money(p["price_minor"], p["currency"])}<br><span class="muted">Vốn: {money(p["cost_minor"], p["currency"])}</span></td><td><span class="pill">Còn {p["available_stock"]}</span> <span class="pill">Giữ {p["reserved_stock"]}</span> <span class="pill">Bán {p["sold_stock"]}</span></td><td>{status}</td><td><div class="action-row"><a class="btn small secondary" href="/products?edit={p["id"]}">Sửa</a><form method="post" action="/products/delete" style="display:inline" onsubmit="return confirm('Bạn chắc chắn muốn xóa/ẩn sản phẩm này?');">{self._form_csrf()}<input type="hidden" name="id" value="{p["id"]}"><button class="small danger">{delete_label}</button></form><a class="btn small ghost" href="/stock?product_id={p["id"]}">Nhập kho</a></div></td></tr>''')
        empty = '<tr><td colspan="5"><div class="alert">Chưa có sản phẩm. Bấm “Thêm sản phẩm” để tạo sản phẩm đầu tiên.</div></td></tr>' if not rows else ""
        return f'''<div class="card"><div class="section-head"><div><h2>Danh sách sản phẩm</h2><p class="muted">Chỉ hiển thị danh sách và nút thao tác rõ ràng. Bấm Thêm/Sửa để mở form riêng, không sửa lẫn trong bảng.</p></div><a class="btn" href="/products?new=1">＋ Thêm sản phẩm</a></div></div><div class="card table-wrap"><table class="premium-table"><tr><th>Sản phẩm</th><th>Giá</th><th>Kho</th><th>Trạng thái</th><th>Thao tác</th></tr>{empty}{"".join(rows)}</table></div>'''

    def _stock(self, query: dict[str, list[str]]) -> str:
        products = self.service.list_products()
        selected_product = query.get("product_id", [""])[0]
        opts = "".join(f'<option value="{p["id"]}" {selected(selected_product, p["id"])}>{esc(p["name"])} · còn {p["available_stock"]}</option>' for p in products)
        form = f'<div class="card"><h3>Nhập kho key/tài khoản</h3><p class="muted">Dán mỗi key/tài khoản một dòng. Hệ thống tự bỏ qua dòng trùng trong cùng sản phẩm.</p><form method="post" action="/stock/import" class="form-grid">{self._form_csrf()}<label>Sản phẩm<select name="product_id">{opts}</select></label><label class="full">Danh sách key/account<textarea name="contents" placeholder="email1@gmail.com|password1&#10;email2@gmail.com|password2&#10;license-key-1"></textarea><div class="help">Không nhập cùng một account/key hai lần. Mỗi dòng sẽ được giao cho tối đa một khách.</div></label><button>{tr(self._theme_lang()[0],"import_stock")}</button></form></div>'
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

    def _users(self) -> str:
        rows = "".join(f'<tr><td>{u["id"]}</td><td>{esc(u["telegram_id"])}</td><td>@{esc(u["username"] or "")}</td><td>{esc(u["full_name"] or "")}</td><td>{u["order_count"]}</td><td>{money(u["spent_minor"], "VND")}</td><td>{esc(u["created_at"])}</td></tr>' for u in self.service.list_users())
        return '<div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Telegram</th><th>Username</th><th>Tên</th><th>Đơn</th><th>Đã mua</th><th>Ngày</th></tr>' + rows + '</table></div>'

    def _wallets(self) -> str:
        rows = "".join(f'<tr><td>{w["user_id"]}</td><td>@{esc(w["username"] or "")}</td><td>{esc(w["currency"])}</td><td>{money(w["balance_minor"], w["currency"])}</td><td>{esc(w["updated_at"])}</td></tr>' for w in self.service.list_wallets())
        form = f'<div class="card"><h3>Cộng/trừ ví thủ công</h3><p class="muted">Chỉ dùng khi cần hỗ trợ khách hoặc đối soát giao dịch sai nội dung.</p><form method="post" action="/wallets/adjust" class="form-grid">{self._form_csrf()}<label>User ID<input name="user_id" required placeholder="ID nội bộ trong bảng người dùng"></label><label>Loại<select name="direction"><option value="credit">Cộng tiền</option><option value="debit">Trừ tiền</option></select></label><label>Tiền tệ<select name="currency"><option>VND</option><option>USDT</option><option>USD</option></select></label><label>Số tiền<input name="amount" required placeholder="100000"></label><label class="full">Lý do<input name="reason" value="web_admin_adjust"></label><button>Lưu thay đổi ví</button></form></div>'
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
        return f'''<div class="hint-box"><b>Hướng dẫn cấu hình:</b><br>1) Nhập Bot Token và Telegram ID admin trước. 2) Nếu dùng ngân hàng, bật Bank và nhập Bank BIN/Số tài khoản/Chủ tài khoản/SePay API key. 3) Tick “Ghi ra file .env”. 4) Lưu xong restart bot/web để áp dụng biến môi trường.</div><div class="card"><form method="post" action="/settings">{self._form_csrf()}{"".join(groups)}<label style="display:flex;gap:10px;align-items:center;font-weight:800"><input style="width:auto;margin:0" type="checkbox" name="write_env"> Ghi ra file .env để áp dụng sau khi restart bot/web</label><br><button>{tr(self._theme_lang()[0],"save")}</button></form></div>'''

    def _audit(self) -> str:
        issues = self.service.audit()
        if not issues:
            return '<div class="card"><span class="status delivered">Hệ thống sạch</span><p class="muted">Không phát hiện lệch ví, đơn, kho, giao hàng hoặc dòng tiền.</p></div>'
        return '<div class="card">' + "".join(f'<div class="alert err"><b>{esc(i["code"])}</b><br>{esc(i["message"])}</div>' for i in issues) + '</div>'

    def _logs(self) -> str:
        rows = "".join(f'<tr><td>{l["id"]}</td><td>{esc(l["admin_username"] or l["admin_id"])}</td><td>{esc(l["action"])}</td><td>{esc(l["target_type"])}</td><td>{esc(l["target_id"])}</td><td><code>{esc(l["metadata_json"])}</code></td><td>{esc(l["created_at"])}</td></tr>' for l in self.service.audit_logs())
        return '<div class="card table-wrap"><table class="premium-table"><tr><th>ID</th><th>Admin</th><th>Hành động</th><th>Loại</th><th>ID</th><th>Dữ liệu</th><th>Thời gian</th></tr>' + rows + '</table></div>'


def create_server(db_path: str | Path, *, host: str = "127.0.0.1", port: int = 8080, session_secret: str | None = None, project_root: str | Path | None = None, bootstrap_username: str = "admin", bootstrap_password: str | None = None) -> ThreadingHTTPServer:
    db = Database(db_path)
    service = AdminWebService(db, project_root=project_root)
    service.init(bootstrap_username=bootstrap_username, bootstrap_password=bootstrap_password)
    server = ThreadingHTTPServer((host, port), AdminRequestHandler)
    server.service = service  # type: ignore[attr-defined]
    server.session_secret = session_secret or os.getenv("WEB_SESSION_SECRET") or "change-this-web-session-secret"  # type: ignore[attr-defined]
    return server
