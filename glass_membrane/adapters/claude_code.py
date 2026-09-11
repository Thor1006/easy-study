"""Claude Code CLI adapter: `claude -p` on the user's subscription login.

Documented behaviour this relies on (code.claude.com/docs/en/headless,
/agent-sdk/cost-tracking, /agent-sdk/subagents):
- `--output-format stream-json --verbose` emits JSON lines ending in a
  `result` message; `--json-schema` puts the object in `structured_output`.
- `modelUsage` / `total_cost_usd` include subagent work; `usage` does not.
- Subagent calls are `tool_use` blocks named Agent (older: Task).
- Rate-limit retries surface as `system/api_retry` with `error: rate_limit`.
- `--bare` does not read subscription credentials, so it is not used.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .base import Invocation, ModelAdapter, ModelResult, Usage, quota_reset_time
from .process import resolve_command, run_process

_RATE_WORDS = ("rate_limit", "rate limit", "429", "usage limit", "too many requests")
# Child processes must not inherit a parent Claude Code session marker, and must use
# the subscription login rather than an API key found in the environment.
_STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def parse_claude_stream(lines: list[str]) -> dict:
    info = {"children": 0, "rate_limit_retries": 0, "retries": 0, "result": None, "init": None}
    seen_tool_ids: set[str] = set()
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            info["init"] = {"model": event.get("model"), "tools": event.get("tools")}
        elif kind == "system" and event.get("subtype") == "api_retry":
            info["retries"] += 1
            if event.get("error") == "rate_limit":
                info["rate_limit_retries"] += 1
        elif kind == "assistant" and not event.get("parent_tool_use_id"):
            for block in (event.get("message") or {}).get("content") or []:
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") in ("Agent", "Task") and block.get("id") not in seen_tool_ids):
                    seen_tool_ids.add(block.get("id"))
                    info["children"] += 1
        elif kind == "result":
            info["result"] = event
    return info


def usage_from_result(final: dict | None) -> Usage:
    if not final:
        return Usage()
    model_usage = final.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        rows = [v for v in model_usage.values() if isinstance(v, dict)]

        def total(key: str) -> int | None:
            values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
            return int(sum(values)) if values else None

        fresh, created = total("inputTokens"), total("cacheCreationInputTokens")
        return Usage(input_tokens=None if fresh is None and created is None else (fresh or 0) + (created or 0),
                     output_tokens=total("outputTokens"), cached_input_tokens=total("cacheReadInputTokens"),
                     cost_usd=final.get("total_cost_usd"))
    usage = final.get("usage") or {}
    return Usage(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                 cached_input_tokens=usage.get("cache_read_input_tokens"), cost_usd=final.get("total_cost_usd"))


def parse_json_object(text) -> dict | None:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                value = json.loads(match.group(0))
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def interpret(info: dict, returncode: int | None, stderr: str, timed_out: bool, timeout_s: float,
              duration: float) -> ModelResult:
    final = info["result"]
    usage = usage_from_result(final)
    children = info["children"]
    stats = (final or {}).get("subagent_stats")
    if isinstance(stats, dict) and isinstance(stats.get("spawned"), int):
        children = stats["spawned"]  # reported by the CLI itself (observed on Claude Code 2.1.267)
    if timed_out:
        return ModelResult(ok=False, failure="TOOL_FAILURE", detail=f"timed out after {timeout_s:g}s",
                           usage=usage, children_spawned=children, duration_s=duration)
    if final is None or final.get("is_error") or returncode not in (0, None):
        detail = str((final or {}).get("result") or (final or {}).get("subtype") or stderr[-800:]
                     or f"exit code {returncode}")
        text = f"{detail} {(final or {}).get('subtype', '')} {stderr[-800:]}".lower()
        retry_after = quota_reset_time(f"{detail} {stderr[-800:]}")
        rate_limited = retry_after is not None or any(word in text for word in _RATE_WORDS) or (
            final is None and info["rate_limit_retries"] > 0)
        failure = "BUDGET_EXHAUSTED" if (final or {}).get("subtype") == "error_max_budget_usd" else "TOOL_FAILURE"
        return ModelResult(ok=False, failure=failure, detail=detail[:1000], rate_limited=rate_limited,
                           retry_after=retry_after, usage=usage, children_spawned=children, duration_s=duration)
    output = final.get("structured_output")
    if not isinstance(output, dict):
        output = parse_json_object(final.get("result"))
    if not isinstance(output, dict):
        return ModelResult(ok=False, failure="TOOL_FAILURE", detail="no structured output in the result",
                           raw_text=str(final.get("result") or "")[:2000], usage=usage,
                           children_spawned=children, duration_s=duration)
    return ModelResult(ok=True, output=output, raw_text=str(final.get("result") or ""), usage=usage,
                       children_spawned=children, duration_s=duration)


class ClaudeCodeAdapter(ModelAdapter):
    provider = "claude"
    provenance = ("Claude Code CLI headless mode (documented); stdin prompt, structured output, and modelUsage "
                  "measured in a live check on 2026-09-11 (Claude Code 2.1.267); run `gm probe` for capacity")

    def __init__(self, command: str = "claude", sandbox_dir: str | Path = "runtime/sandbox/claude", *,
                 setting_sources: str | None = "local", keep_api_key_env: bool = False) -> None:
        self.command = command
        self.sandbox = Path(sandbox_dir)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.setting_sources = setting_sources
        self.keep_api_key_env = keep_api_key_env
        self.capabilities = {"structured_output": "measured", "swarm": "documented", "usage": "measured",
                             "child_usage": "documented (modelUsage)", "cancellation": "process kill"}

    def build(self, inv: Invocation) -> tuple[list[str], dict[str, str]]:
        argv = resolve_command(self.command) + [
            "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
            "--permission-prompts", "none", "--strict-mcp-config", "--disable-slash-commands",
            "--json-schema", json.dumps(inv.schema, separators=(",", ":")),
            "--system-prompt", inv.system_prompt,
            "--tools", "Agent" if inv.swarm_grant > 0 else "",
        ]
        if self.setting_sources is not None:
            argv += ["--setting-sources", self.setting_sources]
        if inv.model:
            argv += ["--model", inv.model]
        if inv.effort:
            argv += ["--effort", inv.effort]
        if inv.max_budget_usd:
            argv += ["--max-budget-usd", f"{inv.max_budget_usd:g}"]
        strip = _STRIP_ENV if not self.keep_api_key_env else _STRIP_ENV[:2]
        env = {k: v for k, v in os.environ.items() if k not in strip}
        env.update(inv.env)
        return argv, env

    async def invoke(self, inv: Invocation) -> ModelResult:
        started = time.monotonic()
        try:
            argv, env = self.build(inv)
        except FileNotFoundError as exc:
            return ModelResult(ok=False, failure="TOOL_FAILURE", detail=str(exc))
        proc = await run_process(argv, stdin_text=inv.prompt, cwd=self.sandbox, env=env, timeout=inv.timeout_s)
        info = parse_claude_stream(proc.stdout_lines)
        return interpret(info, proc.returncode, proc.stderr, proc.timed_out, inv.timeout_s,
                         time.monotonic() - started)
