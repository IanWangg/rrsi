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
# Clean up a specific interrupted job: --job JOB [--jobs-dir REPO_LOCAL_DIR].
# Only resources belonging to that job's recorded Compose projects are removed.
set -euo pipefail
MAIN="${RRSI_REPO_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
VENV="${RRSI_CODING_VENV:-$MAIN/.venv}"
cd "$MAIN"
exec "${RRSI_CODING_PYTHON:-$VENV/bin/python}" -m domains.coding.docker_resources "$@"
