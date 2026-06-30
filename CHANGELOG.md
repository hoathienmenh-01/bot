# CHANGELOG

## v1.5.0-premium-admin-ui

- Làm lại Web Admin theo hướng premium, dễ nhìn hơn, nút bấm rõ ràng hơn và responsive tốt hơn.
- Làm lại trang Cấu hình theo nhóm: Shop/Admin Telegram, Bot Telegram, Ngân hàng & SePay, Binance Pay, Web Admin.
- Thêm hướng dẫn tiếng Việt chi tiết cho từng ô cấu hình: nhập gì, lấy ở đâu, dùng để làm gì.
- Secret như BOT_TOKEN/SEPAY/BINANCE không còn hiện ngược ra form; để trống nghĩa là giữ giá trị cũ.
- Đổi WEB_ADMIN_USERNAME/WEB_ADMIN_PASSWORD trong web có hiệu lực ngay trong database admin, không cần sửa tay.
- Làm lại trang Sản phẩm: chỉ hiện danh sách sản phẩm và nút Thêm/Sửa/Xóa rõ ràng; form thêm/sửa tách riêng.
- Thêm xóa sản phẩm an toàn: sản phẩm chưa có lịch sử sẽ xóa thật; sản phẩm đã có đơn/bán hàng sẽ ẩn để giữ audit/doanh thu.
- Chặn xóa sản phẩm đang có đơn chờ hoặc stock đang được giữ để tránh mất hàng/lệch đơn.
- Việt hóa các nhãn gây rối trong web admin như Payment intents, Provider events, Cash ledger.
- Bổ sung test cho sửa/xóa sản phẩm, UI sản phẩm, form cấu hình, bảo toàn secret khi cập nhật settings, đổi mật khẩu web.
- Full test: 45/45 passed; compileall, seed demo và audit đều OK.

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
