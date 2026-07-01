# NIMO Telegram Shop Bot Complete

Bot bán hàng Telegram cho sản phẩm số: tài khoản, key, license, gói premium. Bản này tập trung vào an toàn tiền/kho/đơn, Web Admin vận hành bằng trình duyệt và giao diện catalog kiểu shop premium.

## Trạng thái bản này

- Phiên bản: `2.8.1-commercial-hardening`
- Core ví/đơn/kho/payment: đã có test chống bán trùng, lệch ví, lệch kho và lỗi thanh toán phổ biến.
- Telegram UI: single-panel, danh mục/sản phẩm có trạng thái còn hàng/hết hàng, sản phẩm có ảnh/icon/custom emoji.
- Đặt trước: sản phẩm hết hàng có nút đặt trước; phí đặt trước cấu hình theo `%` trong Web Admin.
- Web Admin: quản lý dashboard, đơn hàng, đặt trước, sản phẩm, danh mục, kho, user, ví, dòng tiền, payment, cấu hình, audit, backup, coupon, phân quyền, báo cáo.
- Giao diện web: responsive, light/dark mode, tiếng Việt/English.
- Test: `81/81 passed`, compileall OK. Bản này đã thêm regression test cho các lỗi thương mại: webhook bắt buộc secret, đối soát unmatched, preorder refund/100% deposit, backup path traversal, user bị ban và Buyer API idempotency.

### Cập nhật v2.8.1 commercial hardening

- Webhook SePay/Binance không còn chấp nhận request unsigned khi thiếu `WEBHOOK_SHARED_SECRET`; production phải dùng secret/chữ ký.
- Web Admin không còn tạo tài khoản `admin/admin12345`; bắt buộc đặt mật khẩu mạnh qua `.env` hoặc tham số `--password`.
- `WEB_SESSION_SECRET` không còn fallback sang chuỗi mặc định; khi mở ngoài localhost phải có secret đủ mạnh.
- Mã thanh toán/đơn/preorder tăng entropy để giảm rủi ro đoán mã.
- Sửa đối soát giao dịch thật bị lưu `unmatched`: admin có thể reconcile lại cùng `provider_tx_id` mà không bị kẹt duplicate.
- Sửa preorder: hủy preorder đã cọc sẽ hoàn cọc vào ví; preorder cọc 100% tự giao hàng khi nhập kho; nút fulfill không còn đánh dấu hoàn thành nếu chưa tạo/giao đơn.
- Sửa đơn 0đ: không tạo external payment intent 0đ; flow ví/free tự giao hàng.
- Backup restore chặn path traversal trong `media/products`; backup mặc định không kèm `.env`.
- Buyer API thêm idempotency key và dùng đúng `ORDER_EXPIRES_MINUTES`.
- Admin Telegram confirm/cancel/refund và nhập kho đã queue thông báo cho khách/preorder giống Web Admin.

### Cập nhật v2.7.0 gộp thanh toán một bot và hardening thương mại

- Một bot shop có thể dùng chung các phương thức: ví nội bộ, chuyển khoản ngân hàng/VietQR/SePay, Binance Pay/USDT. Không cần tách bot thanh toán riêng.
- Sửa lỗi webhook quan trọng: `/webhook/sepay` giờ khớp đúng payment intent provider `bank`; `/webhook/binance` khớp đúng provider `binance_pay`.
- Khi khách bấm cùng một phương thức thanh toán nhiều lần cho một đơn, hệ thống tái sử dụng mã ORD đang còn hạn thay vì tạo nhiều mã thanh toán gây rối đối soát.
- Phân quyền Web Admin đã được enforce thật: finance/stock/support/viewer không còn toàn quyền như owner.
- Thêm test regression cho webhook SePay/Binance, reuse mã thanh toán, role permission và tùy chọn `WEBHOOK_SHARED_SECRET` chống webhook giả.


### Cập nhật v2.6.0 menu lệnh / API mua hàng / lưới catalog / chính sách dòng trùng

- Khi chạy bot, hệ thống tự set command menu Telegram để khách thấy nút **Menu** ở góc trái với `/start`, `/menu`, `/products`, `/wallet`, `/search`, `/taidon`.
- `/start` mở thẳng màn hình catalog/sản phẩm kiểu shop: lời chào, số dư ví, danh mục dạng lưới và nút `🔄 Làm mới`.
- Danh mục hiển thị theo lưới 3 ô mỗi hàng; mỗi danh mục/sản phẩm có trạng thái `🟢` còn hàng và `🔴` hết hàng.
- Thêm khu vực `🔗 API` cho khách: bot tạo `tgb_...` API key, cho phép tạo lại key nếu lộ.
- Thêm Buyer API:
  - `GET /api/telegram-buyer/products` — liệt kê sản phẩm/tồn kho.
  - `POST /api/telegram-buyer/purchase` — mua bằng số dư ví và trả dữ liệu giao hàng JSON.
- Tài liệu API public ở `/t/api-guide`. URL public có thể cấu hình bằng `API_PUBLIC_BASE_URL`.
- Thêm `STOCK_DUPLICATE_POLICY`: `allow` / `skip` / `reject`. Mặc định `allow` để không còn lỗi khi nhập các dòng tài khoản/key trùng nhau theo nhu cầu vận hành.
- Sửa migration SQLite: database cũ có ràng buộc unique kho sẽ được tự chuyển sang bảng cho phép duplicate an toàn, vẫn giữ nguyên ID và lịch sử giao hàng.

### Cập nhật v2.5.0 danh mục theo kho và đặt trước

- Danh mục hiển thị như thư mục sản phẩm: icon + tên + trạng thái tồn kho.
- Nút danh mục/sản phẩm dùng ký hiệu `🟢` khi còn hàng và `🔴` khi hết hàng. Telegram Bot API không cho đổi màu thật của nút inline; cách ổn định là dùng emoji/trạng thái trong text nút.
- Khi bấm danh mục, bot hiển thị các sản phẩm trong danh mục đó, giống luồng trong ảnh mẫu.
- Khi sản phẩm hết hàng, bot không hiện nút mua thường; thay bằng `🧾 Đặt trước 1` và `✍️ Nhập số lượng đặt trước`.
- Phí đặt trước chỉnh trong `Cấu hình → Giao hàng cho khách → Phí đặt trước (%)` hoặc biến `.env` `PREORDER_DEPOSIT_PERCENT`.
- Web Admin có trang `Đặt trước` để xem, hủy hoặc đánh dấu đã xử lý đơn đặt trước.
- Danh mục trong Web Admin có thêm `Icon danh mục`.


### Cập nhật v1.7.0 số lượng mua / ví / ngôn ngữ / chạy một lệnh

- Khách có thể chọn nhanh số lượng mua: **Mua 1 / Mua 2 / Mua 3 / Mua 5** theo tồn kho.
- Khách có thể bấm **Nhập số lượng khác** rồi nhắn số lượng tự do, hoặc dùng lệnh `/mua PRODUCT_ID SO_LUONG`.
- Khi tạo đơn, bot hiển thị **đơn giá, tổng tiền, số dư ví hiện tại và số tiền còn thiếu** nếu muốn thanh toán bằng ví.
- Menu `/start` hiển thị số dư ví của khách ngay ở màn hình đầu.
- Nút thanh toán đơn có thêm **Nạp ví** để khách bổ sung tiền nhanh.
- Thêm ngôn ngữ phổ biến: Tiếng Việt, English, 中文, 日本語, 한국어, ไทย, Español, Français. Menu chính đổi theo ngôn ngữ đã chọn.
- Thêm lệnh chạy một lần cả bot và web admin: `PYTHONPATH=src python -m nimo_shop.run_all --host 0.0.0.0 --port 8080` hoặc `./scripts/run_all.sh`.

### Cập nhật v1.6.0 sửa nạp ví/kho/ví thủ công/thông báo

- Khách có thể nạp ví tự do bằng nút **Nạp số tiền khác** hoặc lệnh `/nap 150000`; các mốc cố định vẫn còn để thao tác nhanh.
- Nhập kho không còn bỏ qua dòng trùng im lặng. Nếu trùng trong textarea hoặc đã có trong sản phẩm, hệ thống báo lỗi và không nhập.
- Cộng/trừ ví thủ công trong Web Admin chấp nhận **Telegram ID**, **@username** hoặc **ID nội bộ**; nếu cộng tiền cho Telegram ID chưa có hồ sơ, hệ thống tự tạo user tối thiểu.
- Sửa sản phẩm có checkbox gửi thông báo cập nhật qua bot; web tạo hàng chờ và bot gửi nền khi đang chạy.
- Xác nhận thanh toán thủ công qua web chỉ xử lý một lần mỗi POST, tiếp tục dùng provider_tx_id để chống cộng tiền 2 lần.

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
→ chọn số lượng muốn mua hoặc nhập số lượng tự do
→ bot tạo đơn và giữ stock tạm
→ chọn thanh toán bằng ví / ngân hàng / Binance
→ nếu thanh toán thành công, bot giao key/tài khoản
```

## Chạy bot + web admin bằng một lệnh

Trên máy tính hoặc Termux, sau khi đã cài requirements và có `.env`:

```bash
PYTHONPATH=src python -m nimo_shop.run_all --host 0.0.0.0 --port 8080
```

Hoặc:

```bash
chmod +x scripts/run_all.sh
./scripts/run_all.sh
```

Lệnh này mở Web Admin tại `http://127.0.0.1:8080` và chạy bot Telegram trong cùng một tiến trình. Nếu `BOT_TOKEN` còn trống/sai, Web Admin vẫn chạy để bạn vào Cấu hình nhập token rồi restart.

## Web Admin

Chạy web admin:

```bash
PYTHONPATH=src python -m nimo_shop.web.main --host 0.0.0.0 --port 8080
```

Mở trên máy tính cùng WiFi:

```text
http://IP_DIEN_THOAI:8080
```

Bản thương mại không còn mật khẩu mặc định. Hãy đặt trong `.env` hoặc truyền `--password` khi chạy lần đầu:


```env
WEB_ADMIN_USERNAME=admin
WEB_ADMIN_PASSWORD=mat_khau_manh_cua_ban
WEB_SESSION_SECRET=chuoi_ngau_nhien_rat_dai_it_nhat_32_ky_tu
WEBHOOK_SHARED_SECRET=chuoi_ngau_nhien_cho_webhook
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

- Bắt buộc đặt `WEB_ADMIN_PASSWORD`, `WEB_SESSION_SECRET`, `WEBHOOK_SHARED_SECRET`, `BOT_TOKEN`, `SEPAY_API_KEY` trước khi mở shop.
- Không public web admin trực tiếp ra internet. Nên dùng LAN/VPN/SSH tunnel.
- Nếu chạy trên điện thoại Android/Termux, bật `termux-wake-lock`, tắt tối ưu pin, backup `data/shop.db` hằng ngày.
- Test giao dịch thật nhỏ trước khi nhận tiền lớn.


## First-run setup without editing `.env`

If `BOT_TOKEN` is empty, placeholder, or invalid, the bot launcher will no longer crash. Run:

```bash
PYTHONPATH=src python -m nimo_shop.main
```

It will automatically open Web Admin Setup at `http://127.0.0.1:8080` and print a temporary setup password in the terminal if you did not configure `WEB_ADMIN_PASSWORD`. Open **Cấu hình / Settings**, enter `BOT_TOKEN`, `ADMIN_IDS`, bank/SePay/Binance values, set permanent `WEB_ADMIN_PASSWORD`, `WEB_SESSION_SECRET`, `WEBHOOK_SHARED_SECRET`, tick **Ghi ra file .env**, save, and restart the same command.

You can also run the web admin directly anytime:

```bash
PYTHONPATH=src python -m nimo_shop.web.main --host 0.0.0.0 --port 8080
```

## v1.8 additions

### Customer product search
Telegram users can search products from the bot menu using `🔎 Tìm sản phẩm` or command:

```text
/search chatgpt
/timkiem canva
```

### Web Admin pages
- `/bots`: manage multiple bot tokens and choose the primary running bot.
- `/notifications`: create bot notifications/broadcasts; running bot sends queued messages to users who used `/start`.
- `/backup`: download/restore backup ZIP for moving data between Android phone and PC.
- `/guide`: step-by-step BotFather, bank/SePay, Binance, backup and operation guide.

### Backup
From Web Admin: `Backup dữ liệu` → download backup.

CLI backup:

```bash
./scripts/backup_data.sh
```

### One-command run

```bash
PYTHONPATH=src python -m nimo_shop.run_all --host 0.0.0.0 --port 8080
```

## v1.9 notes: large orders and cleaner Telegram chat

### Single-panel navigation
Most inline button actions now edit the same Telegram bot message instead of posting a new bot message. This keeps the customer chat short and easier to navigate.

### Large order delivery by file
For high-quantity orders, the bot sends a TXT file containing all delivered keys/accounts instead of placing every line into the chat. Customers can also download a delivered order again:

```text
/taidon ORD1234ABCD
/download_order ORD1234ABCD
/export_order ORD1234ABCD
```

### Run bot + web together

```bash
PYTHONPATH=src python -m nimo_shop.run_all --host 0.0.0.0 --port 8080
```

## Delivery file policy

Web Admin → Cấu hình → Giao hàng cho khách now supports:

- `auto`: small orders are shown directly in chat; large/long orders are sent as TXT files.
- `file_only`: every delivered order is sent as a TXT file, including quantity 1.
- `inline_and_file`: small orders are shown in chat and also sent as TXT files.

Environment variables:

```env
DELIVERY_OUTPUT_MODE=auto
DELIVERY_FILE_THRESHOLD=20
```

Use `file_only` if you want all customers to download a file for easier saving and cleaner chats.

## v2.1 Operations Center

New Web Admin pages:

- **Trạng thái**: check database, token format, SePay/Bank, Binance and low-stock overview.
- **Nhập/Xuất**: import products/categories/stock by CSV.
- **Báo cáo**: download CSV reports for orders, products, stock, wallets, finance and users.
- **Đối soát**: review unmatched payment events and mark them reviewed.
- **Mã giảm giá**: create/edit/delete coupon codes.
- **Phân quyền**: create admin accounts with roles: owner, finance, stock, support, viewer.
- **Lịch sử giao hàng**: track generated/downloaded order delivery files.
- **Cảnh báo kho**: view low-stock products and queue low-stock notifications.

Webhook endpoints added:

```text
POST /webhook/sepay
POST /webhook/binance
```

Supported request fields: `tx_id` / `transaction_id`, `amount`, `currency`, `description` / `content` / `note`. Unmatched or malformed payments are saved for reconciliation.

Run everything:

```bash
PYTHONPATH=src python -m nimo_shop.run_all --host 0.0.0.0 --port 8080
```


## Nhập kho nhanh bằng file

Trong Web Admin vào **Kho hàng** hoặc từ **Sản phẩm → Nhập kho**.

Hỗ trợ:

- `.txt`: mỗi dòng là một tài khoản/key.
- `.csv`: dữ liệu từ Excel/Google Sheet.
- `.docx`: file Word mới; hệ thống đọc từng dòng/đoạn văn bản.
- Dạng phổ biến: `UID|Mật khẩu|Cookie|Token`, `Email|Mật khẩu`, hoặc một license/key mỗi dòng.

Khi nhập 100, 1000 hoặc 10000 tài khoản, hãy tải file lên thay vì dán tay. Hệ thống sẽ tự nhận diện định dạng, kiểm tra trùng trong cùng sản phẩm và báo lỗi nếu trùng. Cookie/token được che trong preview/log, nhưng nội dung gốc vẫn được lưu để giao cho khách sau khi thanh toán.

Lưu ý: không dùng file Word `.doc` cũ; hãy lưu thành `.docx`, `.txt` hoặc `.csv`.

## v2.3: Định dạng dữ liệu kho theo từng sản phẩm

Mỗi sản phẩm có thể dùng định dạng kho riêng. Vào **Sản phẩm → Sửa sản phẩm** và cấu hình:

- **Định dạng dữ liệu kho**: Auto, Raw, Email|Mật khẩu, Email / Mật khẩu, Email|Mật khẩu|2FA, UID|Mật khẩu|Cookie|Token, CSV...
- **Nhãn cột dữ liệu**: ví dụ `Email|Mật khẩu|2FA` hoặc `UID|Mật khẩu|Cookie|Token`.
- **Ví dụ nhập kho**: ghi mẫu để sau này admin nhìn là biết file cần có dạng gì.
- **Kiểu giao hàng**: giao nguyên dòng hoặc giao có nhãn cột.

Khi nhập kho, nên để **Kiểu nhận diện = Theo cấu hình sản phẩm**. Như vậy mỗi sản phẩm được xử lý theo đúng kiểu dữ liệu của nó:

- `email@example.com|password|2FA` → lưu thành một hàng giao có 3 cột.
- `email@example.com / password` → tự chuẩn hóa thành `email@example.com|password`.
- `UID|password|cookie|token` → giữ đủ dữ liệu, preview/log che bớt cookie/token.
- Key/license/link mỗi dòng → giữ nguyên từng dòng.

Dữ liệu giao cho khách luôn lấy từ `stock_items.content`; nếu sản phẩm bật giao có nhãn, file giao hàng sẽ hiển thị thành các dòng có nhãn như Email, Mật khẩu, 2FA, Cookie, Token.

## Product images, icons and premium catalog UI

From v2.4.0, each product can have:

- A normal icon/emoji for compact product list buttons.
- A Telegram custom emoji ID for Premium-style detail captions.
- A product image uploaded from Web Admin.
- A short description and long description for richer product cards.

Use:

```text
Web Admin → Products → Add/Edit product
```

Upload images as JPG, PNG or WebP. The limit is 5MB per image. Images are stored in:

```text
media/products/
```

Backups include `media/products/`, so transferring from phone to computer preserves product images. When the bot successfully sends a product photo, it stores Telegram `file_id` in the database so future sends can use Telegram cache instead of uploading again.

