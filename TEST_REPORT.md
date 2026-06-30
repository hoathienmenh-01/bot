# TEST REPORT

## v2.6.1 Test Report

Command:

```bash
python -m compileall -q src tests
./scripts/run_full_tests.sh
```

Result:

```text
Ran 69 tests in 4.579s

OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

New v2.6.1 coverage:

- Regression test for upgrading an existing SQLite `users` table without `api_key`.
- Verifies the migration no longer uses unsupported `ALTER TABLE ... ADD COLUMN ... UNIQUE`.
- Verifies API keys remain unique through `idx_users_api_key_unique`.

Command:

```bash
python -m compileall -q src tests
./scripts/run_full_tests.sh
```

Result:

```text
Ran 58 tests in 3.643s

OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

New v2.2 coverage:

- Auto-detect pipe account files: UID/password/cookie/token.
- Mask sensitive cookie/token values in preview/log metadata.
- Import `.txt` stock upload into selected product.
- Import `.docx` stock upload by reading Word paragraphs with Python stdlib.
- Web Inventory page exposes file upload, parser mode, and guidance.

## v2.3.0 Test Report

Command:

```bash
python -m compileall -q src tests
./scripts/run_full_tests.sh
```

Result:

```text
Ran 59 tests in 4.200s

OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

New coverage:

- Product-specific stock parser configuration.
- Email|Password|2FA style stock import.
- Email / Password style stock import and normalization.
- UID|Password|Cookie|Token detection stays masked in preview.
- Existing web pages and old stock import flows remain compatible.

## v2.4.0 Test Report

Command:

```bash
python -m compileall -q src tests
./scripts/run_full_tests.sh
```

Result:

```text
Ran 61 tests in 3.857s

OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

New coverage:

- Product image validation accepts JPG/PNG/WebP and rejects invalid formats.
- Product image upload stores files under `media/products/`.
- Backup includes uploaded product images.
- Web Admin product preview displays image/icon/custom emoji metadata.
- Bot product list renders icon, price and stock.
- Bot product detail renders custom emoji tag, short description and long description.
- Product with image and product without image both remain compatible with single-panel navigation.


## v2.5.0 Test Report

Command:

```bash
python -m compileall -q src tests
./scripts/run_full_tests.sh
```

Result:

```text
Ran 65 tests in 4.233s

OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

New coverage:

- Category list renders green/red stock status and category icons.
- Out-of-stock products expose preorder buttons instead of purchase buttons.
- Preorder creation computes deposit by configured percentage.
- Preorder wallet payment debits once and marks preorder active.
- Preorder owner guard prevents another user from viewing/paying.
- Web Admin can edit category icons, write preorder deposit percent to `.env`, list preorders and mark them fulfilled.


## v2.6.0 Test Report

Command:

```bash
python -m compileall -q src tests
./scripts/run_full_tests.sh
```

Result:

```text
Ran 68 tests in 4.715s

OK
Seeded demo categories/products/stock.
AUDIT OK: no consistency issues found
FULL TEST OK
```

New coverage:

- Telegram category grid rows include 3-column layout and refresh callback.
- API link view exposes buyer API docs and regenerate-key callback.
- Buyer API rejects invalid keys.
- Buyer API lists products with stock.
- Buyer API purchases products using wallet balance and returns delivery JSON.
- Duplicate stock import policy supports allow, skip and reject modes.
