"""Explicit, paid DeepSeek API + Terminus JSON protocol smoke (no benchmark run)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from domains.coding.runtime import Runtime, configure_harbor
from domains.coding.harbor_entry import agent_kwargs
from rrsi.deepseek import redact, settings
from rrsi.llm import generate


async def protocol(runtime, model, logs):
    sys.path.insert(0, str(runtime.repo / "third_party"))
    from harbor_terminus2 import AgentHarness
    from harbor.llms.chat import Chat
    agent = AgentHarness(logs_dir=logs, model_name=model, **agent_kwargs(model, True))
    chat = Chat(agent._llm, interleaved_thinking=False)
    prompt = ('Return only one JSON object with analysis (string), plan (string), '
              'commands (array of {keystrokes: string, duration: number}), task_complete (boolean). '
              'First request: send one harmless command printf RRSI_OK followed by newline. '
              'Do not mark complete yet. After the terminal reports RRSI_OK, return no commands and task_complete=true.')
    usages = []
    for turn, message in enumerate((prompt, "Terminal output: RRSI_OK. Mark the task complete.")):
        response = await chat.chat(message, **agent._llm_call_kwargs)
        parsed = agent._parser.parse_response(response.content)
        if parsed.error or (turn == 0 and not parsed.commands) or (turn == 1 and not parsed.is_task_complete):
            raise RuntimeError(f"Terminus protocol failed on turn {turn + 1}: {parsed.error}")
        usage = response.usage
        usages.append({"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens} if usage else None)
    return {"turns": 2, "usage": usages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="runs/coding/logs/api_smoke.json")
    args = parser.parse_args()
    runtime = Runtime()
    runtime.activate()
    os.environ["DEEPSEEK_REASONING_EFFORT"] = "low"
    path = runtime.inside(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    model = json.loads((runtime.repo / "domains/coding/rrsi.json").read_text())["policy_model"]
    report = {"ok": False, "model": model, "reasoning_effort": "low", "stages": {}}
    start = time.monotonic()
    try:
        cfg = settings()
        from openai import OpenAI
        with OpenAI(api_key=cfg.api_key, base_url=cfg.api_base, max_retries=0, timeout=120) as client:
            models = client.models.list()
        report["stages"]["models"] = [m.id for m in models.data]
        if model.removeprefix("deepseek/") not in report["stages"]["models"]:
            raise RuntimeError("Configured deepseek-flash is not available; no model substitution was made")
        response = generate('Return the JSON object {"ok": true}.', json_only=True,
            model=model, max_tokens=8192, cache_prefix="RRSI Coding provider integration smoke.")
        if json.loads(response) != {"ok": True}:
            raise RuntimeError("Search client did not return the expected JSON object")
        report["stages"]["search_json"] = {"ok": True}
        configure_harbor(runtime)
        report["stages"]["terminus_json_protocol"] = asyncio.run(protocol(runtime, model, path.parent / "api_smoke_agent"))
        report["ok"] = True
    except Exception as error:
        report["error"] = redact(f"{type(error).__name__}: {error}")
    report["elapsed_seconds"] = round(time.monotonic() - start, 2)
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({**report, "report_path": str(path)}, indent=2))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
