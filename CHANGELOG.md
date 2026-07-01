## v2.8.11-pay2s-bank-provider

- Added native Pay2S bank provider support to the multi-bank receiving account module.
- `pay2s` provider can poll Pay2S transaction history using the Pay2S `pay2s-token` header and `POST /userapi/transactions`.
- Added `/webhook/pay2s` endpoint that verifies Pay2S `Authorization: Bearer <webhook_token>` from the bank account API secret field and reconciles Pay2S `transactions[]` payloads.
- Extended bank transaction normalization for Pay2S fields: `transaction_id`, `account_number`, `bank`, `amount`, `description`, `type`, `transactionNumber`, `content`, `transferType`, `transferAmount`, and `checksum`.
- Updated bank provider UI labels so Pay2S is a first-class automatic provider, not a generic/custom placeholder.
- Added migration to allow `provider='pay2s'` in existing SQLite `bank_accounts` tables.
- Added regression tests for Pay2S polling normalization, Pay2S account storage, and Pay2S webhook confirmation.


## v2.8.4 - Wallet Top-up Flow Fix

- Removed fixed top-up amount buttons from the Telegram wallet screen.
- Wallet now shows only current balances: VND and USDT with network label.
- Added interactive custom amount flow for bank, Binance ID, and USDT BEP20 top-ups.
- Bank top-ups/orders now show the unique NAP/ORD transfer code and send VietQR image to the customer.
- Binance ID and USDT BEP20 top-ups now create separate payment intents with unique payment codes for admin/webhook reconciliation.
- Added `USDT_NETWORK` setting so the displayed wallet/network label can be changed between BEP20/TRC20 when needed.

# CHANGELOG

## v2.8.1 - Commercial hardening production fix

- Webhooks now require `WEBHOOK_SHARED_SECRET`/HMAC; unsigned SePay/Binance webhook requests are rejected instead of accepted silently.
- Web Admin no longer falls back to `admin/admin12345` or a fixed session secret. Public Web Admin requires a strong `WEB_SESSION_SECRET`.
- Payment/order/preorder public codes now use larger random suffixes while keeping legacy 8-hex payment codes reconcilable.
- Reconciliation can now apply an earlier unmatched real transaction with the same `provider_tx_id` after admin supplies the correct payment code.
- Paid preorder cancellation refunds the deposit to the buyer wallet exactly once.
- Full-deposit preorders are delivered automatically when stock arrives; Web Admin fulfill no longer marks a preorder fulfilled without creating/delivering an order.
- Backup restore blocks `media/products` path traversal and backup download defaults to excluding `.env`.
- Buyer API now supports idempotency keys and uses configured order expiry.
- Bot admin commands now queue customer notifications for confirm/cancel/refund and stock-import preorder handling.
- Added commercial regression suite. Full test suite: 81 tests passing, compileall OK.

## v2.8.0 - Commercial checkout, preorder fulfillment and admin notifications

- Split checkout into Wallet, Bank QR, Binance and USDT BEP20 actions.
- Added Binance ID fallback instructions and USDT BEP20 payment instructions with QR image URL support.
- Added `/napusdt` and wallet top-up choices for Binance/USDT.
- Changed default order expiry to 15 minutes and added a background expired-order notifier that cancels stale orders and informs buyers.
- Fixed zero-price/test products: wallet payment no longer crashes with `debit amount must be > 0`; free orders deliver safely.
- Web Admin stock import now queues a broadcast when new stock is added so customers know to return and buy.
- Active preorders are now matched FIFO when stock arrives; the system creates a remaining-payment order and notifies the buyer.
- Admin order cancel/refund/manual payment confirmation now queues targeted buyer notifications.
- Delivery messages include a copy-friendly `<pre>` block for small orders while keeping TXT file delivery for large orders.
- Web Admin sidebar is grouped into operational sections: Overview, Sales, Products & Stock, Payments & Wallets, Customers/API and System.
- Added settings for `BINANCE_PAY_ID`, `BINANCE_PAY_NOTE`, `USDT_BEP20_ADDRESS`, `USDT_BEP20_TOLERANCE`.
- Added regression tests for zero-price delivery, stock broadcast and preorder auto-order creation.
- Full test suite: 75 tests passing, compileall OK, seed demo OK, audit OK.

## v2.7.0 - Unified payments and commercial hardening

- Confirmed the shop can run bank/VietQR/SePay, Binance Pay/USDT and wallet payments inside one Telegram bot; no separate payment bot is required.
- Fixed webhook provider mapping: `/webhook/sepay` now reconciles `bank` payment intents and `/webhook/binance` reconciles `binance_pay` intents. Earlier versions could persist valid webhook payments as unmatched because the public URL provider name did not match the internal intent provider.
- Hardened order payment intent creation: repeatedly pressing the same payment method for one pending order now reuses the existing live payment code instead of generating multiple confusing ORD codes.
- Enforced Web Admin role permissions. Roles were previously stored but not enforced; non-owner accounts can no longer access owner-only pages or post owner-only actions.
- Added regression tests for webhook mapping, pending payment-code reuse, and role permission enforcement, and optional shared-secret/HMAC webhook protection.
- Full test suite: 73 tests passing, compileall OK, seed demo OK, audit OK.

# Changelog

## v2.6.0 - Telegram Menu / Buyer API / Catalog Grid / Duplicate Stock Policy

- Bot startup now publishes Telegram command menu so the left-bottom **Menu** button shows `/start`, `/menu`, `/products`, `/wallet`, `/search`, and `/taidon`.
- `/start` opens the shop catalog immediately with welcome text, wallet balance, grid category buttons, stock state, and `🔄 Làm mới`.
- Category buttons are arranged in a 3-column grid; category and product buttons use `🟢/🔴` state markers.
- Added buyer API key flow inside Telegram bot (`🔗 API` + regenerate key).
- Added public buyer API endpoints:
  - `GET /api/telegram-buyer/products`
  - `POST /api/telegram-buyer/purchase`
  - `/t/api-guide` documentation page.
- Added `STOCK_DUPLICATE_POLICY=allow|skip|reject` and made `allow` the default for shops that intentionally import duplicate-looking rows.
- Added migration to remove the old SQLite unique stock constraint while preserving existing stock IDs and delivery history.
- Added regression tests for API key/docs, API product listing, API purchase with wallet, category grid, refresh callback rows and duplicate policy.

## v1.7.0 — Quantity / Wallet Visibility / Languages / One-command Run

- Added quantity-aware Telegram purchase flow:
  - quick buttons for 1/2/3/5 items based on available stock;
  - custom quantity prompt;
  - `/mua PRODUCT_ID SO_LUONG` and `/buy PRODUCT_ID QUANTITY` command.
- Order creation now shows unit price, total price, current wallet balance, and missing wallet amount.
- Main `/start` menu now shows the user's wallet balance immediately.
- Order payment keyboard now includes a direct `Nạp ví` action.
- Added popular user languages: Vietnamese, English, Chinese, Japanese, Korean, Thai, Spanish, French.
- Main reply menu changes by the selected language; handlers accept all translated menu labels.
- Added one-command launcher:
  - `PYTHONPATH=src python -m nimo_shop.run_all --host 0.0.0.0 --port 8080`
  - `./scripts/run_all.sh`
- Added regression tests for quantity UI, wallet visibility, supported languages, and menu labels.

## v1.6.0 — Payment / Stock / Notify Fixes

- Free amount wallet top-up via `/nap` and custom top-up button.
- Strict duplicate stock import validation.
- Product update notification queue for bot broadcast.
- Manual wallet adjustment now accepts Telegram ID / username / internal ID.
- Fixed duplicate payment confirmation path in Web Admin.

## v1.8.0 - Search / Backup / Multi-bot Admin
- Added customer product search in Telegram bot (`🔎 Tìm sản phẩm`, `/search <keyword>`).
- Added Web Admin pages: Bot Manager, Bot Notifications, Backup/Restore, and step-by-step Guide.
- Added managed bot records for multiple Telegram bots, including primary bot selection.
- Added admin broadcast/notification queue managed from Web Admin.
- Added backup download using SQLite backup API to avoid copying a hot WAL database.
- Added tests for product search, backup download, managed bot CRUD, and notification queue.

## v1.9.0 - Single Panel Navigation / Large Delivery Files
- Telegram inline callback flow now edits the current control message instead of sending a new bot message for category, product, quantity, payment, wallet, history, language, and support panels.
- `/start` now opens an inline control panel so users can navigate the shop from one message instead of scrolling through many bot messages.
- Custom quantity prompt keeps the same control panel; after the user sends the number, the bot edits the original panel into the order/payment screen.
- Large delivered orders are exported as downloadable TXT files instead of dumping hundreds/thousands of accounts into chat.
- Added `/taidon ORD...`, `/download_order ORD...`, and `/export_order ORD...` so customers can download an already-delivered order again.
- Added regression tests for large delivery export and single-message navigation callback rows.

## v2.0.0 - Delivery File Policy / Web Admin Config
- Added Web Admin delivery settings:
  - `auto`: small orders are shown in chat, large orders are sent as TXT files.
  - `file_only`: every delivered order is sent as a TXT file, including quantity 1.
  - `inline_and_file`: small orders are shown in chat and also sent as TXT files.
- Added configurable file threshold via `DELIVERY_FILE_THRESHOLD`.
- Added `.env.example` entries for delivery policy.
- Added regression tests for one-item file delivery, inline+file delivery, and configurable threshold.

## v2.1.0 - Operations Center / Reports / Reconciliation

- Added System Status page: database, bot token format, bank/SePay, Binance and low-stock overview.
- Added Bot Token format checker from Web Admin.
- Added CSV import page for categories/products/stock.
- Added CSV export reports for orders, products, stock, wallets, finance and users.
- Added provider webhook endpoints `/webhook/sepay` and `/webhook/binance` with unmatched-event persistence for reconciliation.
- Added Admin Roles page: owner, finance, stock, support, viewer.
- Added Reconciliation page for unmatched/reviewed external payment events.
- Added Delivery Logs page and bot-side delivery file logging.
- Added Coupon management page.
- Added Low-stock warning page and queued low-stock notifications.
- Full test suite: 55 tests passing, compileall OK, seed demo OK, audit OK.

## v2.2.0 - Smart stock file upload
- Added product-level stock upload in Web Admin Inventory page.
- Added `.txt`, `.csv`, and `.docx` stock file parsing without heavy dependencies.
- Added auto-detection for `UID|password|cookie|token`, `email|password`, CSV/Excel, and raw one-line-per-item stock.
- Added masked preview/parser logic so admin can understand detected columns without exposing full cookies/tokens in web/log previews.
- Duplicate stock is still fail-fast: duplicate pasted/uploaded rows are rejected instead of silently skipped.
- Added tests for pipe account import, DOCX paragraph import, and HTTP stock upload UI.

## v2.3.0 - Product stock formats and manual data handling

- Added product-specific stock format configuration:
  - Auto detect
  - Raw one item per line
  - Email | Password
  - Email / Password
  - Email | Password | 2FA/Recovery
  - UID | Password | Cookie | Token
  - Pipe-separated custom columns
  - CSV/Excel columns
- Added stock labels and examples per product so admins know exactly what to paste/upload.
- Stock import now defaults to “Theo cấu hình sản phẩm” instead of forcing one global parser.
- Email / password inputs are normalized into Email|Password internally for consistent delivery.
- Delivery files can render labeled fields per product, e.g. Email / Password / 2FA.
- Added SQLite migrations for existing databases to add product stock-format columns safely.
- Added tests for product-specific stock formats and slash-delimited account imports.


## v2.5.0 - Category stock folders and preorders

- Added category icons and stock-aware category buttons. Categories now show green/available or red/out-of-stock state with total available stock count.
- Product list buttons now show green/red stock status and keep the compact shop-style layout.
- Out-of-stock product detail pages now show preorder buttons instead of normal buy buttons.
- Added preorder database table and PreorderService with owner guard, deposit calculation and wallet deposit payment.
- Added configurable `PREORDER_DEPOSIT_PERCENT` in Web Admin Settings and `.env.example`.
- Added Web Admin Preorders page to review, cancel and mark preorder requests as fulfilled.
- Added category icon editing in Web Admin category management.
- Added bot views/keyboards for preorder create, custom quantity, wallet deposit payment and cancel.
- Added regression tests for category stock states, preorder UI, preorder wallet payment, owner guard and Web Admin preorder management.
- Full test suite: 65 tests passing, compileall OK, seed demo OK, audit OK.

## v2.4.0 - Product media cards / premium catalog UI

- Added product image upload in Web Admin product create/edit forms.
- Added product icon and custom Telegram emoji ID fields per product.
- Added product short description and long description fields for richer customer-facing cards.
- Product list buttons now show icon, name, price and stock count in a compact shop-style format.
- Product detail can edit the same single-panel message into a photo card with caption and buy buttons.
- Bot stores Telegram `file_id` after a product image is successfully sent so future sends can reuse Telegram cache.
- Added Web Admin product preview page.
- Added strict image validation: JPG/PNG/WebP only, maximum 5MB, magic-byte checked.
- Backup ZIP now includes `media/products/` and restore extracts product images.
- Added tests for product media upload, backup media inclusion, custom emoji rendering and image/no-image product views.
- Full test suite: 61 tests passing, compileall OK, seed demo OK, audit OK.

## v2.8.2-commercial-ready

- Fixed preorder deposit state machine so preorders are not marked fulfilled until actual delivery.
- Added `orders.preorder_id` migration and linked preorder fulfillment to delivered orders.
- Made Buyer API wallet purchase and idempotency atomic in one transaction.
- Added native Binance Pay webhook signature verification and payload parsing.
- Hardened product image path handling and prevented orphan files for invalid product IDs.
- Made wallet user reference resolution explicit/safe to avoid crediting the wrong user.
- Added Web Admin login brute-force lockout.
- Added `APP_ENV=production` guard requiring `WEB_COOKIE_SECURE=true`.
- Synced Telegram `/addstock` duplicate policy with web `app_settings`.
- Added commercial regression tests; full suite now passes: `85 passed`.


## v2.8.6 payment automation UI polish
- Làm rõ cấu hình SePay API tự động quét giao dịch ngân hàng.
- Binance wallet topup dùng Binance Pay merchant order khi API/webhook đã cấu hình; nếu thiếu sẽ fallback Binance ID thủ công.
- Sửa QR VietQR không còn bị hiển thị lặp URL + ảnh QR.
- Sidebar admin cố định và nhớ vị trí scroll sau khi bấm chức năng.
- Ô cấu hình dạng click rõ hơn, dễ nhận biết hơn.

## v2.8.7 - Bank Auto Topup + Admin UX Fixes

- Fixed bank top-up instruction so VietQR is sent once as a Telegram photo, not duplicated as both text URL and photo.
- Made SePay auto-poller read live Web Admin settings from `app_settings` instead of only `.env`, so bank auto top-up works after configuring in Admin and restarting.
- Kept SePay watcher running even when env values are blank; it waits until Bank/SePay are enabled in Admin settings.
- Reduced admin notification queue latency from 15 seconds to 2 seconds.
- Made Admin sidebar groups collapsible with persisted open/closed state and preserved sidebar scroll position.
- Expanded webhook bank payload parsing for common SePay/bank field names such as `amount_in`, `transaction_content`, `transfer_content`, `transactionId`, and `bank_transaction_id`.

## v2.8.9 - Multi Bank Accounts + Binance runtime config

- Added a multi-bank receiving account module in Web Admin at **Thanh toán & ví → Tài khoản ngân hàng**.
- Each bank account can store label, bank name, VietQR BIN, account number, owner name, provider type, API key/secret, base URL, poll interval, enabled/default state and notes.
- Bot now lets customers choose a bank account when multiple enabled receiving accounts exist, then creates the correct `NAP...`/`ORD...` code and VietQR for that account.
- SePay poller now scans every enabled bank account with provider `sepay` and its own API key; old single `SEPAY_API_KEY` remains as backward-compatible fallback.
- Provider transaction ids are prefixed by bank-account id during polling to avoid collisions across multiple linked accounts.
- Binance Pay creation now reads live Web Admin `app_settings` at runtime, not only `.env`, before falling back to Binance ID manual instructions.
- Added regression tests for multi-bank account storage/default behavior and multi-bank SePay transaction id collision protection.
- Full test suite: `89 passed`.

## v2.8.10-admin-theme-bank-api-panel
- Fixed Admin UI contrast: dark mode now uses true black backgrounds instead of blue/gray gradients; light mode uses higher-contrast text, inputs, cards and tables.
- Added bank/API account configuration directly inside Web Admin → Cấu hình, not only the separate Bank Accounts page.
- Added quick bank selector presets that auto-fill bank name and VietQR BIN for common Vietnamese banks.
- Clarified provider API fields per bank account: provider, API key/access token, API secret/client secret, base URL/endpoint, poll interval, default/enabled flags.
- Added regression test for high-contrast theme CSS and visible multi-bank API fields in the Settings page.

## v2.8.12 - Pay2S polling and admin config persistence fix

- Fixed Pay2S auto-polling for multi-bank accounts so enabled Pay2S/SePay bank accounts poll even when the legacy `BANK_ENABLED` single-bank switch is off.
- Made Pay2S response parsing tolerant of `transactions`, nested `data.transactions`, `data.items`, `records`, `results`, and direct list payloads.
- Added Telegram confirmation messages after background bank/Pay2S payments are applied to a wallet top-up or order payment.
- Kept saved bank API keys/secrets visible as masked status in Admin instead of appearing lost after saving.
- Returning from embedded bank-account forms in Settings now goes back to Settings, so the saved configuration stays visible in the same place.
- Added regression tests for nested Pay2S payload parsing and Pay2S-to-bank-intent reconciliation.
- Full test suite: `95 passed`.

## v2.8.13 - Pay2S poller isolation and diagnostics

- Fixed bank-provider polling so a bad legacy `SEPAY_API_KEY` can no longer block Pay2S multi-bank polling after the shop has created bank-account rows.
- Legacy single-bank SePay fallback is now used only for old installs with no `bank_accounts` rows at all.
- Isolated provider polling per bank account: one failed SePay/Pay2S account no longer stops other enabled accounts from being scanned.
- Replaced misleading `[sepay] polling error` with provider-specific logs such as `[bank-sync:pay2s] account=...` or `[bank-sync:sepay] account=...`.
- Added regression tests for Pay2S multi-bank configuration overriding stale legacy SePay config and for incomplete multi-bank rows not falling back to stale SePay keys.
- Full test suite: `97 passed`.

## v2.8.14 - Bank Account Admin Edit/Delete UX

- Made multi-bank account management explicit in Web Admin so admins do not need SQL/code changes to manage receiving accounts.
- Added a visible **Quản lý nhanh** table with always-visible **Sửa**, **Xóa**, and **Đặt mặc định** actions for each bank account.
- Added a dedicated `/bank-accounts/edit?id=...` edit page for changing bank/provider/API/token/account details.
- Fixed embedded Settings bank-account actions to preserve the correct return page after delete/default actions.
- Added clear UI copy that adding, editing, deleting, changing provider and replacing Pay2S/SePay tokens are all handled in Web Admin.
- Added HTTP regression coverage for create → edit → update → delete bank account flow from Web Admin.
- Full test suite: `97 passed`.

## v2.8.15-pay2s-diagnostics
- Normalize Pay2S base URL when admins paste the full `/transactions` endpoint.
- Surface Pay2S HTTP error response bodies in runtime logs so HTTP 400 can be diagnosed without guessing.
- Add regression test for Pay2S copied endpoint/token/account normalization.
## Payment hardening patch

- Canonicalized bank provider handling: SePay/Pay2S/Casso/manual now reconcile through internal provider `bank`.
- Fixed Pay2S webhook/poller duplicate-credit risk by using source-level transaction ids.
- Bound selected bank account ids to payment intents and reject mismatched receiving accounts.
- Blocked Binance/USDT order payment for VND-priced orders until an explicit FX quote module exists.
- Persisted missing-code bank transactions for admin reconciliation.
- Added regression tests for Pay2S double-credit and bank-account mismatch.


## v2.8.16 - Payment webhook buyer notification cleanup

- Fixed Pay2S/SePay/Binance webhook-settled payments being applied silently without notifying the buyer in Telegram.
- Added a queued `payment_success` notification path so the web webhook thread can hand buyer messages to the running bot process.
- Bank top-up and order-bank payment prompts now persist their Telegram instruction/QR message ids on the payment intent.
- After a provider confirms payment, the bot deletes stale QR/instruction messages and sends a final success message with the credited amount and wallet balance.
- Added `bot_notifications.metadata_json` migration to carry payment cleanup actions safely.
- Added regression test for webhook payment success notice + old QR cleanup metadata.
- Full test suite: `101 passed`.

## v2.8.17 - Provider payment auto-delivery fix

- Fixed QR/bank-provider order payments only sending a success receipt after webhook settlement without sending the purchased goods.
- Webhook and poller payment-success notifications now carry `delivery_order_id`; the bot runtime fetches delivered stock rows and sends the real order payload/file automatically.
- The success receipt now includes the exact fallback command `/taidon ORD...` instead of a generic `/taidon` that opens the help prompt.
- Binance Pay webhook settlements use the same delivery notification path as Pay2S/SePay, so paid Binance orders are also auto-delivered.
- Removed duplicate queueing risk in native Binance webhook handling.
- Added regression tests for order-webhook delivery metadata and bot-side delivery payload sending.
- Full test suite: `103 passed`.
