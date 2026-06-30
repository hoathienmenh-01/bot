# TEST REPORT

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
