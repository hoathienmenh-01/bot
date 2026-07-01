# Test report v2.8.15

Command:

```bash
PYTHONPATH=src python -m compileall -q src tests
PYTHONPATH=src pytest -q
```

Result:

```text
98 passed in 10.79s
```

## Payment patch verification

Command run:

```bash
PYTHONPATH=src python3 -m pytest -q
```

Result: `100 passed`.

Covered regressions:

- Pay2S webhook + poller same transaction is idempotent.
- Selected bank account mismatch is not auto-credited.
- Binance/USDT order payment is blocked for VND orders without FX quote.
- Bank provider aliases reconcile to canonical `bank`.


## Payment webhook notification patch verification

Command run:

```bash
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m pytest -q
```

Result: `101 passed in 10.99s`.

Covered regressions:

- Webhook-applied Pay2S payment now queues a buyer-facing `payment_success` Telegram notification.
- The queued payment notification carries old instruction/QR message ids for cleanup.
- Existing Pay2S idempotency, bank-account mismatch, Binance currency guard and admin/web tests remain passing.
