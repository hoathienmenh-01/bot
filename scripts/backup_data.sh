#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)
DB_PATH=${DATABASE_PATH:-data/shop.db}
OUT="backups/nimo-backup-$STAMP.zip"
if [ ! -f "$DB_PATH" ]; then
  echo "Không tìm thấy database: $DB_PATH" >&2
  exit 1
fi
TMP="backups/shop-$STAMP.db"
python - <<PY
import sqlite3
src=sqlite3.connect('$DB_PATH')
dst=sqlite3.connect('$TMP')
src.backup(dst)
dst.close(); src.close()
PY
if [ -f .env ]; then
  zip -q "$OUT" "$TMP" .env
else
  zip -q "$OUT" "$TMP"
fi
python - <<PY
import zipfile
p='$OUT'
with zipfile.ZipFile(p,'a',zipfile.ZIP_DEFLATED) as z:
    z.write('$TMP','data/shop.db')
PY
rm -f "$TMP"
echo "OK backup: $OUT"
