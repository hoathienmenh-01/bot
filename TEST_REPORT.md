# TEST REPORT - NIMO Shop v1.4.0 Web Admin

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
Ran 43 tests in 1.564s
OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

## Nhóm test mới trong v1.4

- `test_password_hash_session_and_csrf_are_enforced`
- `test_service_manages_products_stock_settings_wallet_and_payment`
- `test_http_admin_login_csrf_forms_and_pages`

## Phạm vi đã test

- Web Admin login bằng password hash PBKDF2.
- Session signed cookie.
- CSRF token cho form POST.
- Dashboard chạy sau login.
- Tạo danh mục qua web.
- Tạo sản phẩm qua web.
- Nhập kho qua web.
- Cộng ví thủ công qua web service.
- Xác nhận thanh toán thủ công qua web service và giao đơn.
- Cập nhật settings, ghi `.env`.
- Light/Dark + VI/EN toggle ở HTTP layer.
- Audit không báo lỗi sau luồng web.
- Toàn bộ 40 test lõi cũ vẫn pass.


## v1.4.1 additional verification

- Added tests for first-run setup token validation.
- Verified full test suite after setup fallback changes.
