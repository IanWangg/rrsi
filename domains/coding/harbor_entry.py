"""Frozen Harbor launch configuration; also usable for bounded protocol checks."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from domains.coding.runtime import Runtime, configure_harbor, job_name
from rrsi.deepseek import is_deepseek, settings

POLICY_MAX_TOKENS = 20000


def agent_kwargs(model: str, smoke: bool = False) -> dict:
    if is_deepseek(model):
        cfg = settings()
        values = {"api_base": cfg.api_base, "reasoning_effort": cfg.reasoning_effort,
            "interleaved_thinking": False, "llm_kwargs": {"num_retries": 0},
            "llm_call_kwargs": {"extra_body": cfg.body, "timeout": 120,
                                "max_tokens": POLICY_MAX_TOKENS},
            "model_info": {"max_input_tokens": 1048576, "max_output_tokens": 393216,
                           "litellm_provider": "deepseek", "mode": "chat"}}
    else:
        if not os.environ.get("VERTEXAI_PROJECT") and model.startswith("vertex_ai/"):
            raise RuntimeError("set VERTEXAI_PROJECT for a Vertex policy")
        values = {"llm_kwargs": {"num_retries": 0}}
    if smoke:
        values.update(max_turns=40, suppress_max_turns_warning=True)
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("dataset")
    parser.add_argument("attempts", type=int)
    parser.add_argument("concurrency", type=int)
    parser.add_argument("--jobs-dir", required=True)
    parser.add_argument("--smoke", action="store_true")
    args, extra = parser.parse_known_args()
    runtime = Runtime()
    runtime.activate()
    name = job_name(args.job)
    jobs = runtime.inside(args.jobs_dir)
    source = runtime.inside(os.environ.get("RRSI_CODING_ROOT", str(runtime.repo / "domains/coding")))
    if args.attempts < 1 or args.concurrency < 1:
        parser.error("attempts and concurrency must be positive")
    cfg = json.loads((runtime.repo / "domains/coding/rrsi.json").read_text())
    model = os.environ.get("MODEL", cfg["policy_model"])
    if args.smoke:
        os.environ["DEEPSEEK_REASONING_EFFORT"] = "low"
    values = agent_kwargs(model, args.smoke)
    os.environ["RRSI_DOCKER_BIN"] = runtime.docker()
    os.environ["RRSI_DOCKER_MANIFEST"] = str(runtime.inside(jobs / name / "docker.jsonl"))
    os.environ["PATH"] = f"{runtime.repo / 'domains/coding/bin'}{os.pathsep}{os.environ.get('PATH', '')}"
    sys.path.insert(0, str(source.parents[1] / "third_party"))
    configure_harbor(runtime)
    argv = ["harbor", "run", "-d", args.dataset, "--agent", "harbor_terminus2:AgentHarness",
            "-m", model, "-k", str(args.attempts), "-n", str(args.concurrency),
            "--jobs-dir", str(jobs), "--job-name", name]
    for key, value in values.items():
        argv += ["--agent-kwarg", f"{key}={json.dumps(value)}"]
    # Prevent overrides that bypass the frozen provider or filesystem boundary.
    allowed = {"-i", "--include-task-name", "-x", "--exclude-task-name", "-l", "--n-tasks"}
    index = 0
    while index < len(extra):
        flag = extra[index].split("=", 1)[0]
        if flag in {"-q", "--quiet"}:
            index += 1
        elif flag in allowed:
            index += 1 if "=" in extra[index] else 2
            if index > len(extra):
                parser.error(f"missing value for {flag}")
        else:
            parser.error("extra flags may only filter tasks or enable quiet output")
    sys.argv = argv + extra
    if args.smoke:
        # Harbor 0.23 exposes timeout multipliers on the CLI, but the typed
        # AgentConfig supports an absolute cap. Preserve each task's verifier.
        from functools import wraps
        import harbor.cli.jobs as jobs_cli
        build = jobs_cli.build_job_config

        @wraps(build)
        def bounded_config(*args, **kwargs):
            config = build(*args, **kwargs)
            for agent in config.agents:
                agent.max_timeout_sec = 600
            return config
        jobs_cli.build_job_config = bounded_config
    from harbor.cli.main import app
    app()


if __name__ == "__main__":
    main()
