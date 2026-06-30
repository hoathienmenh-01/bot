# Test Report - NIMO Telegram Shop Bot 1.3.0

## Commands executed

```bash
./scripts/run_full_tests.sh
```

Equivalent manual commands:

```bash
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src tests
DATABASE_PATH=$(mktemp -d)/shop.db PYTHONPATH=src python3 -m nimo_shop.seed_demo
DATABASE_PATH=$(mktemp -d)/shop.db PYTHONPATH=src python3 -m nimo_shop.audit
```

## Result

- Unit tests: 40/40 passed
- ResourceWarning check: passed
- Compile check: passed
- Demo seed smoke check: passed
- Audit smoke check: passed

## Additional fixes in 1.3.0

- User ownership guard: a customer cannot pay, cancel, or create a payment intent for another customer's order.
- Sold stock cleanup: delivery clears stale reservation fields for cleaner stock audit.
- AuditService added: checks wallet-vs-ledger drift, reserved-stock mismatch, delivered-order delivery count, sold-stock delivery links, stale available stock references, and cash ledger/event linkage.
- Admin `/audit`, `/orders`, `/finance`, `/stock`, `/users` commands added.
- Binance Pay create-order flow is called from the Telegram payment button when merchant credentials are configured.
- `scripts/run_full_tests.sh` added for one-command regression testing.

## Coverage focus

- Wallet credit/debit validation and idempotency
- Negative/zero amount rejection
- Order reserve/delivery/cancel/expiry
- Stock oversell and duplicate stock prevention
- Provider payment idempotency by provider_tx_id
- Underpayment, overpayment, late payment, cancelled/expired order payment
- Duplicate payment to already-confirmed intent
- Refund-to-wallet idempotency
- External event audit for unmatched payment codes
- User ownership enforcement for order actions
- Data consistency audit checks
- Binance Pay v3 payload/signature/create-order helper
- SePay/VietQR helper
- Settings parsing
- Admin command parsers
- Customer/admin text rendering
- SePay transaction normalization and idempotent application
- Demo seed script smoke test

## Not live-tested here

- Real Telegram BotFather token polling was not run in this sandbox because no live token was provided.
- Real SePay/Binance network calls were not executed with live credentials.
- Binance webhook receiver still requires a public HTTPS endpoint in production.
