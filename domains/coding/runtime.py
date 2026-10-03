"""Repo-confined Coding runtime, shared by CLI, Harbor and candidate worktrees."""
from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = {"harbor": "0.23.0", "litellm": "1.103.2", "openai": "2.54.0"}


class Runtime:
    def __init__(self, repo: Path | None = None):
        if repo is None:
            # A candidate's harness must share the main checkout's runtime.
            common = subprocess.run(["git", "-C", str(SOURCE_ROOT), "rev-parse",
                                     "--path-format=absolute", "--git-common-dir"],
                                    capture_output=True, text=True)
            repo = Path(common.stdout.strip()).parent if common.returncode == 0 else SOURCE_ROOT
        self.repo = Path(repo).resolve()
        configured = os.environ.get("RRSI_REPO_ROOT")
        if configured and Path(configured).resolve() != self.repo:
            raise ValueError("RRSI_REPO_ROOT must identify the main repository")
        self.base = self.inside(self.repo / ".runtime")
        self.venv = self.inside(os.environ.get("RRSI_CODING_VENV", str(self.repo / ".venv")))

    def inside(self, path: Path | str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.repo / p
        p = p.resolve()
        if p == self.repo or not p.is_relative_to(self.repo):
            raise ValueError(f"Coding writable path must be below this repository: {p}")
        return p

    @property
    def python(self) -> Path:
        p = Path(os.environ.get("RRSI_CODING_PYTHON", str(self.venv / "bin/python")))
        if not p.is_absolute():
            p = self.repo / p
        # venv Python is normally a symlink to a system executable. Constrain its
        # containing environment, rather than rejecting that legitimate symlink.
        self.inside(p.parent)
        return p

    def activate(self) -> None:
        desktop_bin = Path("/Applications/Docker.app/Contents/Resources/bin")
        if desktop_bin.is_dir() and str(desktop_bin) not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = str(desktop_bin) + os.pathsep + os.environ.get("PATH", "")
        paths = {
            "TMPDIR": self.base / "tmp",
            "PIP_CACHE_DIR": self.base / "cache/pip",
            "XDG_CACHE_HOME": self.base / "cache",
            "XDG_CONFIG_HOME": self.base / "config",
            "TIKTOKEN_CACHE_DIR": self.base / "cache/tiktoken",
            "HF_HOME": self.base / "cache/huggingface",
            "BUILDX_CONFIG": self.base / "docker/buildx",
            "RRSI_STATE_DIR": os.environ.get("RRSI_STATE_DIR", str(self.base / "state")),
            "RRSI_LLM_USAGE_PATH": os.environ.get("RRSI_LLM_USAGE_PATH",
                str(self.repo / "runs/coding/logs/search_usage.jsonl")),
        }
        for name, value in paths.items():
            if name == "RRSI_LLM_USAGE_PATH" and value == "":
                continue
            path = self.inside(value)
            (path.parent if name == "RRSI_LLM_USAGE_PATH" else path).mkdir(parents=True, exist_ok=True)
            os.environ[name] = str(path)
        tempfile.tempdir = None
        sys.pycache_prefix = str(self.base / "cache/pycache")
        os.environ.update(RRSI_REPO_ROOT=str(self.repo), RRSI_CODING_VENV=str(self.venv),
                          DOCKER_DEFAULT_PLATFORM="linux/amd64", HARBOR_TELEMETRY="0",
                          LITELLM_LOCAL_MODEL_COST_MAP="True", LITELLM_LOG="ERROR")

    def docker(self) -> str:
        explicit = os.environ.get("RRSI_DOCKER_BIN")
        candidates = [explicit] if explicit else [
            str(Path(folder) / "docker") for folder in os.environ.get("PATH", "").split(os.pathsep)] + [
            "/Applications/Docker.app/Contents/Resources/bin/docker",
            str(Path.home() / ".docker/bin/docker"),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            p = Path(candidate).expanduser()
            if (p.is_file() and os.access(p, os.X_OK)
                    and not (p.parent.name == "bin" and p.parent.parent.name == "coding")):
                return str(p.resolve())
        raise RuntimeError("Docker CLI not found; install/start Docker Desktop or set RRSI_DOCKER_BIN")

    def preflight(self) -> dict:
        from rrsi.deepseek import settings
        self.activate()
        checks = {"python": sys.version.split()[0], "repo": str(self.repo),
                  "runtime": str(self.base), "platform": "linux/amd64"}
        errors = []
        if sys.version_info < (3, 12):
            errors.append("Coding requires Python >=3.12")
        try:
            cfg = settings()
            checks.update(api_key_available=True, api_base=cfg.api_base,
                          reasoning_effort=cfg.reasoning_effort)
        except (RuntimeError, ValueError) as error:
            checks["api_key_available"] = bool(os.environ.get("DEEPSEEK_API_KEY"))
            errors.append(str(error))
        deps = {}
        for name, wanted in VERSIONS.items():
            try:
                deps[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                deps[name] = None
            if deps[name] != wanted:
                errors.append(f"{name}=={wanted} required (found {deps[name]})")
        checks["dependencies"] = deps
        try:
            binary = self.docker()
            checks["docker_bin"] = binary
            for label, args in (("docker", ["version", "--format", "{{json .Server}}"]),
                                ("compose", ["compose", "version", "--short"]),
                                ("buildx", ["buildx", "version"])):
                result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=30)
                if result.returncode:
                    errors.append(f"{label} unavailable: {result.stderr.strip()[-400:]}")
                else:
                    checks[label] = result.stdout.strip()
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
        checks["disk_free_gb"] = round(shutil.disk_usage(self.repo).free / 1024**3, 1)
        return {"ok": not errors, "checks": checks, "errors": errors}


def job_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("job name must start with an alphanumeric and contain only letters, digits, _, -, .")
    return name


def configure_harbor(runtime: Runtime) -> None:
    """Override upstream's home-directory constants before its CLI imports them."""
    runtime.activate()
    import harbor.constants as constants
    constants.CACHE_DIR = runtime.inside(runtime.base / "cache/harbor")
    constants.TASK_CACHE_DIR = constants.CACHE_DIR / "tasks"
    constants.PACKAGE_CACHE_DIR = constants.TASK_CACHE_DIR / "packages"
    constants.DATASET_CACHE_DIR = constants.CACHE_DIR / "datasets"
    constants.NOTIFICATIONS_PATH = constants.CACHE_DIR / "notifications.json"
    import harbor.auth.constants as auth
    auth.CREDENTIALS_DIR = runtime.inside(runtime.base / "config/harbor")
    auth.CREDENTIALS_PATH = auth.CREDENTIALS_DIR / "credentials.json"
    auth.OAUTH_PENDING_PATH = auth.CREDENTIALS_DIR / "oauth-pending.json"
    import harbor.environments.docker.utils as docker_utils
    docker_utils._docker_build_cache_dir = lambda: constants.CACHE_DIR / "docker_build"

    async def platform():
        return "linux/amd64"
    docker_utils.default_docker_platform = platform
    import harbor.environments.docker.docker as docker_environment
    docker_environment.default_docker_platform = platform

    from harbor.llms.lite_llm import LiteLLM
    from rrsi.deepseek import is_deepseek, redact, retryable
    from functools import wraps
    from tenacity import retry_if_exception

    if not getattr(LiteLLM.call, "_rrsi_provider_routing", False):
        original_call = LiteLLM.call
        deepseek_call = original_call.retry_with(retry=retry_if_exception(retryable))

        @wraps(original_call)
        async def provider_call(self, *args, **kwargs):
            if not is_deepseek(self._model_name):
                return await original_call(self, *args, **kwargs)
            try:
                return await deepseek_call(self, *args, **kwargs)
            except Exception as error:
                if getattr(error, "status_code", None) is not None or retryable(error):
                    # The harness must not multiply this backend's retry budget.
                    error._rrsi_provider_exhausted = True
                    error.args = tuple(redact(value) for value in error.args)
                    if isinstance(getattr(error, "message", None), str):
                        error.message = redact(error.message)
                raise

        provider_call._rrsi_provider_routing = True
        provider_call._rrsi_deepseek_call = deepseek_call
        LiteLLM.call = provider_call

    def logger_fn(self, logging_path):
        def log(model_call):
            if model_call.get("log_event_type") == "post_api_call":
                safe = redact(self._clean_value(model_call))
                logging_path.write_text(json.dumps(safe, indent=2, default=str))
        return log
    LiteLLM._init_logger_fn = logger_fn
