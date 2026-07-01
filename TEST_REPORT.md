# Test Report

Date: 2026-07-01
Package: NIMO Telegram Shop Bot commercial hardening fix

## Commands

```bash
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m pytest -q
```

## Result

```text
85 passed in 9.34s
```

## Added/updated commercial regression coverage

- Preorder remaining-payment expiry does not lose buyer deposit.
- Preorder is fulfilled only after the linked remaining-payment order is delivered.
- Buyer API idempotency key returns one purchase and does not double debit/deliver.
- Native Binance Pay webhook signature and payload are accepted.
- Previous webhook, admin, stock, payment, wallet, backup, product image, and bot rendering tests still pass.
