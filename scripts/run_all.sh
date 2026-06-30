#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
python -m nimo_shop.run_all --host "${WEB_HOST:-0.0.0.0}" --port "${WEB_PORT:-8080}"
