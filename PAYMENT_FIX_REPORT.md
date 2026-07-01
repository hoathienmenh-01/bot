# PAYMENT_FIX_REPORT

Bản sửa tập trung vào các lỗi thanh toán P0/P1 đã phát hiện ở luồng ngân hàng, Pay2S/SePay và Binance/USDT.

## Đã sửa

1. Chuẩn hóa provider nội bộ:
   - `sepay`, `pay2s`, `casso`, `manual`, `custom` đều map về provider nội bộ `bank`.
   - `binance_pay`, `binance_manual`, `usdt_bep20` được tách rõ.
   - Provider cũ trong database vẫn được match để không làm hỏng pending intent legacy.

2. Chặn thanh toán Binance/USDT sai đơn vị cho đơn VND:
   - Đơn VND không còn được tạo intent `binance_pay`, `binance_manual`, `usdt_bep20` trực tiếp.
   - Muốn thanh toán crypto cho đơn VND cần module FX quote VND -> USDT riêng.

3. Chống double-credit Pay2S/SePay:
   - Canonical `provider_tx_id` theo source thật: `pay2s:<transaction_id>`, `sepay:<transaction_id>`.
   - Không dùng `_bank_account_id` nội bộ để biến cùng một giao dịch thật thành nhiều giao dịch khác nhau.
   - Pay2S webhook và poller cùng transaction id sẽ idempotent, không cộng tiền lần 2.

4. Gắn bank account vào payment intent:
   - Khi khách chọn tài khoản ngân hàng, intent lưu `bank_account_id` trong `metadata_json`.
   - Khi Pay2S/SePay trả account id/account number, hệ thống kiểm tra khớp tài khoản đã chọn.
   - Sai tài khoản sẽ bị status `account_mismatch`, không tự cộng ví/giao hàng.

5. Pay2S webhook:
   - Bearer token được resolve về đúng bank account cấu hình.
   - Webhook dùng chung normalizer với poller để tránh lệch transaction id.
   - Response vẫn giữ `results` dạng dễ đọc cho test/tool cũ, đồng thời có `detailed_results` để debug.

6. Poller lưu giao dịch thiếu mã:
   - Giao dịch có transaction id/amount/description nhưng thiếu NAP/ORD được lưu status `missing_code` để admin đối soát.

7. Nhập tiền VND linh hoạt hơn:
   - Hỗ trợ `1.000.000`, `1,000,000`, `1 000 000`, `1000000đ` cho VND.

## Test đã chạy

```bash
PYTHONPATH=src python3 -m pytest -q
# 100 passed
```

## Test regression mới

- Pay2S webhook + poller cùng transaction id không double-credit.
- Bank account mismatch không auto-credit.
- Crypto/Binance intent cho đơn VND bị chặn nếu chưa có FX quote.
- Bank provider alias `sepay/pay2s/casso/manual` vẫn khớp intent `bank`.

## Bổ sung v2.8.16: thông báo nhận tiền và xóa QR cũ

- Sửa lỗi webhook Pay2S xử lý cộng ví thành công nhưng không báo khách trên Telegram.
- Webhook web thread giờ queue thông báo `payment_success` cho bot process gửi ra Telegram.
- Khi tạo QR nạp ví hoặc QR thanh toán đơn qua ngân hàng, bot lưu lại `chat_id` và `message_id` của tin hướng dẫn/QR trong `payment_intents.metadata_json`.
- Khi tiền được xác nhận, bot xóa các tin hướng dẫn/QR cũ rồi gửi tin mới: `Nạp tiền thành công` hoặc `Thanh toán thành công`.
- Thêm migration `bot_notifications.metadata_json` để lưu action xóa message cũ.
- Test mới xác nhận webhook-applied payment tạo notification và metadata xóa QR đúng.

## v2.8.17 follow-up: paid order delivery after provider webhook

User-reported issue: Pay2S QR order payment was confirmed, but Telegram only sent a success receipt and did not send the purchased goods automatically. The generic `/taidon` hint also caused Telegram to send `/taidon` without the order code, showing the help prompt.

Fixes:

- `queue_payment_success_notice()` now writes `delivery_order_id` into notification metadata for `order_delivered` results.
- `notification_send_loop()` deletes stale QR/instruction messages, sends the success receipt, then sends the actual order delivery payload/file.
- The provider poller direct notification path now sends delivery payloads too.
- Success message now contains the exact fallback command `/taidon ORD...`.
- Native Binance Pay webhook no longer queues duplicate success notifications and uses the same auto-delivery path.

Verification:

- `PYTHONPATH=src python3 -m compileall -q src tests`
- `PYTHONPATH=src python3 -m pytest -q`
- Result: `103 passed`.
