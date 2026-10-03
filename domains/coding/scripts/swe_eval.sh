#!/usr/bin/env bash
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# H_0 and the incumbent on SWE-bench Verified, each from its own worktree, one
# attempt per instance; then the resolve rates over the full 500-instance
# denominator. Usage (from anywhere):
#   bash domains/coding/scripts/swe_eval.sh [n_concurrent]
set -euo pipefail
DOM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; REPO="$(cd "$DOM/../.." && pwd)"
RUNS="${RRSI_RUNS_DIR:-$REPO/runs/coding}"; N="${1:-1}"
DATASET="${SWE_DATASET:-swe-bench/swe-bench-verified}"
cd "$REPO"
export RRSI_REPO_ROOT="$REPO"
export RRSI_CODING_VENV="${RRSI_CODING_VENV:-$REPO/.venv}"
PYTHON="${RRSI_CODING_PYTHON:-$RRSI_CODING_VENV/bin/python}"
RUNS="$("$PYTHON" -c 'import sys; from domains.coding.runtime import Runtime; print(Runtime().inside(sys.argv[1]))' "$RUNS")"
"$PYTHON" -c 'import json, sys; from pathlib import Path; from domains.coding.adapter import DOMAIN; DOMAIN.validate_frontier(json.loads((Path(sys.argv[1])/"frontier.json").read_text()))' "$RUNS"
BASE="$("$PYTHON" -c 'import json, sys; from pathlib import Path; print(json.loads((Path(sys.argv[1])/"frontier.json").read_text())["trajectory"][0]["commit"])' "$RUNS")"
CHAMP="$("$PYTHON" -c 'import json, sys; from pathlib import Path; print(json.loads((Path(sys.argv[1])/"frontier.json").read_text())["incumbent"]["commit"])' "$RUNS")"
export MODEL="${MODEL:-$("$PYTHON" -c 'from domains.coding.adapter import CFG; print(CFG["policy_model"])')}"
for arm in "base:$BASE" "best:$CHAMP"; do
  name="${arm%%:*}"; commit="${arm#*:}"; wt="$RUNS/worktrees/swe_$name"
  "$PYTHON" -c 'import sys; from domains.coding.runtime import Runtime; Runtime().inside(sys.argv[1])' "$wt"
  git -C "$REPO" worktree remove --force "$wt" 2>/dev/null || true
  git -C "$REPO" worktree add --detach "$wt" "$commit" >/dev/null
  echo "[swe_eval] arm $name = $commit"
  (cd "$wt/domains/coding" && RRSI_CODING_ROOT="$wt/domains/coding" bash "$DOM/scripts/run_eval.sh" "swe_$name" "$DATASET" 1 "$N" --jobs-dir "$RUNS/jobs")
done
"$PYTHON" "$DOM/scripts/swe_summary.py" "$RUNS/jobs/swe_base" "$RUNS/jobs/swe_best"
