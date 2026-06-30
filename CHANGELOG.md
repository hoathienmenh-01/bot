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
