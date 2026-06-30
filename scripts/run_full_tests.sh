#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m compileall -q src tests
TMPDIR="$(mktemp -d)"
DATABASE_PATH="$TMPDIR/shop.db" python -m nimo_shop.seed_demo
DATABASE_PATH="$TMPDIR/shop.db" python -m nimo_shop.audit
rm -rf "$TMPDIR"
echo "FULL TEST OK"
