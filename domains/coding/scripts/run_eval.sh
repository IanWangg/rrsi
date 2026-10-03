#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Run the forked AgentHarness on a harbor dataset.
# Usage: run_eval.sh <job-name> [dataset] [n_attempts] [n_concurrent] [extra harbor flags...]
#   dataset default: terminal-bench/terminal-bench-2-1
#   extra flags: --jobs-dir <dir> (default <this checkout>/runs/jobs), -i <task> ...
# Model/infra invariants are pinned here (frozen; not evolvable).
set -euo pipefail
REPO="${RRSI_CODING_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

JOB_NAME="${1:?job name required}"
DATASET="${2:-terminal-bench/terminal-bench-2-1}"
N_ATTEMPTS="${3:-1}"
N_CONCURRENT="${4:-1}"
shift $(( $# > 4 ? 4 : $# ))

MAIN="${RRSI_REPO_ROOT:-$(cd "$REPO/../.." && pwd)}"
JOBS_DIR="$MAIN/runs/coding/jobs"
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs-dir) JOBS_DIR="$2"; shift 2;;
    *) EXTRA+=("$1"); shift;;
  esac
done

export RRSI_REPO_ROOT="$MAIN"
export RRSI_CODING_ROOT="$REPO"
VENV="${RRSI_CODING_VENV:-$MAIN/.venv}"
PYTHON="${RRSI_CODING_PYTHON:-$VENV/bin/python}"
exec "$PYTHON" "$MAIN/domains/coding/harbor_entry.py" \
  "$JOB_NAME" "$DATASET" "$N_ATTEMPTS" "$N_CONCURRENT" \
  --jobs-dir "$JOBS_DIR" "${EXTRA[@]}"
