# Changelog

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
