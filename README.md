# NIMO Telegram Shop Bot Complete

Bot bán hàng Telegram cho sản phẩm số: tài khoản, key, license, gói premium. Bản này được dựng lại sạch, tập trung vào an toàn tiền/kho/đơn và đã hoàn thiện thêm flow Telegram để có thể chạy thử bot thật sau khi cấu hình token.

## Trạng thái bản này

- Phiên bản: `1.3.0`
- Core ví/đơn/kho/payment: hoàn thiện ở mức MVP dùng thật cẩn thận.
- Telegram UI: đã có flow khách hàng và admin cơ bản bằng aiogram 3.
- Test: `40/40 passed`.
- Đã bổ sung audit dữ liệu, khóa thao tác order theo đúng chủ sở hữu user, và Binance Pay merchant create-order flow khi cấu hình API key. Webhook public HTTPS cho Binance vẫn cần triển khai trên domain/HTTPS thật nếu muốn auto callback production.

## Chức năng khách hàng

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
→ 🛒 Mua ngay
→ chọn danh mục
→ chọn sản phẩm
→ xem giá / mô tả / tồn kho / bảo hành
→ ✅ Mua ngay
→ bot tạo đơn và giữ stock tạm
→ chọn thanh toán bằng ví / ngân hàng / Binance
→ nếu thanh toán thành công, bot giao key/tài khoản
```

Ví:

- Xem số dư `VND`, `USDT`, `USD`.
- Nạp ví theo mức nhanh: 50k, 100k, 200k, 500k.
- Nạp qua ngân hàng tạo mã thanh toán + nội dung chuyển khoản + VietQR URL.
- Khi SePay polling nhận giao dịch đúng mã, bot tự cộng ví hoặc giao hàng.

## Chức năng admin Telegram

Menu `/admin`:

- 📦 Đơn chờ duyệt
- 💵 Dòng tiền
- ➕ Thêm sản phẩm
- 📥 Nhập kho
- 👥 Khách hàng
- 📊 Thống kê

Lệnh admin:

```text
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

Ví dụ:

```text
/newcategory ChatGPT
/addproduct 1 | ChatGPT Plus 1 tháng | 150000 | 100000 | Tài khoản dùng 30 ngày | 1 đổi 1 trong thời hạn bảo hành
/addstock 1
email1@example.com|pass1
email2@example.com|pass2
```

Manual confirm dùng khi API ngân hàng/Binance chưa tự nhận được tiền:

```text
/confirm ORDABCDEF12 TXBANK123 150000 VND bank
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

- Có helper tạo payload Binance Pay v3 `/binancepay/openapi/v3/order` và bot sẽ gọi create-order khi `BINANCE_PAY_ENABLED=true` cùng API key/secret.
- Có helper ký request create-order và lưu `provider_ref` từ Binance response vào payment intent.
- Chưa dựng HTTP webhook server public trong package này; nếu dùng production Binance Pay merchant, cần domain HTTPS để nhận callback, verify chữ ký theo tài liệu Binance, rồi gọi `PaymentService.confirm_provider_transaction()` hoặc dùng `/confirm` dự phòng.

## Kho hàng

Stock có 3 trạng thái:

- `available`
- `reserved`
- `sold`

Khi tạo đơn, bot giữ hàng tạm bằng `reserved` và `reserved_until`. Nếu đơn hủy/hết hạn, stock trả về `available`. Khi thanh toán thành công, stock chuyển `sold`, nội dung giao hàng ghi vào `deliveries`.

## Cài đặt

```bash
cd nimo_telegram_shop_bot_complete
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Cấu hình quan trọng trong `.env`:

```env
BOT_TOKEN=token_botfather
ADMIN_IDS=telegram_id_cua_ban
DATABASE_PATH=data/shop.db
BANK_ENABLED=true
SEPAY_API_KEY=...
BANK_BIN=970436
BANK_ACCOUNT=...
BANK_OWNER=...
SUPPORT_CONTACT=@username_admin
```

Tạo dữ liệu demo:

```bash
PYTHONPATH=src python -m nimo_shop.seed_demo
```

Chạy bot:

```bash
PYTHONPATH=src python -m nimo_shop.main
```

Chạy test full:

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

## Kết quả test hiện tại

```text
Ran 40 tests in 0.720s
OK
compileall OK
seed demo OK
audit OK
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
- SePay transaction normalizer và idempotent apply.

## Cấu trúc

```text
src/nimo_shop/
├── bot/
│   ├── admin_commands.py
│   ├── app.py
│   ├── keyboards.py
│   └── views.py
├── payments/
│   ├── binance_pay.py
│   └── sepay.py
├── services/
│   ├── audit.py
│   ├── catalog.py
│   ├── finance.py
│   ├── orders.py
│   ├── payments.py
│   ├── provider_sync.py
│   ├── users.py
│   └── wallet.py
├── config.py
├── db.py
├── main.py
├── money.py
├── audit.py
└── seed_demo.py
```

## Lưu ý trước khi bán thật

Bản này đã chắc hơn ở lõi tiền/kho/đơn, nhưng trước khi nhận tiền thật quy mô lớn bạn vẫn nên chạy thử bằng tài khoản bot riêng, dùng sản phẩm demo, kiểm tra SePay/Binance với giao dịch nhỏ, rồi mới đưa vào vận hành. Không public database hoặc token bot. Nếu triển khai Binance Pay merchant webhook, bắt buộc dùng HTTPS và verify chữ ký webhook trước khi gọi xác nhận giao dịch.
