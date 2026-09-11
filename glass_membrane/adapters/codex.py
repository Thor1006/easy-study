"""Codex CLI adapter: `codex exec` on the user's ChatGPT login.

Documented behaviour this relies on (developers.openai.com/codex/noninteractive,
/codex/subagents, /codex/config-reference):
- `--json` streams JSONL events; `turn.completed` carries `usage` with
  input_tokens, cached_input_tokens, output_tokens.
- `--output-schema FILE` constrains the final message; `-o FILE` writes it.
- `-s read-only` sandboxes model-generated commands.
- Subagents are on by default; `agents.max_concurrent_threads_per_session`
  caps them. Per-child usage is not documented, so it is reported as unknown.
Codex `exec` has no system-prompt flag, so the role instructions are sent at
the top of the prompt.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from ..refs import new_id
from .base import Invocation, ModelAdapter, ModelResult, Usage
from .claude_code import parse_json_object
from .process import resolve_command, run_process

_RATE_WORDS = ("rate limit", "rate_limit", "429", "usage limit", "too many requests", "quota")
_STRIP_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")


def parse_codex_events(lines: list[str]) -> dict:
    info = {"input": 0, "cached": 0, "output": 0, "usage_seen": False, "errors": [], "messages": []}
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type", ""))
        if kind == "turn.completed":
            usage = event.get("usage") or {}
            info["usage_seen"] = True
            info["input"] += int(usage.get("input_tokens") or 0)
            info["cached"] += int(usage.get("cached_input_tokens") or 0)
            info["output"] += int(usage.get("output_tokens") or 0)
        elif kind in ("turn.failed", "error"):
            err = event.get("error")
            message = err.get("message") if isinstance(err, dict) else (err or event.get("message"))
            info["errors"].append(str(message))
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") in ("agent_message", "assistant_message") and item.get("text"):
                info["messages"].append(item["text"])
    return info


def interpret(info: dict, last_message: str | None, returncode: int | None, stderr: str, timed_out: bool,
              timeout_s: float, swarm_grant: int, duration: float) -> ModelResult:
    usage = (Usage(input_tokens=info["input"], output_tokens=info["output"], cached_input_tokens=info["cached"])
             if info["usage_seen"] else Usage())
    children = 0 if swarm_grant <= 0 else None  # grant 0 disables multi_agent; otherwise count is undocumented
    if timed_out:
        return ModelResult(ok=False, failure="TOOL_FAILURE", detail=f"timed out after {timeout_s:g}s",
                           usage=usage, children_spawned=children, duration_s=duration)
    text = last_message if last_message and last_message.strip() else (info["messages"][-1] if info["messages"] else "")
    output = parse_json_object(text)
    if returncode not in (0, None) or info["errors"] or output is None:
        detail = "; ".join(info["errors"]) or stderr[-800:] or (f"exit code {returncode}" if returncode else
                                                               "no structured output in the final message")
        rate_limited = any(word in f"{detail} {stderr[-800:]}".lower() for word in _RATE_WORDS)
        if output is not None and returncode in (0, None) and not rate_limited:
            pass  # a recovered error with a valid final message still counts as a result
        else:
            return ModelResult(ok=False, failure="TOOL_FAILURE", detail=detail[:1000], rate_limited=rate_limited,
                               raw_text=text[:2000], usage=usage, children_spawned=children, duration_s=duration)
    return ModelResult(ok=True, output=output, raw_text=text, usage=usage, children_spawned=children,
                       duration_s=duration)


class CodexAdapter(ModelAdapter):
    provider = "codex"
    provenance = "Codex CLI exec mode (documented); run `gm probe` to measure on your plan"

    def __init__(self, command: str = "codex", sandbox_dir: str | Path = "runtime/sandbox/codex", *,
                 keep_api_key_env: bool = False) -> None:
        self.command = command
        self.sandbox = Path(sandbox_dir)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.calls_dir = self.sandbox.parent / "codex-calls"
        self.keep_api_key_env = keep_api_key_env
        self.capabilities = {"structured_output": "documented", "swarm": "documented",
                             "usage": "documented (turn.completed)", "child_usage": "unknown",
                             "cancellation": "process kill"}

    def build(self, inv: Invocation, schema_path: Path, out_path: Path) -> tuple[list[str], dict[str, str]]:
        argv = resolve_command(self.command) + [
            "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--color", "never",
            "-s", "read-only", "-C", str(self.sandbox),
            "--output-schema", str(schema_path), "-o", str(out_path),
        ]
        if inv.model:
            argv += ["-m", inv.model]
        if inv.effort:
            argv += ["-c", f"model_reasoning_effort={inv.effort}"]
        argv += list(inv.extra_args)
        argv.append("-")  # read the prompt from stdin
        env = {k: v for k, v in os.environ.items() if self.keep_api_key_env or k not in _STRIP_ENV}
        env.update(inv.env)
        return argv, env

    async def invoke(self, inv: Invocation) -> ModelResult:
        started = time.monotonic()
        call_dir = self.calls_dir / new_id("call-")
        call_dir.mkdir(parents=True, exist_ok=True)
        schema_path, out_path = call_dir / "schema.json", call_dir / "last_message.json"
        schema_path.write_text(json.dumps(inv.schema), encoding="utf-8")
        try:
            try:
                argv, env = self.build(inv, schema_path, out_path)
            except FileNotFoundError as exc:
                return ModelResult(ok=False, failure="TOOL_FAILURE", detail=str(exc))
            stdin = f"{inv.system_prompt}\n\n---\n\n{inv.prompt}"
            proc = await run_process(argv, stdin_text=stdin, cwd=self.sandbox, env=env, timeout=inv.timeout_s)
            last = out_path.read_text(encoding="utf-8") if out_path.exists() else None
            info = parse_codex_events(proc.stdout_lines)
            return interpret(info, last, proc.returncode, proc.stderr, proc.timed_out, inv.timeout_s,
                             inv.swarm_grant, time.monotonic() - started)
        finally:
            shutil.rmtree(call_dir, ignore_errors=True)
