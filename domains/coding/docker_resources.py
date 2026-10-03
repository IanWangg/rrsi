"""Portable Docker dispatch and cleanup restricted to recorded Compose projects."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
from pathlib import Path

from domains.coding.runtime import Runtime, job_name


def prefix(runtime: Runtime) -> str:
    return "rrsi-" + hashlib.sha256(str(runtime.repo).encode()).hexdigest()[:12] + "-"


def projects(runtime: Runtime, manifest: Path) -> list[str]:
    manifest = runtime.inside(manifest)
    if not manifest.exists():
        return []
    names = {json.loads(line)["project"] for line in manifest.read_text().splitlines() if line.strip()}
    if any(not name.startswith(prefix(runtime)) for name in names):
        raise ValueError("manifest contains a foreign Docker project")
    return sorted(names)


def docker_env() -> dict:
    # API credentials belong to the controller, never benchmark task containers.
    return {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}


def dispatch(args: list[str]) -> int:
    runtime = Runtime()
    binary = runtime.docker()
    if "compose" in args:
        for index, arg in enumerate(args):
            if arg in ("--project-name", "-p"):
                project = args[index + 1]
                project = project if project.startswith(prefix(runtime)) else prefix(runtime) + project
                args[index + 1] = project
                manifest_value = os.environ.get("RRSI_DOCKER_MANIFEST")
                if not manifest_value:
                    raise RuntimeError("RRSI_DOCKER_MANIFEST is required for Coding Compose operations")
                manifest = runtime.inside(manifest_value)
                manifest.parent.mkdir(parents=True, exist_ok=True)
                with manifest.open("a") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    stream.write(json.dumps({"project": project}) + "\n")
                break
    return subprocess.run([binary, *args], env=docker_env()).returncode


def resources(runtime: Runtime, manifest: Path) -> dict[str, list[str]]:
    binary = runtime.docker()
    found = {"container": [], "network": [], "volume": []}
    for project in projects(runtime, manifest):
        for kind, command in (("container", ["ps", "-aq"]),
                              ("network", ["network", "ls", "-q"]),
                              ("volume", ["volume", "ls", "-q"])):
            result = subprocess.run([binary, *command, "--filter",
                f"label=com.docker.compose.project={project}"], capture_output=True,
                text=True, check=True, env=docker_env())
            found[kind].extend(result.stdout.split())
    return {k: sorted(set(v)) for k, v in found.items()}


def cleanup(runtime: Runtime, manifest: Path) -> dict:
    binary = runtime.docker()
    found = resources(runtime, manifest)
    for kind, identifiers in found.items():
        for identifier in identifiers:
            command = ["rm", "-f", identifier] if kind == "container" else [kind, "rm", identifier]
            subprocess.run([binary, *command], check=True, capture_output=True,
                           text=True, env=docker_env())
    return {"removed": found, "remaining": resources(runtime, manifest)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--jobs-dir", default="runs/coding/jobs")
    args = parser.parse_args()
    runtime = Runtime()
    runtime.activate()
    manifest = runtime.inside(runtime.inside(args.jobs_dir) / job_name(args.job) / "docker.jsonl")
    print(json.dumps(cleanup(runtime, manifest), indent=2))


if __name__ == "__main__":
    main()
