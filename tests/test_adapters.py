"""CLI adapters: command construction, stream parsing, and the process runner.

No real provider is called: parsing is tested on recorded-shape samples, and
the process runner is exercised with a tiny Python stand-in for a CLI.
"""

import asyncio
import json
import sys
import textwrap
import time

from glass_membrane.adapters.base import Invocation
from glass_membrane.adapters.claude_code import ClaudeCodeAdapter, parse_claude_stream
from glass_membrane.adapters.claude_code import interpret as claude_interpret
from glass_membrane.adapters.codex import CodexAdapter, parse_codex_events
from glass_membrane.adapters.codex import interpret as codex_interpret
from glass_membrane.adapters.process import run_process
from glass_membrane.roles import WORKER_SCHEMA


def make_inv(**overrides):
    fields = dict(run_id="r", label="E0", role="efficiency", system_prompt="SYSTEM", prompt="PROMPT",
                  schema=WORKER_SCHEMA, model="haiku", effort="low", timeout_s=30)
    fields.update(overrides)
    return Invocation(**fields)


def flag(argv, name):
    return argv[argv.index(name) + 1]


# ---------------------------------------------------------------- Claude Code

def test_claude_command_is_subscription_safe_and_tool_free(tmp_path, monkeypatch):
    monkeypatch.setattr("glass_membrane.adapters.claude_code.resolve_command", lambda c: ["claude"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDECODE", "1")
    adapter = ClaudeCodeAdapter("claude", tmp_path)
    argv, env = adapter.build(make_inv())
    assert "--bare" not in argv, "--bare would skip the subscription login"
    assert flag(argv, "--output-format") == "stream-json" and "--no-session-persistence" in argv
    assert flag(argv, "--tools") == "" and flag(argv, "--permission-prompts") == "none"
    assert flag(argv, "--model") == "haiku" and flag(argv, "--effort") == "low"
    assert json.loads(flag(argv, "--json-schema")) == WORKER_SCHEMA
    assert flag(argv, "--system-prompt") == "SYSTEM"
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env

    argv, env = adapter.build(make_inv(swarm_grant=2, env={"CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "2"}))
    assert flag(argv, "--tools") == "Agent"
    assert env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "2"


CLAUDE_STREAM = [
    json.dumps({"type": "system", "subtype": "init", "model": "claude-haiku-4-5", "tools": ["Agent"]}),
    json.dumps({"type": "system", "subtype": "api_retry", "error": "rate_limit", "attempt": 1}),
    json.dumps({"type": "assistant", "parent_tool_use_id": None,
                "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Agent", "input": {}}]}}),
    json.dumps({"type": "assistant", "parent_tool_use_id": None,
                "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Agent", "input": {}}]}}),
    json.dumps({"type": "assistant", "parent_tool_use_id": "t1",
                "message": {"content": [{"type": "tool_use", "id": "t9", "name": "Agent"}]}}),
    "not json",
    json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "{}",
                "structured_output": {"answer": "4", "summary": "4", "confidence": 1, "uncertainty": None,
                                      "status": "COMPLETE", "shift": None},
                "total_cost_usd": 0.0123, "usage": {"input_tokens": 10, "output_tokens": 5},
                "modelUsage": {"claude-haiku": {"inputTokens": 100, "outputTokens": 40, "cacheReadInputTokens": 7,
                                                "cacheCreationInputTokens": 3, "costUSD": 0.01},
                               "claude-sonnet": {"inputTokens": 50, "outputTokens": 10, "costUSD": 0.0023}}}),
]


def test_claude_stream_counts_children_once_and_uses_whole_tree_usage():
    info = parse_claude_stream(CLAUDE_STREAM)
    assert info["children"] == 1, "duplicate and nested Agent calls are not double-counted"
    assert info["rate_limit_retries"] == 1
    result = claude_interpret(info, 0, "", False, 30, 1.0)
    assert result.ok and result.output["answer"] == "4"
    assert result.usage.input_tokens == 153 and result.usage.output_tokens == 50
    assert result.usage.cached_input_tokens == 7 and result.usage.cost_usd == 0.0123
    assert result.children_spawned == 1


def test_claude_failures_are_classified():
    limited = parse_claude_stream([json.dumps({"type": "result", "subtype": "error_during_execution",
                                               "is_error": True, "result": "API Error: 429 rate_limit_error"})])
    result = claude_interpret(limited, 1, "", False, 30, 1.0)
    assert not result.ok and result.rate_limited
    timeout = claude_interpret(parse_claude_stream([]), None, "", True, 30, 30.0)
    assert timeout.failure == "TOOL_FAILURE" and "timed out" in timeout.detail
    budget = parse_claude_stream([json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True})])
    assert claude_interpret(budget, 1, "", False, 30, 1.0).failure == "BUDGET_EXHAUSTED"


# ---------------------------------------------------------------- Codex

def test_codex_command_is_read_only_and_subscription_safe(tmp_path, monkeypatch):
    monkeypatch.setattr("glass_membrane.adapters.codex.resolve_command", lambda c: ["codex"])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = CodexAdapter("codex", tmp_path / "sandbox")
    argv, env = adapter.build(make_inv(model=None, extra_args=["--disable", "multi_agent"]),
                              tmp_path / "schema.json", tmp_path / "out.json")
    assert argv[:2] == ["codex", "exec"] and flag(argv, "-s") == "read-only"
    assert "--json" in argv and "--ephemeral" in argv and "-m" not in argv
    assert "model_reasoning_effort=low" in argv and argv[-1] == "-"
    assert argv[argv.index("--disable") + 1] == "multi_agent"
    assert "OPENAI_API_KEY" not in env


def test_codex_events_usage_and_failures():
    events = [json.dumps({"type": "thread.started"}),
              json.dumps({"type": "turn.completed",
                          "usage": {"input_tokens": 120, "cached_input_tokens": 100, "output_tokens": 30}}),
              json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"answer":"4"}'}})]
    info = parse_codex_events(events)
    result = codex_interpret(info, '{"answer": "4", "summary": "4"}', 0, "", False, 30, 0, 1.0)
    assert result.ok and result.output["answer"] == "4"
    assert (result.usage.input_tokens, result.usage.cached_input_tokens, result.usage.output_tokens) == (120, 100, 30)
    assert result.children_spawned == 0
    granted = codex_interpret(info, None, 0, "", False, 30, 2, 1.0)
    assert granted.ok and granted.children_spawned is None, "Codex child counts are undocumented: unknown, not 0"
    failed = codex_interpret(parse_codex_events([json.dumps({"type": "turn.failed",
                                                             "error": {"message": "You've hit your usage limit"}})]),
                             None, 1, "", False, 30, 0, 1.0)
    assert not failed.ok and failed.rate_limited


# ---------------------------------------------------------------- process runner

FAKE_CLI = textwrap.dedent("""
    import json, sys, time
    prompt = sys.stdin.read()
    if "SLEEP" in prompt:
        time.sleep(30)
    print(json.dumps({"type": "result", "result": prompt.strip()}), flush=True)
""")


def test_run_process_passes_stdin_and_collects_stdout(tmp_path):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI)
    result = asyncio.run(run_process([sys.executable, str(script)], stdin_text="hello", cwd=tmp_path,
                                     env=None, timeout=20))
    assert result.returncode == 0 and json.loads(result.stdout_lines[-1])["result"] == "hello"


def test_run_process_kills_the_process_on_timeout(tmp_path):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI)
    started = time.monotonic()
    result = asyncio.run(run_process([sys.executable, str(script)], stdin_text="SLEEP", cwd=tmp_path,
                                     env=None, timeout=1))
    assert result.timed_out and time.monotonic() - started < 10
