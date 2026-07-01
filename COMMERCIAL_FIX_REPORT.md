# NIMO Telegram Shop Bot v2.8.1 Commercial Hardening Fix Report

## Scope
This build fixes the production-blocking issues found in the commercial review: webhook/auth hardening, payment reconciliation, preorder money safety, zero-price checkout, backup safety, admin notifications, and buyer API idempotency.

## Main fixes

1. Webhook security
   - `/webhook/sepay` and `/webhook/binance` now reject unsigned requests when `WEBHOOK_SHARED_SECRET` is missing or invalid.
   - HMAC verification through `X-NIMO-Signature` and direct secret through `X-NIMO-Webhook-Secret` are supported.

2. Web Admin security
   - Removed unsafe default `admin/admin12345` bootstrap.
   - Removed fixed `change-this-web-session-secret` fallback.
   - Public Web Admin requires a strong `WEB_SESSION_SECRET`; localhost may use a temporary in-memory secret for setup.
   - Login cookie can use `Secure` with `WEB_COOKIE_SECURE=true`.

3. Payment safety
   - Payment/order/preorder public codes now use larger random suffixes.
   - Legacy 8-hex payment codes remain reconcilable.
   - Previously `unmatched` external transactions can be reconciled later with the same `provider_tx_id` after admin supplies the correct payment code.
   - Zero-amount orders no longer create external payment intents.

4. Preorder safety
   - Cancelling a paid active preorder refunds the deposit into the buyer wallet exactly once.
   - Full-deposit preorders auto-deliver when stock arrives.
   - Web Admin fulfill no longer marks a preorder fulfilled unless it creates/delivers the corresponding order.

5. Backup/restore safety
   - Backup download defaults to excluding `.env`.
   - Restore rejects `media/products` path traversal attempts.
   - Clearing product images only deletes files under `media/products`.

6. Buyer API hardening
   - `POST /api/telegram-buyer/purchase` supports `Idempotency-Key` or JSON `idempotency_key`.
   - API order expiry now uses configured `ORDER_EXPIRES_MINUTES`.
   - Banned users cannot create orders/preorders.

7. Notifications and operations
   - Telegram admin `/confirm`, `/cancel`, `/refund`, and `/addstock` queue buyer/preorder notifications consistently with Web Admin.
   - Notification loop marks all-failed/no-recipient sends as failed instead of sent.

## Verification

```bash
python3 -m compileall -q src tests
PYTHONPATH=src python3 -m pytest -q
```

Result:

```text
81 passed
```

Additional demo audit check:

```text
Seeded demo categories/products/stock.
AUDIT_ISSUES 0
```

## Production notes

Before opening the shop publicly, set these in `.env`:

```env
WEB_ADMIN_PASSWORD=<strong password>
WEB_SESSION_SECRET=<random string at least 32 characters>
WEBHOOK_SHARED_SECRET=<random webhook secret>
BOT_TOKEN=<BotFather token>
ADMIN_IDS=<your Telegram numeric id>
```

Do not expose Web Admin directly to the public internet without HTTPS/VPN/reverse proxy access control.
