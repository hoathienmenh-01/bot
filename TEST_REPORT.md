# TEST REPORT - NIMO Shop v1.5.0 Premium Admin UI

Ngày kiểm thử: 2026-06-30

## Lệnh đã chạy

```bash
./scripts/run_full_tests.sh
```

Script thực hiện:

```bash
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -v
PYTHONPATH=src python -m compileall -q src tests
DATABASE_PATH=$(mktemp -d)/shop.db PYTHONPATH=src python -m nimo_shop.seed_demo
DATABASE_PATH=$(mktemp -d)/shop.db PYTHONPATH=src python -m nimo_shop.audit
```

## Kết quả

```text
Ran 45 tests in 1.694s
OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

## Phạm vi test đáng chú ý

- Core ví/đơn/kho/payment vẫn pass toàn bộ test cũ.
- Chống double-credit provider transaction.
- Chống bán trùng stock.
- Xử lý thiếu tiền/dư tiền/chuyển tiền muộn.
- Refund idempotent.
- Order ownership guard.
- Binance Pay helper và SePay/VietQR helper.
- First-run setup không crash khi BOT_TOKEN trống/mẫu/sai format.
- Web Admin login/session/CSRF.
- Dashboard/categories/products/stock/settings HTTP flow.
- Trang sản phẩm mới: danh sách + nút Thêm/Sửa/Xóa rõ ràng.
- Service sửa sản phẩm.
- Service xóa sản phẩm an toàn: hard delete khi chưa có lịch sử, soft hide khi đã có lịch sử.
- Trang cấu hình mới có hướng dẫn tiếng Việt theo nhóm.
- Secret settings để trống thì giữ giá trị cũ.
- Đổi username/password web admin qua settings có hiệu lực ngay.
- Audit sạch sau luồng web.

## Kết luận

Bản v1.5.0 đã sửa trọng tâm Web Admin UI/UX, settings và quản lý sản phẩm. Không phát hiện lỗi consistency trong audit sau test.
