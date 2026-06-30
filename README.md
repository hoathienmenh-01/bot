# NIMO Telegram Shop Bot Complete

Bot bán hàng Telegram cho sản phẩm số: tài khoản, key, license, gói premium. Bản này tập trung vào an toàn tiền/kho/đơn và đã bổ sung **Web Admin hiện đại** để quản lý bằng trình duyệt, không cần sửa file trực tiếp trên điện thoại.

## Trạng thái bản này

- Phiên bản: `1.5.0-premium-admin-ui`
- Core ví/đơn/kho/payment: hoàn thiện ở mức MVP dùng thật cẩn thận.
- Telegram UI: flow khách hàng và admin cơ bản bằng aiogram 3.
- Web Admin: quản lý dashboard, đơn hàng, sản phẩm, danh mục, kho, user, ví, dòng tiền, payment, cấu hình, audit, log admin.
- Giao diện web: responsive, light/dark mode, tiếng Việt/English.
- Test: `45/45 passed`.


### Cập nhật v1.5.0 Web Admin Premium

- Trang **Cấu hình** đã được chia thành từng nhóm rõ ràng: Shop/Admin Telegram, Bot Telegram, Ngân hàng & SePay, Binance Pay, Web Admin.
- Mỗi ô cấu hình có nhãn tiếng Việt, mô tả nhập gì, lấy ở đâu và dùng để làm gì.
- Secret như Bot Token/API key không hiển thị lại trên form; để trống nghĩa là giữ giá trị cũ.
- Trang **Sản phẩm** chỉ hiển thị danh sách và nút **Thêm / Sửa / Xóa** rõ ràng. Form thêm/sửa nằm ở trang riêng.
- Xóa sản phẩm an toàn: chưa có lịch sử thì xóa thật; đã có đơn/bán hàng thì tự ẩn để giữ báo cáo và audit.
- Đổi mật khẩu Web Admin trong trang cấu hình có hiệu lực ngay, không cần chỉnh database/câu lệnh thủ công.

## Chức năng khách hàng Telegram

Menu `/start`:

- 🛒 Mua ngay
- 👤 Hồ sơ
- 📜 Lịch sử mua
- 💰 Ví
- 💬 Hỗ trợ
- 🌐 Ngôn ngữ

Flow mua hàng:

```text
/start
→ Mua ngay
→ chọn danh mục
→ chọn sản phẩm
→ xem giá / mô tả / tồn kho / bảo hành
→ ✅ Mua ngay
→ bot tạo đơn và giữ stock tạm
→ chọn thanh toán bằng ví / ngân hàng / Binance
→ nếu thanh toán thành công, bot giao key/tài khoản
```

## Web Admin

Chạy web admin:

```bash
PYTHONPATH=src python -m nimo_shop.web.main --host 0.0.0.0 --port 8080
```

Mở trên máy tính cùng WiFi:

```text
http://IP_DIEN_THOAI:8080
```

Đăng nhập mặc định nếu chưa đặt env:

```text
admin / admin12345
```

Khuyến nghị đặt trong `.env` trước khi chạy thật:

```env
WEB_ADMIN_USERNAME=admin
WEB_ADMIN_PASSWORD=mat_khau_manh_cua_ban
WEB_SESSION_SECRET=chuoi_ngau_nhien_rat_dai
```

Web Admin có các trang:

- Dashboard: tổng quan shop, đơn gần đây, audit nhanh.
- Đơn hàng: xem, hủy, refund.
- Sản phẩm: thêm/sửa sản phẩm, giá bán, giá vốn, mô tả, bảo hành, bật/tắt.
- Danh mục: thêm/sửa/bật/tắt danh mục.
- Kho hàng: nhập key/account nhiều dòng, xem available/reserved/sold.
- Người dùng: xem user Telegram, số đơn, tổng đã mua.
- Ví: xem số dư, cộng/trừ ví thủ công có log.
- Dòng tiền: cash ledger, doanh thu, giá vốn, lãi gộp, nợ ví khách.
- Thanh toán: xem payment intent, provider event, xác nhận thanh toán thủ công.
- Cấu hình: sửa bank/SePay/Binance/Bot/Web settings, có tùy chọn ghi ra `.env`.
- Audit: kiểm tra lệch ví/ledger, kho/đơn/giao hàng/dòng tiền.
- Log admin: xem thao tác admin.

## Chạy bot + web admin trên Termux

Cài môi trường:

```bash
pkg update && pkg upgrade -y
pkg install python git nano unzip tmux openssh cronie -y
```

Tải code:

```bash
git clone https://github.com/hoathienmenh-01/bot.git
cd bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Chạy test:

```bash
chmod +x scripts/run_full_tests.sh
./scripts/run_full_tests.sh
```

Seed demo:

```bash
PYTHONPATH=src python -m nimo_shop.seed_demo
```

Chạy bot:

```bash
PYTHONPATH=src python -m nimo_shop.main
```

Chạy web admin ở cửa sổ tmux khác:

```bash
PYTHONPATH=src python -m nimo_shop.web.main --host 0.0.0.0 --port 8080
```

## Chạy bot và web bằng tmux

```bash
tmux new -s nimo
cd ~/bot
source .venv/bin/activate
PYTHONPATH=src python -m nimo_shop.main
```

Tạo cửa sổ mới:

```text
Ctrl+B rồi bấm C
```

Chạy web:

```bash
cd ~/bot
source .venv/bin/activate
PYTHONPATH=src python -m nimo_shop.web.main --host 0.0.0.0 --port 8080
```

Thoát mà vẫn chạy:

```text
Ctrl+B rồi bấm D
```

## Quản lý bằng Telegram Admin

```text
/admin
/newcategory Tên danh mục
/addproduct category_id | tên | giá_vnd | giá_vốn_vnd | mô tả | bảo hành
/addstock product_id
key1
key2
/confirm PAYMENT_CODE TX_ID AMOUNT [CURRENCY] [PROVIDER]
/cancel ORDER_ID
/refund ORDER_ID
/sweep
/audit
/orders
/finance
/stock
/users
```

## Quản lý dòng tiền

Có 2 lớp sổ riêng:

1. `ledger_entries`: ledger ví khách, ghi credit/debit theo user, có `idempotency_key`.
2. `cash_ledger`: sổ dòng tiền hệ thống, ghi tiền vào/ra/internal theo provider, fee, reference.

Admin xem được:

- Tiền vào theo provider: bank / Binance / wallet.
- Phí giao dịch nếu có.
- Số dư ví khách đang giữ.
- Doanh thu, giá vốn, lãi gộp.
- Đơn hàng theo trạng thái.
- Tồn kho available/reserved/sold.

## Thanh toán/API

### Ngân hàng / SePay

- `SepayClient` có hàm gọi `/transactions/list`.
- `main.py` có background polling SePay nếu cấu hình `SEPAY_API_KEY`.
- `provider_sync.apply_sepay_transactions()` chuẩn hóa nhiều kiểu field transaction thường gặp.
- Chống cộng tiền 2 lần bằng unique `(provider, provider_tx_id)`.

### VietQR

- Tạo VietQR URL theo `BANK_BIN`, `BANK_ACCOUNT`, `BANK_OWNER`.
- Nội dung chuyển khoản là payment code như `NAP...` hoặc `ORD...`.

### Binance Pay

- Có helper tạo payload Binance Pay v3 `/binancepay/openapi/v3/order` và bot gọi create-order khi `BINANCE_PAY_ENABLED=true` cùng API key/secret.
- Có helper ký request create-order và lưu `provider_ref` từ Binance response vào payment intent.
- Nếu dùng production Binance Pay merchant, cần domain HTTPS để nhận callback/webhook và verify chữ ký trước khi xác nhận giao dịch.

## Test full

```bash
./scripts/run_full_tests.sh
```

Hoặc chạy thủ công:

```bash
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -v
PYTHONPATH=src python -m compileall -q src tests
DATABASE_PATH=$(mktemp -d)/shop.db PYTHONPATH=src python -m nimo_shop.seed_demo
DATABASE_PATH=$(mktemp -d)/shop.db PYTHONPATH=src python -m nimo_shop.audit
```

Kết quả hiện tại:

```text
Ran 43 tests in 1.5s
OK
compileall OK
seed demo OK
audit OK
FULL TEST OK
```

Test bao phủ:

- ví không nhận debit/credit âm hoặc bằng 0;
- idempotency cho wallet ledger, provider transaction và refund;
- không bán trùng stock, không nhập trùng key;
- thanh toán bằng ví không debit/giao hàng 2 lần khi retry;
- thiếu tiền không bán stock;
- hủy/hết hạn trả stock về kho;
- khách chuyển thiếu/chuyển dư/chuyển sau khi đơn hủy hoặc hết hạn không làm mất tiền;
- payment code không khớp vẫn được ghi `external_payment_events` để admin đối soát;
- Binance Pay signature helper và SePay/VietQR helper;
- parser lệnh admin;
- chặn user thao tác đơn hàng không thuộc về mình;
- audit phát hiện lệch ví/ledger và lệch kho/đơn;
- text rendering khách/admin;
- SePay transaction normalizer và idempotent apply;
- Web Admin auth, CSRF, session, dashboard, category/product/stock forms, settings `.env`, wallet adjust và payment confirm.

## Cấu trúc

```text
src/nimo_shop/
├── bot/
├── payments/
├── services/
├── web/
│   ├── app.py
│   ├── main.py
│   ├── security.py
│   └── service.py
├── config.py
├── db.py
├── main.py
├── money.py
├── audit.py
└── seed_demo.py
```

## Lưu ý trước khi bán thật

- Đổi `WEB_ADMIN_PASSWORD`, `WEB_SESSION_SECRET`, `BOT_TOKEN`, `SEPAY_API_KEY` trước khi mở shop.
- Không public web admin trực tiếp ra internet. Nên dùng LAN/VPN/SSH tunnel.
- Nếu chạy trên điện thoại Android/Termux, bật `termux-wake-lock`, tắt tối ưu pin, backup `data/shop.db` hằng ngày.
- Test giao dịch thật nhỏ trước khi nhận tiền lớn.


## First-run setup without editing `.env`

If `BOT_TOKEN` is empty, placeholder, or invalid, the bot launcher will no longer crash. Run:

```bash
PYTHONPATH=src python -m nimo_shop.main
```

It will automatically open Web Admin Setup at `http://127.0.0.1:8080`. Login with `admin / admin12345` if you have not configured another password, then open **Cấu hình / Settings**, enter `BOT_TOKEN`, `ADMIN_IDS`, bank/SePay/Binance values, tick **Ghi ra file .env**, save, and restart the same command.

You can also run the web admin directly anytime:

```bash
PYTHONPATH=src python -m nimo_shop.web.main --host 0.0.0.0 --port 8080
```
