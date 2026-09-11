"""`gm doctor`: check that Glass Membrane can run.

By default it uses no provider quota: it checks Python, the install, the
config, the data folder, each CLI's version and login status, and runs a
short self-test of the whole runtime on simulated models. `--live` adds one
tiny real call per provider.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .registry import Config, REPO_ROOT

MARK = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
_STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
              "OPENAI_API_KEY", "CODEX_API_KEY")


@dataclass
class Check:
    name: str
    status: str   # ok | warn | fail
    detail: str


def find_command(name: str) -> str | None:
    return shutil.which(name)


def run_command(argv: list[str], timeout: float = 30) -> tuple[int, str]:
    """Run a CLI the way the adapters do (same environment filtering)."""
    from .adapters.process import resolve_command

    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    try:
        proc = subprocess.run(resolve_command(argv[0]) + argv[1:], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _version(command: str) -> str:
    code, out = run_command([command, "--version"])
    return out.splitlines()[0] if code == 0 and out else "unknown version"


def check_claude(command: str) -> Check:
    name = "Claude Code CLI"
    if not find_command(command):
        return Check(name, "warn", f"`{command}` not found — install Claude Code to use Claude models")
    from .adapters.claude_code import parse_json_object

    version = _version(command)
    _, out = run_command([command, "auth", "status", "--json"])
    info = parse_json_object(out)
    if info is None:
        return Check(name, "warn", f"{version}; could not read the login status — run `claude auth status`")
    if not info.get("loggedIn"):
        return Check(name, "warn", f"{version}; not signed in — run `claude auth login`")
    method, plan = info.get("authMethod") or "signed in", info.get("subscriptionType")
    return Check(name, "ok", f"{version}; signed in via {method}" + (f" ({plan} plan)" if plan else ""))


def check_codex(command: str) -> Check:
    name = "Codex CLI"
    if not find_command(command):
        return Check(name, "warn", f"`{command}` not found — install Codex to use Codex models")
    version = _version(command)
    _, out = run_command([command, "login", "status"])
    low = out.lower()
    if "logged in" in low and "not logged in" not in low:
        return Check(name, "ok", f"{version}; {out.splitlines()[-1].strip()}")
    return Check(name, "warn", f"{version}; not signed in — run `codex login`")


def self_test() -> Check:
    """Run two requests end to end on simulated models in a throwaway folder."""
    from .adapters.fake import FakeAdapter
    from .runtime import Runtime

    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            rt = Runtime(Path(tmp), config=Config({"runtime": {"fsync": False, "watchdog_interval_s": 0.05}}),
                         adapters={p: FakeAdapter(p) for p in ("claude", "codex")}, demo=True)
            await rt.start()
            try:
                easy = await rt.submit("2x2")
                await rt.wait(easy.id, timeout=20)
                wide = await rt.submit("Compare apples and oranges")
                await rt.wait(wide.id, timeout=20)
                return easy, wide
            finally:
                await rt.stop()

    try:
        easy, wide = asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        return Check("Runtime self-test", "fail", f"{type(exc).__name__}: {exc}")
    if easy.answer == "4" and easy.answered_by.startswith("filter") and wide.status == "done":
        return Check("Runtime self-test", "ok",
                     "2x2 answered by the filter; a 2-part task planned, run in parallel, and merged "
                     "(simulated models)")
    return Check("Runtime self-test", "fail", f"unexpected results: {easy.status}/{easy.answer!r}, {wide.status}")


async def live_calls(config: Config) -> list[Check]:
    """One tiny real call per installed provider (uses a little quota)."""
    from .adapters.base import Invocation
    from .probe import PROBE_SCHEMA
    from .runtime import LIVE_DATA_DIR, build_live_adapters
    from .swarm import SwarmGovernor

    adapters = build_live_adapters(config, LIVE_DATA_DIR)

    async def one(provider: str, adapter) -> Check:
        binding = next((b for b in config.tier_bindings("efficiency") if b.provider == provider), None)
        result = await adapter.invoke(Invocation(
            run_id="doctor", label="doctor", role="efficiency",
            system_prompt="You are a connectivity check. Reply with JSON only.",
            prompt='Return {"ok": true, "echo": "glass"} exactly.', schema=PROBE_SCHEMA,
            model=binding.model if binding else None, effort=binding.effort if binding else None,
            timeout_s=180, max_budget_usd=0.5, extra_args=SwarmGovernor.provider_settings(provider, 0)[1]))
        name = f"Live call · {provider}"
        if result.ok and (result.output or {}).get("echo") == "glass":
            return Check(name, "ok", f"answered correctly in {result.duration_s:.1f}s")
        if result.retry_after:
            until = time.strftime("%H:%M", time.localtime(result.retry_after))
            return Check(name, "warn", f"out of quota until {until} — work will go to the other provider")
        return Check(name, "warn", f"failed: {result.detail[:200] or result.failure}")

    return list(await asyncio.gather(*(one(p, a) for p, a in adapters.items())))


def collect_checks(*, demo: bool, live: bool = False, data_dir: Path | None = None) -> list[Check]:
    from .roles import SKILL_FILES, SKILLS_DIR
    from .runtime import data_dir_for

    checks: list[Check] = []
    v = sys.version_info
    checks.append(Check("Python", "ok" if v >= (3, 11) else "fail",
                        f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 11) else " — Glass Membrane needs 3.11 or newer")))

    missing = [f for f in ("index.html", "app.js", "styles.css") if not (REPO_ROOT / "silicate" / f).is_file()]
    checks.append(Check("Silicate UI files", "fail" if missing else "ok",
                        f"missing: {', '.join(missing)}" if missing else "present"))
    missing_skills = sorted({f for f in SKILL_FILES.values() if not (SKILLS_DIR / f).is_file()})
    checks.append(Check("Role instructions", "warn" if missing_skills else "ok",
                        f"missing (a generic fallback is used): {', '.join(missing_skills)}" if missing_skills
                        else f"{len(set(SKILL_FILES.values()))} role files in runtime/skills"))

    try:
        config = Config.load()
        ceilings = ", ".join(f"{p} {config.provider_ceiling(p)}" for p in config.providers)
        checks.append(Check("Configuration", "ok", f"runtime/config/models.toml read; process ceilings: {ceilings}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Configuration", "fail", f"runtime/config/models.toml: {exc}"))
        config = Config()

    folder = Path(data_dir) if data_dir else data_dir_for(demo)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("Data folder", "ok", f"{folder} is writable"))
    except OSError as exc:
        checks.append(Check("Data folder", "fail", f"{folder}: {exc}"))

    host_file = folder / "host.json"
    if host_file.exists():
        from .cli import find_host

        client = find_host(demo)
        if client:
            checks.append(Check("Silicate host", "ok", f"already running at {client.base}/ — the launcher will open it"))

    if demo:
        checks.append(Check("Models", "ok", "demo mode uses simulated models (no provider usage)"))
    else:
        providers = [check_claude(config.providers.get("claude", {}).get("command", "claude")),
                     check_codex(config.providers.get("codex", {}).get("command", "codex"))]
        checks += providers
        if not any(c.status == "ok" for c in providers):
            checks.append(Check("Providers", "fail",
                                "no signed-in provider CLI — sign in to Claude Code or Codex, or start with --demo"))
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            if os.environ.get(var):
                checks.append(Check("API key in environment", "ok",
                                    f"{var} is set but ignored: calls use your subscription logins"))

    checks.append(self_test())
    if live and not demo:
        checks += asyncio.run(live_calls(config))
    return checks


def run_doctor(*, demo: bool, live: bool = False, data_dir: Path | None = None) -> int:
    print(f"Glass Membrane check ({'demo' if demo else 'live'} mode)"
          + (" — includes one real call per provider" if live and not demo else " — uses no provider quota"))
    checks = collect_checks(demo=demo, live=live, data_dir=data_dir)
    for check in checks:
        print(f"  {MARK[check.status]} {check.name}: {check.detail}")
    fails = sum(c.status == "fail" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    if fails:
        print(f"\nNot ready: {fails} problem(s) must be fixed first.")
        return 1
    print("\nReady." + (f" {warns} warning(s) — see above." if warns else ""))
    return 0
