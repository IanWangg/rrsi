#!/usr/bin/env bash
# Run the same Coding CLI in Linux when native Python dependencies are unavailable.
set -euo pipefail
MAIN="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$MAIN"
python3 -c 'import sys; from domains.coding.runtime import Runtime; r=Runtime(); [r.inside(p) for p in sys.argv[1:]]' \
  .runtime/linux-venv .runtime/docker/buildx .runtime/tmp .runtime/cache/pip domains/coding/requirements.lock
D="${RRSI_DOCKER_BIN:-}"
if [[ -z "$D" ]]; then
  if command -v docker >/dev/null 2>&1; then D="$(command -v docker)";
  elif [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
    D=/Applications/Docker.app/Contents/Resources/bin/docker
  else echo 'Docker Desktop is required' >&2; exit 1; fi
fi
export PATH="$(dirname "$D"):$PATH"
export BUILDX_CONFIG="$MAIN/.runtime/docker/buildx"
mkdir -p "$BUILDX_CONFIG" "$MAIN/.runtime/tmp" "$MAIN/.runtime/cache/pip"
IMAGE=rrsi-coding-controller:py313
if [[ "${1:-}" == setup ]]; then
  shift
  NATIVE="$($D version --format '{{.Server.Os}}/{{.Server.Arch}}')"
  "$D" build --platform "$NATIVE" -f "$MAIN/domains/coding/Dockerfile.controller" -t "$IMAGE" "$MAIN/domains/coding"
  "$D" run --rm --mount "type=bind,source=$MAIN,target=$MAIN" --workdir "$MAIN" \
    --env "TMPDIR=$MAIN/.runtime/tmp" --env "PIP_CACHE_DIR=$MAIN/.runtime/cache/pip" \
    "$IMAGE" sh -ec 'python -m venv .runtime/linux-venv; if [ -f domains/coding/requirements.lock ]; then .runtime/linux-venv/bin/python -m pip install -c domains/coding/requirements.lock -e ".[coding,dev]"; else .runtime/linux-venv/bin/python -m pip install -e ".[coding,dev]"; .runtime/linux-venv/bin/python -m pip freeze --exclude-editable > domains/coding/requirements.lock; fi'
  exit
fi
if [[ ! -f "$MAIN/.runtime/linux-venv/pyvenv.cfg" ]]; then
  echo 'First run: bash domains/coding/scripts/docker_controller.sh setup' >&2; exit 1
fi
if [[ "${1:-}" == --shell ]]; then
  shift
  COMMAND=(bash "$@")
else
  COMMAND=("$MAIN/.runtime/linux-venv/bin/python" "${@:-rrsi.py}")
fi
exec "$D" run --rm --init --mount "type=bind,source=$MAIN,target=$MAIN" \
  --mount 'type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock' \
  --workdir "$MAIN" --env DEEPSEEK_API_KEY --env DEEPSEEK_BASE_URL \
  --env DEEPSEEK_REASONING_EFFORT --env MODEL \
  --env "RRSI_REPO_ROOT=$MAIN" --env "RRSI_CODING_VENV=$MAIN/.runtime/linux-venv" \
  --env "RRSI_CODING_PYTHON=$MAIN/.runtime/linux-venv/bin/python" \
  --env "TMPDIR=$MAIN/.runtime/tmp" --env "PIP_CACHE_DIR=$MAIN/.runtime/cache/pip" \
  "$IMAGE" "${COMMAND[@]}"
