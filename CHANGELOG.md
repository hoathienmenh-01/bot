# CHANGELOG

## v1.4.0-web-admin

- Thêm Web Admin nhẹ, chạy bằng Python stdlib `http.server`, không cần Node/React/FastAPI.
- Thêm login admin bằng PBKDF2 password hash.
- Thêm signed session cookie và CSRF token cho POST form.
- Thêm giao diện hiện đại responsive, hỗ trợ sáng/tối và tiếng Việt/English.
- Thêm trang Dashboard, Orders, Products, Categories, Stock, Users, Wallets, Finance, Payments, Settings, Audit, Logs.
- Thêm nhập kho bằng textarea nhiều dòng.
- Thêm sửa sản phẩm/danh mục bằng form.
- Thêm xác nhận thanh toán thủ công qua web.
- Thêm cộng/trừ ví thủ công qua web.
- Thêm cấu hình bot/bank/SePay/Binance/Web trong web và ghi ra `.env`.
- Thêm `admin_accounts`, `app_settings`, `admin_audit_logs`.
- Thêm tests web admin/auth/CSRF/settings/payment.
- Full test tăng từ 40 lên 43 test.

## v1.3.0-stable

- Bổ sung audit dữ liệu.
- Chặn user thao tác order không thuộc về mình.
- Làm sạch reservation khi stock sold.
- Bổ sung Binance Pay create-order flow.


## v1.4.1 - First-run web setup

- Bot launcher no longer crashes when `BOT_TOKEN` is missing, placeholder, or malformed.
- `python -m nimo_shop.main` now starts Web Admin Setup automatically so admins can configure everything from the browser.
- `.env.example` now leaves `BOT_TOKEN` blank to avoid aiogram `TokenValidationError` during first-run setup.
- Added regression tests for first-run setup token validation.
