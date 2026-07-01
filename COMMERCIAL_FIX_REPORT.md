# NIMO Telegram Shop Bot - Commercial Release Fix Report

## Build reviewed
Source package: `nimo_telegram_shop_bot_v2_8_1_commercial_hardened(1).zip`
Fixed package version: `v2.8.2-commercial-ready`

## Critical fixes completed

### 1. Preorder deposit safety
Fixed a commercial money-loss bug where preorders were marked `fulfilled` immediately after stock arrived, even when the buyer still had an unpaid remaining-balance order.

New behavior:
- A paid preorder stays `active` while the linked remaining-payment order is awaiting payment.
- `preorders.status='fulfilled'` is set only after the linked order is delivered.
- Expired/cancelled remaining-payment orders release stock but do not erase the preorder deposit state.
- Cancelling an active preorder also cancels any pending linked remaining-payment order and refunds the deposit exactly once.

Files changed:
- `src/nimo_shop/services/preorders.py`
- `src/nimo_shop/services/orders.py`
- `src/nimo_shop/db.py`

Schema migration:
- Added `orders.preorder_id`.
- Added `idx_orders_preorder_status`.

Regression tests added:
- `test_preorder_remaining_order_expiry_does_not_lose_deposit`
- `test_preorder_fulfilled_only_after_remaining_payment_is_paid`

### 2. Atomic Buyer API purchase/idempotency
Fixed non-atomic Buyer API idempotency. Previously two parallel requests with the same idempotency key could both create/pay/deliver orders before the response was stored.

New behavior:
- Buyer API reserves the idempotency key inside the same `BEGIN IMMEDIATE` transaction before reserving stock or debiting wallet.
- Order creation, wallet debit, delivery, and idempotency response storage are now one transaction.
- Repeated calls with the same idempotency key return the exact same response and do not debit twice.

Files changed:
- `src/nimo_shop/web/app.py`
- `src/nimo_shop/services/orders.py`

Regression test added:
- `test_buyer_api_idempotency_key_returns_same_purchase_once`

### 3. Native Binance Pay webhook verification
Fixed Binance webhook handling so the system supports native Binance Pay webhook headers/signatures instead of only the internal `X-NIMO-*` signature.

New behavior:
- `/webhook/binance` supports native `BinancePay-Timestamp`, `BinancePay-Nonce`, and `BinancePay-Signature` verification with `BINANCE_PAY_SECRET_KEY`.
- Parses native Binance payload fields such as `bizStatus`, `data.merchantTradeNo`, `data.orderAmount`, `data.currency`, and `data.transactionId`.
- Keeps backward-compatible internal signed webhook support for proxy/testing flows.

Files changed:
- `src/nimo_shop/web/app.py`

Regression test added:
- `test_native_binance_webhook_signature_and_payload_are_supported`

### 4. Safer product image handling
Fixed product image upload logic so invalid product IDs cannot leave orphan image files behind. Bot-side product image loading now refuses absolute paths and only serves files under `media/products/`.

Files changed:
- `src/nimo_shop/web/service.py`
- `src/nimo_shop/bot/app.py`

### 5. Safer wallet user reference resolution
Fixed a money-risk issue where a numeric Telegram ID could fall back to an internal DB user ID and credit/debit the wrong user.

New behavior:
- `tg:123456789` or plain numeric input means Telegram ID.
- `id:12` means internal DB ID.
- `@username` or username resolves username.
- Plain numeric input no longer falls back to internal DB ID.

File changed:
- `src/nimo_shop/web/service.py`

### 6. Admin login brute-force guard
Added basic brute-force protection for Web Admin login.

New behavior:
- Failed login attempts are tracked per IP + username.
- 5 failed attempts lock login for 15 minutes.
- Successful login clears failed attempts.

Files changed:
- `src/nimo_shop/web/service.py`
- `src/nimo_shop/web/app.py`

### 7. Production cookie guard
Added production guard requiring `WEB_COOKIE_SECURE=true` when `APP_ENV=production`.

Files changed:
- `src/nimo_shop/web/app.py`
- `.env.example`

### 8. Bot `/addstock` duplicate policy consistency
Telegram admin `/addstock` now reads `STOCK_DUPLICATE_POLICY` from `app_settings` when available, instead of only using environment defaults.

File changed:
- `src/nimo_shop/bot/app.py`

## Test result

```bash
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m pytest -q
```

Result:

```text
85 passed in 9.34s
```

## Release note
This version is safer for a commercial launch than the submitted build. The main remaining operational requirements are external: deploy behind HTTPS, set strong secrets, configure real payment provider credentials, test one small real payment end-to-end, and keep backups enabled.
