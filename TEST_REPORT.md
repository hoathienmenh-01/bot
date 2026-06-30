# TEST REPORT - v2.1.0

Commands executed:

```bash
python -m compileall -q src tests
./scripts/run_full_tests.sh
```

Result:

```text
Ran 55 tests in 3.293s
OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

Covered areas:

- Wallet/ledger idempotency and invalid amount protection.
- Order creation, stock reservation, expiry, cancellation, refund.
- Bank/SePay payment matching, overpay/underpay/late payment handling.
- Delivery file policy for small/large orders.
- Web Admin login/session/CSRF.
- Product/category/stock/settings/wallet/payment management.
- Multi-bot manager, notifications, backup/restore, admin guide.
- v2.1 additions: status page, token checker, CSV import/export, webhook ingestion, reconciliation, coupons, roles, delivery logs, low-stock alerts.
