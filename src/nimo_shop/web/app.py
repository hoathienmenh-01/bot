from __future__ import annotations

import html
import json
import mimetypes
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
        "payments": "Thanh toán", "settings": "Cấu hình", "audit": "Audit", "logs": "Log admin",
        "login": "Đăng nhập", "logout": "Đăng xuất", "save": "Lưu", "create": "Tạo mới",
        "import_stock": "Nhập kho", "confirm_payment": "Xác nhận thanh toán", "light": "Sáng", "dark": "Tối",
        "language": "Ngôn ngữ", "theme": "Giao diện", "welcome": "Trang quản lý NIMO Shop",
        "admin_panel": "Bảng quản trị", "search_note": "Quản lý sản phẩm, kho, đơn, ví, dòng tiền và cấu hình bot từ trình duyệt.",
    },
    "en": {
        "dashboard": "Dashboard", "orders": "Orders", "products": "Products", "categories": "Categories",
        "stock": "Inventory", "users": "Users", "wallets": "Wallets", "finance": "Finance",
        "payments": "Payments", "settings": "Settings", "audit": "Audit", "logs": "Admin Logs",
        "login": "Login", "logout": "Logout", "save": "Save", "create": "Create",
        "import_stock": "Import stock", "confirm_payment": "Confirm payment", "light": "Light", "dark": "Dark",
        "language": "Language", "theme": "Theme", "welcome": "NIMO Shop Admin",
        "admin_panel": "Admin Panel", "search_note": "Manage products, stock, orders, wallets, finance and bot configuration from the browser.",
    },
}

NAV = [
    ("/", "dashboard"), ("/orders", "orders"), ("/products", "products"), ("/categories", "categories"),
    ("/stock", "stock"), ("/users", "users"), ("/wallets", "wallets"), ("/finance", "finance"),
    ("/payments", "payments"), ("/settings", "settings"), ("/audit", "audit"), ("/logs", "logs"),
]

CSS = r"""
:root{--bg:#f7f8fb;--panel:#ffffff;--panel2:#f1f5f9;--text:#0f172a;--muted:#64748b;--brand:#2563eb;--line:#e2e8f0;--danger:#dc2626;--ok:#16a34a;--warn:#d97706;--shadow:0 12px 30px rgba(15,23,42,.08);--radius:18px} 
[data-theme="dark"]{--bg:#0b1120;--panel:#111827;--panel2:#1f2937;--text:#e5e7eb;--muted:#94a3b8;--brand:#60a5fa;--line:#334155;--danger:#f87171;--ok:#4ade80;--warn:#fbbf24;--shadow:0 12px 30px rgba(0,0,0,.35)}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif} a{color:inherit;text-decoration:none}.layout{display:grid;grid-template-columns:260px 1fr;min-height:100vh}.sidebar{padding:22px;background:var(--panel);border-right:1px solid var(--line);position:sticky;top:0;height:100vh;overflow:auto}.brand{font-weight:800;font-size:20px;letter-spacing:.2px;margin-bottom:6px}.subtitle{color:var(--muted);font-size:13px;margin-bottom:22px}.nav a{display:flex;padding:11px 13px;border-radius:12px;color:var(--muted);margin:4px 0}.nav a.active,.nav a:hover{background:var(--panel2);color:var(--text)}.main{padding:24px;max-width:1450px}.topbar{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:22px}.h1{font-size:28px;font-weight:800;margin:0}.toolbar{display:flex;gap:8px;flex-wrap:wrap}.btn,button{border:0;background:var(--brand);color:white;padding:10px 14px;border-radius:12px;cursor:pointer;font-weight:700}.btn.secondary,button.secondary{background:var(--panel2);color:var(--text)}.btn.danger,button.danger{background:var(--danger)}.btn.small,button.small{padding:6px 10px;border-radius:9px;font-size:13px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px;margin-bottom:16px}.metric{font-size:28px;font-weight:800}.muted{color:var(--muted)}.status{display:inline-flex;padding:4px 9px;border-radius:999px;background:var(--panel2);font-size:12px;font-weight:700}.status.delivered,.status.paid,.status.order_delivered,.status.wallet_credited{color:var(--ok)}.status.cancelled,.status.refunded,.status.rejected,.status.unmatched{color:var(--danger)}.status.awaiting_payment,.status.pending,.status.reserved{color:var(--warn)}table{width:100%;border-collapse:collapse}th,td{padding:11px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}input,select,textarea{width:100%;border:1px solid var(--line);background:var(--bg);color:var(--text);padding:10px 12px;border-radius:12px;font:inherit}textarea{min-height:110px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.form-grid .full{grid-column:1/-1}.alert{padding:12px 14px;border-radius:14px;margin-bottom:14px;background:var(--panel2);border:1px solid var(--line)}.alert.ok{border-color:rgba(22,163,74,.45)}.alert.err{border-color:rgba(220,38,38,.45);color:var(--danger)}.login{min-height:100vh;display:grid;place-items:center;padding:24px}.login-card{max-width:420px;width:100%}.hide-mobile{display:inline}@media(max-width:980px){.layout{grid-template-columns:1fr}.sidebar{position:relative;height:auto}.main{padding:16px}.grid,.grid2,.form-grid{grid-template-columns:1fr}.hide-mobile{display:none}}
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


class AdminRequestHandler(BaseHTTPRequestHandler):
    server_version = "NIMOAdmin/1.4"

    @property
    def service(self) -> AdminWebService:
        return self.server.service  # type: ignore[attr-defined]

    @property
    def session_secret(self) -> str:
        return self.server.session_secret  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter tests/termux logs
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
            # multiple Set-Cookie values need direct header calls
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

    def _page(self, title_key: str, body: str, *, active: str | None = None, alert: str = "", error: str = "") -> str:
        lang, theme = self._theme_lang()
        session = self._session()
        nav = "".join(
            f'<a class="{("active" if active == path else "")}" href="{path}">{tr(lang, key)}</a>'
            for path, key in NAV
        )
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
        return f"""<!doctype html><html lang="{esc(lang)}" data-theme="{esc(theme)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(tr(lang,title_key))} - NIMO</title><style>{CSS}</style></head><body><div class="layout"><aside class="sidebar"><div class="brand">NIMO Shop</div><div class="subtitle">{tr(lang,'admin_panel')} · {user}</div><nav class="nav">{nav}</nav><div style="margin-top:18px" class="toolbar"><a class="btn secondary small" href="?{toolbar_qs_light}">{tr(lang,'light')}</a><a class="btn secondary small" href="?{toolbar_qs_dark}">{tr(lang,'dark')}</a><a class="btn secondary small" href="?{toolbar_qs_lang}">{switch_lang.upper()}</a><a class="btn danger small" href="/logout">{tr(lang,'logout')}</a></div></aside><main class="main"><div class="topbar"><div><h1 class="h1">{esc(tr(lang,title_key))}</h1><div class="muted">{esc(tr(lang,'search_note'))}</div></div></div>{msg}{body}</main></div></body></html>"""

    def _login_page(self, error: str = "") -> str:
        lang, theme = self._theme_lang()
        err = f'<div class="alert err">{esc(error)}</div>' if error else ""
        return f"""<!doctype html><html lang="{esc(lang)}" data-theme="{esc(theme)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Login - NIMO</title><style>{CSS}</style></head><body><div class="login"><div class="card login-card"><div class="brand">NIMO Shop Admin</div><p class="muted">{esc(tr(lang,'welcome'))}</p>{err}<form method="post" action="/login"><label>Username<input name="username" autocomplete="username"></label><br><br><label>Password<input type="password" name="password" autocomplete="current-password"></label><br><br><button>{esc(tr(lang,'login'))}</button></form></div></div></body></html>"""

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
            return self._page("products", self._products(), active="/products")
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

    def _form_csrf(self) -> str:
        return f'<input type="hidden" name="csrf" value="{esc(self._csrf())}">'

    def _dashboard(self) -> str:
        data = self.service.dashboard()
        c = data["counts"]
        metrics = "".join(f'<div class="card"><div class="muted">{esc(k)}</div><div class="metric">{esc(v)}</div></div>' for k, v in c.items())
        audit = data["audit"]
        audit_html = '<span class="status delivered">AUDIT OK</span>' if not audit else "".join(f'<div class="alert err">{esc(i.code)}: {esc(i.message)}</div>' for i in audit)
        orders = self._orders_table(data["recent_orders"], compact=True)
        return f'<div class="grid">{metrics}</div><div class="grid2"><div class="card"><h3>Audit</h3>{audit_html}</div><div class="card"><h3>Đơn gần đây</h3>{orders}</div></div>'

    def _categories(self) -> str:
        rows = self.service.list_categories()
        table_rows = "".join(
            f'<tr><form method="post" action="/categories/update">{self._form_csrf()}<input type="hidden" name="id" value="{r["id"]}"><td>{r["id"]}</td><td><input name="name" value="{esc(r["name"])}"></td><td><input name="sort_order" value="{r["sort_order"]}"></td><td><input type="checkbox" name="is_active" {"checked" if r["is_active"] else ""}></td><td><button class="small">Lưu</button></td></form></tr>'
            for r in rows
        )
        return f'<div class="card"><h3>Tạo danh mục</h3><form method="post" action="/categories/create" class="form-grid">{self._form_csrf()}<input name="name" placeholder="Tên danh mục"><input name="sort_order" value="100"><button>{tr(self._theme_lang()[0],"create")}</button></form></div><div class="card"><table><tr><th>ID</th><th>Tên</th><th>Thứ tự</th><th>Active</th><th></th></tr>{table_rows}</table></div>'

    def _products(self) -> str:
        categories = self.service.list_categories()
        cat_opts = '<option value="">Không có</option>' + "".join(f'<option value="{c["id"]}">{esc(c["name"])}</option>' for c in categories)
        form = f'''<div class="card"><h3>Tạo sản phẩm</h3><form method="post" action="/products/create" class="form-grid">{self._form_csrf()}<label>Danh mục<select name="category_id">{cat_opts}</select></label><label>Tên<input name="name" required></label><label>Tiền tệ<select name="currency"><option>VND</option><option>USDT</option><option>USD</option></select></label><label>Giá bán<input name="price" inputmode="decimal" required></label><label>Giá vốn<input name="cost" inputmode="decimal" value="0"></label><label class="full">Mô tả<textarea name="description"></textarea></label><label class="full">Bảo hành<textarea name="warranty_text"></textarea></label><button>{tr(self._theme_lang()[0],"create")}</button></form></div>'''
        rows = []
        for p in self.service.list_products():
            opts = '<option value="">Không có</option>' + "".join(f'<option value="{c["id"]}" {"selected" if p["category_id"] == c["id"] else ""}>{esc(c["name"])}</option>' for c in categories)
            rows.append(f'''<tr><form method="post" action="/products/update">{self._form_csrf()}<input type="hidden" name="id" value="{p["id"]}"><td>{p["id"]}</td><td><input name="name" value="{esc(p["name"])}"><div class="muted">Tồn: {p["available_stock"]} · Đã bán: {p["sold_stock"]}</div></td><td><select name="category_id">{opts}</select></td><td><select name="currency"><option {"selected" if p["currency"]=="VND" else ""}>VND</option><option {"selected" if p["currency"]=="USDT" else ""}>USDT</option><option {"selected" if p["currency"]=="USD" else ""}>USD</option></select></td><td><input name="price" value="{amount_input(p["price_minor"], p["currency"])}"><input name="cost" value="{amount_input(p["cost_minor"], p["currency"])}"></td><td><textarea name="description">{esc(p["description"] or "")}</textarea><textarea name="warranty_text">{esc(p["warranty_text"] or "")}</textarea></td><td><input type="checkbox" name="is_active" {"checked" if p["is_active"] else ""}></td><td><button class="small">Lưu</button></td></form></tr>''')
        return form + '<div class="card"><table><tr><th>ID</th><th>Sản phẩm</th><th>Danh mục</th><th>Tiền</th><th>Giá/Giá vốn</th><th>Mô tả/BH</th><th>Active</th><th></th></tr>' + "".join(rows) + '</table></div>'

    def _stock(self, query: dict[str, list[str]]) -> str:
        products = self.service.list_products()
        opts = "".join(f'<option value="{p["id"]}">{esc(p["name"])} · còn {p["available_stock"]}</option>' for p in products)
        form = f'<div class="card"><h3>Nhập kho key/tài khoản</h3><form method="post" action="/stock/import" class="form-grid">{self._form_csrf()}<label>Sản phẩm<select name="product_id">{opts}</select></label><label class="full">Danh sách key/account, mỗi dòng 1 mục<textarea name="contents" placeholder="email|password&#10;license-key-1"></textarea></label><button>{tr(self._theme_lang()[0],"import_stock")}</button></form></div>'
        items = self.service.list_stock_items(status=query.get("status", [""])[0] or None)
        rows = "".join(f'<tr><td>{i["id"]}</td><td>{esc(i["product_name"])}</td><td>{status_badge(i["status"])}</td><td><code>{esc(i["content"])}</code></td><td>{esc(i["created_at"])}</td></tr>' for i in items)
        return form + '<div class="card"><table><tr><th>ID</th><th>Sản phẩm</th><th>Trạng thái</th><th>Nội dung</th><th>Ngày nhập</th></tr>' + rows + '</table></div>'

    def _orders_table(self, orders: list[dict], compact: bool = False) -> str:
        actions = "" if compact else "<th>Thao tác</th>"
        rows = []
        for o in orders:
            buttons = ""
            if not compact:
                buttons = f'''<td><form method="post" action="/orders/cancel" style="display:inline">{self._form_csrf()}<input type="hidden" name="order_id" value="{o["id"]}"><button class="small secondary">Hủy</button></form> <form method="post" action="/orders/refund" style="display:inline">{self._form_csrf()}<input type="hidden" name="order_id" value="{o["id"]}"><button class="small danger">Refund</button></form></td>'''
            rows.append(f'<tr><td>#{o["id"]}<br><span class="muted">{esc(o["public_code"])}</span></td><td>{esc(o.get("username") or o.get("telegram_id"))}</td><td>{esc(o["product_name"])}</td><td>{money(o["total_amount_minor"], o["currency"])}</td><td>{status_badge(o["status"])}</td><td>{esc(o["created_at"])}</td>{buttons}</tr>')
        return '<table><tr><th>Đơn</th><th>Khách</th><th>Sản phẩm</th><th>Tiền</th><th>TT</th><th>Ngày</th>' + actions + '</tr>' + "".join(rows) + '</table>'

    def _orders(self, query: dict[str, list[str]]) -> str:
        status = query.get("status", [""])[0] or None
        filters = '<div class="toolbar"><a class="btn secondary small" href="/orders">Tất cả</a><a class="btn secondary small" href="/orders?status=awaiting_payment">Chờ</a><a class="btn secondary small" href="/orders?status=delivered">Đã giao</a><a class="btn secondary small" href="/orders?status=cancelled">Hủy</a><a class="btn secondary small" href="/orders?status=refunded">Refund</a></div>'
        return '<div class="card">' + filters + self._orders_table(self.service.list_orders(status=status, limit=200)) + '</div>'

    def _users(self) -> str:
        rows = "".join(f'<tr><td>{u["id"]}</td><td>{esc(u["telegram_id"])}</td><td>@{esc(u["username"] or "")}</td><td>{esc(u["full_name"] or "")}</td><td>{u["order_count"]}</td><td>{money(u["spent_minor"], "VND")}</td><td>{esc(u["created_at"])}</td></tr>' for u in self.service.list_users())
        return '<div class="card"><table><tr><th>ID</th><th>Telegram</th><th>Username</th><th>Tên</th><th>Đơn</th><th>Đã mua</th><th>Ngày</th></tr>' + rows + '</table></div>'

    def _wallets(self) -> str:
        rows = "".join(f'<tr><td>{w["user_id"]}</td><td>@{esc(w["username"] or "")}</td><td>{esc(w["currency"])}</td><td>{money(w["balance_minor"], w["currency"])}</td><td>{esc(w["updated_at"])}</td></tr>' for w in self.service.list_wallets())
        form = f'<div class="card"><h3>Cộng/trừ ví thủ công</h3><form method="post" action="/wallets/adjust" class="form-grid">{self._form_csrf()}<label>User ID<input name="user_id" required></label><label>Loại<select name="direction"><option value="credit">Cộng</option><option value="debit">Trừ</option></select></label><label>Tiền tệ<select name="currency"><option>VND</option><option>USDT</option><option>USD</option></select></label><label>Số tiền<input name="amount" required></label><label class="full">Lý do<input name="reason" value="web_admin_adjust"></label><button>Lưu</button></form></div>'
        return form + '<div class="card"><table><tr><th>User ID</th><th>Username</th><th>Tiền tệ</th><th>Số dư</th><th>Cập nhật</th></tr>' + rows + '</table></div>'

    def _finance(self) -> str:
        s = self.service.dashboard()["finance"]
        cash_rows = "".join(f'<tr><td>{esc(r["currency"])}</td><td>{esc(r["provider"])}</td><td>{esc(r["direction"])}</td><td>{money(r["amount_minor"], r["currency"])}</td><td>{money(r["fee_minor"], r["currency"])}</td><td>{r["count"]}</td></tr>' for r in s["cash"])
        wallet_rows = "".join(f'<tr><td>{esc(r["currency"])}</td><td>{money(r["liability_minor"], r["currency"])}</td><td>{r["wallets"]}</td></tr>' for r in s["wallet_liabilities"])
        sales_rows = "".join(f'<tr><td>{esc(r["currency"])}</td><td>{money(r["revenue_minor"], r["currency"])}</td><td>{money(r["cost_minor"], r["currency"])}</td><td>{money(int(r["revenue_minor"] or 0)-int(r["cost_minor"] or 0), r["currency"])}</td><td>{r["orders"]}</td></tr>' for r in s["sales"])
        return f'<div class="grid2"><div class="card"><h3>Cash ledger</h3><table><tr><th>Tiền</th><th>Provider</th><th>Hướng</th><th>Số tiền</th><th>Phí</th><th>Count</th></tr>{cash_rows}</table></div><div class="card"><h3>Nợ ví khách</h3><table><tr><th>Tiền</th><th>Tổng ví</th><th>Số ví</th></tr>{wallet_rows}</table></div></div><div class="card"><h3>Doanh thu/lãi gộp</h3><table><tr><th>Tiền</th><th>Doanh thu</th><th>Giá vốn</th><th>Lãi gộp</th><th>Đơn</th></tr>{sales_rows}</table></div>'

    def _payments(self) -> str:
        form = f'<div class="card"><h3>Xác nhận thanh toán thủ công</h3><form method="post" action="/payments/confirm" class="form-grid">{self._form_csrf()}<label>Mã thanh toán<input name="payment_code" placeholder="ORD... / NAP..." required></label><label>TX ID<input name="tx_id" required></label><label>Số tiền<input name="amount" required></label><label>Tiền tệ<select name="currency"><option>VND</option><option>USDT</option><option>USD</option></select></label><label>Provider<input name="provider" value="bank"></label><button>{tr(self._theme_lang()[0],"confirm_payment")}</button></form></div>'
        events = "".join(f'<tr><td>{e["id"]}</td><td>{esc(e["provider"])}</td><td>{esc(e["provider_tx_id"])}</td><td>{esc(e["payment_code"])}</td><td>{money(e["amount_minor"], e["currency"])}</td><td>{status_badge(e["status"])}</td><td>{esc(e["created_at"])}</td></tr>' for e in self.service.list_payment_events())
        intents = "".join(f'<tr><td>{i["id"]}</td><td>{esc(i["public_code"])}</td><td>{esc(i["provider"])}</td><td>{money(i["amount_minor"], i["currency"])}</td><td>{status_badge(i["status"])}</td><td>{esc(i["created_at"])}</td></tr>' for i in self.service.list_payment_intents())
        return form + f'<div class="grid2"><div class="card"><h3>Payment intents</h3><table><tr><th>ID</th><th>Mã</th><th>Provider</th><th>Tiền</th><th>TT</th><th>Ngày</th></tr>{intents}</table></div><div class="card"><h3>Provider events</h3><table><tr><th>ID</th><th>Provider</th><th>TX</th><th>Mã</th><th>Tiền</th><th>TT</th><th>Ngày</th></tr>{events}</table></div></div>'

    def _settings(self) -> str:
        settings = self.service.get_settings()
        fields = []
        for key in DEFAULT_SETTING_KEYS:
            item = settings.get(key, {"value": "", "is_secret": False})
            typ = "password" if item["is_secret"] and item["value"] else "text"
            value = esc(item["value"])
            fields.append(f'<label>{key}<input type="{typ}" name="{key}" value="{value}"></label>')
        return f'<div class="card"><form method="post" action="/settings" class="form-grid">{self._form_csrf()}{"".join(fields)}<label class="full"><input style="width:auto" type="checkbox" name="write_env"> Ghi ra file .env để áp dụng sau khi restart bot/web</label><button>{tr(self._theme_lang()[0],"save")}</button></form></div>'

    def _audit(self) -> str:
        issues = self.service.audit()
        if not issues:
            return '<div class="card"><span class="status delivered">AUDIT OK</span><p class="muted">Không phát hiện lệch ví, đơn, kho, giao hàng hoặc dòng tiền.</p></div>'
        return '<div class="card">' + "".join(f'<div class="alert err"><b>{esc(i["code"])}</b><br>{esc(i["message"])}</div>' for i in issues) + '</div>'

    def _logs(self) -> str:
        rows = "".join(f'<tr><td>{l["id"]}</td><td>{esc(l["admin_username"] or l["admin_id"])}</td><td>{esc(l["action"])}</td><td>{esc(l["target_type"])}</td><td>{esc(l["target_id"])}</td><td><code>{esc(l["metadata_json"])}</code></td><td>{esc(l["created_at"])}</td></tr>' for l in self.service.audit_logs())
        return '<div class="card"><table><tr><th>ID</th><th>Admin</th><th>Action</th><th>Target</th><th>ID</th><th>Data</th><th>Time</th></tr>' + rows + '</table></div>'


def create_server(db_path: str | Path, *, host: str = "127.0.0.1", port: int = 8080, session_secret: str | None = None, project_root: str | Path | None = None, bootstrap_username: str = "admin", bootstrap_password: str | None = None) -> ThreadingHTTPServer:
    db = Database(db_path)
    service = AdminWebService(db, project_root=project_root)
    service.init(bootstrap_username=bootstrap_username, bootstrap_password=bootstrap_password)
    server = ThreadingHTTPServer((host, port), AdminRequestHandler)
    server.service = service  # type: ignore[attr-defined]
    server.session_secret = session_secret or os.getenv("WEB_SESSION_SECRET") or "change-this-web-session-secret"  # type: ignore[attr-defined]
    return server
