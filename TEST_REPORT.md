# TEST REPORT - v1.8.0

Commands run:

```bash
./scripts/run_full_tests.sh
```

Result:

```text
Ran 49 tests in 1.959s
OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

Covered areas:
- Wallet/ledger/order/stock/payment idempotency.
- SePay/Binance helper logic.
- Web Admin login/session/CSRF.
- Product create/edit/delete/import-stock.
- Product search for customers.
- Bot notification queue.
- Managed bot CRUD and primary bot selection.
- Backup ZIP download and SQLite database backup.
- First-run setup path.
- Run-all entrypoint.
