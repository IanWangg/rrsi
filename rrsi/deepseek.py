"""Shared DeepSeek settings and bounded transport retries (no import-time SDK)."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    api_base: str = "https://api.deepseek.com"
    reasoning_effort: str = "low"

    @property
    def body(self) -> dict:
        return {"reasoning_effort": self.reasoning_effort,
                "thinking": {"type": "disabled" if self.reasoning_effort == "none" else "enabled"}}


def is_deepseek(model: str) -> bool:
    return model.startswith(("deepseek/", "deepseek-"))


def settings(*, require_key: bool = True) -> Settings:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if require_key and not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not available to this process")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    url = urlsplit(base)
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or (url.scheme != "https" and not (
                url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("DEEPSEEK_BASE_URL must be HTTPS (HTTP is allowed for loopback tests)")
    effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", "low")
    if effort not in {"none", "low", "high", "max"}:
        raise ValueError("DEEPSEEK_REASONING_EFFORT must be none, low, high, or max")
    return Settings(key, base, effort)


def retryable(error: BaseException) -> bool:
    """Only retry transport, rate-limit and server failures, never bad requests."""
    status = getattr(error, "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    return isinstance(error, (TimeoutError, ConnectionError)) or type(error).__name__ in {
        "APIConnectionError", "APITimeoutError", "Timeout", "ConnectError", "ReadTimeout",
    }


def redact(value):
    """Remove credentials, including nested HTTP headers, from persisted diagnostics."""
    if isinstance(value, dict):
        return {k: ("<redacted>" if str(k).lower().replace("-", "_") in {
            "api_key", "authorization", "x_api_key", "deepseek_api_key"
        } else redact(v)) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(x) for x in value]
    if isinstance(value, str):
        key = os.environ.get("DEEPSEEK_API_KEY")
        return value.replace(key, "<redacted>") if key else value
    return value


def generate(prompt: str, system: str, model: str, max_tokens: int,
             json_only: bool, cache_prefix: str | None, attempts: int) -> str:
    from openai import OpenAI

    cfg = settings()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    # DeepSeek caches stable prefixes automatically; Anthropic cache_control blocks
    # are not part of its Chat Completions schema.
    messages.append({"role": "user", "content":
                     f"{cache_prefix}\n\n{prompt}" if cache_prefix else prompt})
    kwargs = {"model": model.removeprefix("deepseek/"), "messages": messages,
              "max_tokens": max_tokens, "reasoning_effort": cfg.reasoning_effort,
              "extra_body": {"thinking": cfg.body["thinking"]}}
    if json_only:
        kwargs["response_format"] = {"type": "json_object"}
    n = max(1, min(attempts, 3))
    with OpenAI(api_key=cfg.api_key, base_url=cfg.api_base,
                timeout=120, max_retries=0) as client:
        for attempt in range(n):
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as error:
                if not retryable(error) or attempt == n - 1:
                    raise RuntimeError(f"DeepSeek request failed: {redact(str(error))}") from None
                time.sleep(2 ** attempt)
                continue
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise RuntimeError("DeepSeek output was truncated at max_tokens")
            text = choice.message.content
            if not text or not text.strip():
                raise RuntimeError("DeepSeek returned no final content")
            if json_only:
                json.loads(text)
            usage_path = os.environ.get("RRSI_LLM_USAGE_PATH")
            if usage_path:
                from domains.coding.runtime import Runtime
                path = Runtime().inside(usage_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as stream:
                    stream.write(json.dumps({"model": kwargs["model"],
                        "reasoning_effort": cfg.reasoning_effort,
                        "usage": response.usage.model_dump() if response.usage else None}) + "\n")
            return text
    raise AssertionError("unreachable")
