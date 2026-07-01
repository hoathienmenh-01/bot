# TEST REPORT - v2.8.0 Commercial checkout hardening

Commands run:

```bash
python -m compileall -q src tests
./scripts/run_full_tests.sh
```

Result:

```text
Ran 75 tests in 11.970s

OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

New regression coverage:

- Zero-price orders no longer call wallet debit with amount 0.
- Stock import queues a customer broadcast when admin uploads new stock.
- Paid active preorders are matched when stock arrives and receive a remaining-payment order notification.
- Existing payment, wallet, stock, delivery, web admin, webhook, role and buyer API tests still pass.

Manual review notes:

- Telegram transient network errors such as `Connection reset by peer` are upstream/network conditions; aiogram retries polling.
- Live SePay/Binance/BEP20 payment confirmation still requires real provider/API testing before production launch.


## v2.8.1 commercial hardening verification

- `python3 -m compileall -q src tests`: PASS
- `PYTHONPATH=src python3 -m pytest -q`: 81 passed
- Added regression tests for webhook unsigned rejection, payment reconciliation, preorder refund/full-deposit delivery, backup path traversal, and banned-user order blocking.
