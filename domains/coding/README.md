# Coding instance: Terminal-Bench 2.1

* Evolve set: all 89 Terminal-Bench 2.1 tasks (`data/tb21_tasks.txt`), `k = 2` trials per task.
* Starting harness H_0: the Terminus-2 agent from harbor, `third_party/harbor_terminus2/` (`harbor_terminus2:AgentHarness`).
* Score: fraction of trials whose hidden unit tests pass; a missing or crashed trial counts as a failure.
* Out-of-distribution test: SWE-bench Verified (500 instances, resolve rate).

## DeepSeek and local setup

The policy and all four search roles default to `deepseek-flash`, low reasoning.
The digester shares the analyst model. Set `DEEPSEEK_API_KEY` in the launching
process's environment; keys are not read from repo files or saved in run configs.
Optional `DEEPSEEK_BASE_URL` defaults to `https://api.deepseek.com`, and
`DEEPSEEK_REASONING_EFFORT` defaults to `low` (`none`, `high`, `max` also supported).
Smoke always forces `low`. Explicit search-role models live in `rrsi.json`; the
policy uses its `policy_model` or an explicit `MODEL` environment override.
Search and policy requests retry transport errors, HTTP 429 and server errors with
at most three attempts in total; permanent request/authentication/balance errors fail
immediately. The Harbor and harness retry layers share that budget.

Install/start ordinary Docker Desktop on macOS, or a Docker engine on Linux.
Docker Desktop's installation, Linux VM and image/container storage use the normal
system locations. All project environments, datasets, caches, temporary files,
state, worktrees, recordings and results stay under this repo. Writable path
overrides must resolve inside it, including after following directory symlinks.

For native Python >=3.12:

```bash
bash domains/coding/scripts/setup_local.sh
.venv/bin/python rrsi.py --domain coding preflight
.venv/bin/python domains/coding/scripts/api_smoke.py
.venv/bin/python rrsi.py --domain coding smoke --working-tree
```

If Mac package installation is restricted, use the same CLI in a Linux management
container. Its Python environment is created in `.runtime/linux-venv` on the
repo's bind mount. The Docker socket gives this controller access to the local
engine; API credentials stay in the controller and are removed from commands
that start benchmark task containers. Do not run untrusted controller code.

```bash
bash domains/coding/scripts/docker_controller.sh setup
bash domains/coding/scripts/docker_controller.sh rrsi.py --domain coding preflight
bash domains/coding/scripts/docker_controller.sh domains/coding/scripts/api_smoke.py
bash domains/coding/scripts/docker_controller.sh rrsi.py --domain coding smoke --working-tree
bash domains/coding/scripts/docker_controller.sh -m pytest -q --basetemp .runtime/tmp/pytest
```

Both setups consume `requirements.lock` (Harbor 0.23.0, LiteLLM 1.103.2, OpenAI
2.54.0). `.runtime/` holds pip/tokenizer/Harbor/build caches and temporary files;
it is ignored by Git. The runner redirects Harbor's hard-coded home caches before
importing its CLI. `RRSI_CODING_VENV` and `RRSI_CODING_PYTHON` may select another
repo-local environment. Docker needs no host `sudo`; the shim resolves the real
CLI before dispatch, including Docker Desktop's bundled credential helper.

## Smoke and results

`api_smoke.py` is an explicit paid check: model availability, the search client's
JSON response, then two turns through the actual Harbor/LiteLLM/Terminus parser.
It saves `runs/coding/logs/api_smoke.json`. Normal offline tests make no paid calls.

Benchmark smoke uses `extract-elf` and `fix-git`, k=1, concurrency=1, at most
40 turns and 600 seconds of agent execution per task, and 20000 output tokens per
request, matching the regular policy budget. An 8192-token budget caused repeated
truncation on `extract-elf`; thinking mode needs headroom even with low effort
([provider token parameters](https://api-docs.deepseek.com/api/create-chat-completion/)).
Official verifier timeouts remain unchanged. `--working-tree` tests
uncommitted code without creating branches or worktrees; ordinary `smoke` tests
the committed incumbent. Every CLI smoke gets a unique job ID.

`runs/coding/jobs/<job>/` holds trajectories, recordings and verifier results.
`runs/coding/logs/<job>.json` reports integration success, each task's reward and
tokens, elapsed time, sampled task-container CPU/memory peaks and remaining Docker
resources. Reward 0 is a valid task failure; timeouts, missing verifier output,
transport, protocol or infrastructure errors fail smoke even when reward is 1.
Tokens are Harbor's reported usage; failed or truncated calls may be uncounted,
so these are not billing records.
Peaks are sampled every 10 seconds
and may miss short spikes. Image download/build and controller resources are
not included in task-container memory peaks. No GPU is needed for API inference.

Sandboxes use linux/amd64; Apple Silicon relies on Docker Desktop's emulation.
Start with concurrency 1. A suggested smoke allocation is 4 Docker vCPUs, 8 GB RAM
and 20–30 GB available disk; actual needs depend on pulled task images.

## Full RSI and cleanup

Commit the harness you intend to evolve before a full RSI run, which creates Git
worktrees from committed refs. Baseline, full rounds and SWE-bench are separate,
explicit actions; smoke does not start them:

```bash
.venv/bin/python rrsi.py --domain coding baseline  # 89 x k=2 trials; calibrates delta
.venv/bin/python rrsi.py --domain coding run       # T=20
bash domains/coding/scripts/swe_eval.sh            # optional OOD evaluation
```

Use the Docker controller prefix above when using its environment.
For the optional shell script, use
`bash domains/coding/scripts/docker_controller.sh --shell domains/coding/scripts/swe_eval.sh`.
Selection weights and the RSI algorithm are retained; `delta=null` requires fresh DeepSeek
calibration. A frontier or job from another/unknown model, effort, token budget or API endpoint
is rejected rather than reused. Choose a new repo-local `--runs` path for a new
policy. Job manifests also record requested tasks and harness SHA256.
Cached calibration also records the policy. `calibrate` requires full-suite jobs
with matching policy, harness and attempt count; legacy or smoke results are rejected.

Each Compose project is namespaced by repository and recorded in the job's
`docker.jsonl`. Cleanup requires an explicit job and removes only its recorded
containers, networks and volumes; it never runs global prune:

```bash
bash domains/coding/scripts/cleanup_docker.sh --job <job>
# Docker-controller environment:
bash domains/coding/scripts/docker_controller.sh -m domains.coding.docker_resources --job <job>
```

The adapter also cleans those resources when interrupted. On abnormal controller
termination, use the explicit cleanup command; existing job logs are preserved.
