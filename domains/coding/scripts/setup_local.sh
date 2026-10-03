#!/usr/bin/env bash
# Native installation for systems that permit installing Python packages.
set -euo pipefail
MAIN="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$MAIN"
SETUP_PYTHON="${RRSI_SETUP_PYTHON:-python3}"
"$SETUP_PYTHON" -c 'import sys; from domains.coding.runtime import Runtime; assert sys.version_info >= (3,12), "Coding requires Python >=3.12"; r=Runtime(); [r.inside(p) for p in sys.argv[1:]]' \
  .venv .runtime/tmp .runtime/cache/pip domains/coding/requirements.lock
mkdir -p .runtime/tmp .runtime/cache/pip
export TMPDIR="$MAIN/.runtime/tmp" PIP_CACHE_DIR="$MAIN/.runtime/cache/pip"
"$SETUP_PYTHON" -m venv .venv
LOCK=domains/coding/requirements.lock
if [[ -f "$LOCK" ]]; then
  .venv/bin/python -m pip install -c "$LOCK" -e '.[coding,dev]'
else
  .venv/bin/python -m pip install -e '.[coding,dev]'
  .venv/bin/python -m pip freeze --exclude-editable > "$LOCK"
fi
