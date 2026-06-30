# Changelog

## 1.3.0

- Sửa lỗi bảo mật/logic: callback thanh toán/hủy/tạo payment intent giờ kiểm tra đơn thuộc đúng user bấm nút.
- Thêm `OrderOwnershipError` và test chống user thao tác nhầm/đụng đơn của người khác.
- Khi giao hàng, stock `sold` được xóa sạch các field reservation cũ để audit không bị nhiễu.
- Thêm `AuditService` và CLI `python -m nimo_shop.audit` để kiểm tra lệch ví/ledger, kho/đơn, delivery và cash ledger.
- Thêm admin `/audit`, `/orders`, `/finance`, `/stock`, `/users`.
- Bổ sung Binance Pay merchant create-order trong Telegram flow nếu bật `BINANCE_PAY_ENABLED=true` và có API key/secret.
- Thêm `BINANCE_PAY_RETURN_URL`, `BINANCE_PAY_WEBHOOK_URL` vào `.env.example`.
- Thêm `scripts/run_full_tests.sh` chạy unit test + compile + seed demo + audit.
- Tăng test từ 36 lên 40 test.


## 1.2.0

- Hoàn thiện flow Telegram khách hàng bằng aiogram:
  - `/start`, menu chính, mua ngay, danh mục, sản phẩm, tạo đơn, thanh toán ví/ngân hàng/Binance, hủy đơn, ví, lịch sử, hồ sơ, hỗ trợ, ngôn ngữ.
- Hoàn thiện flow admin Telegram:
  - `/admin`, xem đơn chờ, dòng tiền, khách hàng, tồn kho, thêm danh mục, thêm sản phẩm, nhập kho, confirm giao dịch, cancel, refund, sweep hết hạn.
- Thêm `bot/views.py` để render text HTML an toàn, escape nội dung hàng/key/user input.
- Thêm `bot/admin_commands.py` để parse lệnh admin độc lập và có test.
- Thêm `services/provider_sync.py` để chuẩn hóa và áp dụng transaction từ SePay/API ngân hàng.
- Thêm background SePay polling trong `main.py` khi có `SEPAY_API_KEY`.
- Thêm `seed_demo.py` để tạo dữ liệu demo chạy thử nhanh.
- Cập nhật `.env.example` với `SUPPORT_CONTACT` và `SEPAY_POLL_SECONDS`.
- Tăng test từ 31 lên 36 test.

## 1.1.0

- Review lại lõi tiền/kho/đơn theo hướng dùng tiền thật.
- Sửa lỗi nguy hiểm: `WalletService.debit` thiếu chặn số âm, có thể làm tăng số dư bằng giao dịch debit âm.
- Sửa lỗi thanh toán ngoài đến muộn: đơn đã hủy/hết hạn không còn bị giao hàng; tiền được cộng vào ví user để đối soát.
- Sửa xử lý khách chuyển thiếu/chuyển dư: chuyển thiếu được cộng ví, không giao hàng; chuyển dư được giao hàng và cộng phần dư vào ví.
- Sửa xử lý payment code đã confirm nhưng có giao dịch mới: không bỏ qua tiền, cộng vào ví user.
- Sửa `create_order_payment_intent` cho đơn hết hạn: hủy đơn và trả stock về kho trước khi báo lỗi.
- Thêm unique constraint chống nhập trùng stock cùng sản phẩm.
- Thêm `refund_to_wallet` idempotent cho đơn đã giao/đã thanh toán.
- Thêm SQLite connection class tự đóng connection khi dùng `with db.connect()`.
- Tăng test từ 8 lên 31 test, bao phủ wallet, order, stock, payment events, refund, Binance Pay helper, SePay/VietQR helper và config parsing.

## 1.0.0

- Dựng lại lõi bot shop sạch, không phụ thuộc vào source cũ.
- Thêm database schema có wallet ledger, cash ledger, payment intents, external payment events.
- Thêm order reserve stock, release stock, delivery log.
- Thêm wallet payment atomic trong DB transaction.
- Thêm nạp ví qua payment intent, chống cộng tiền 2 lần.
- Thêm thanh toán đơn trực tiếp qua provider ngoài.
- Thêm Binance Pay v3 signed request helper.
- Thêm SePay polling helper và VietQR URL.
- Thêm finance summary cho admin.
- Thêm test suite core.
