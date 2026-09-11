"""`gm probe`: measure how many parallel calls your subscriptions sustain.

This makes REAL provider calls and uses quota, so the CLI requires --confirm.
Each level launches N tiny structured-output calls at once per provider and
records success, rate limiting, latency, and (on Windows) approximate memory
per process. Results are printed and saved under runtime/logs/.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import subprocess
import time

from .adapters.base import Invocation
from .adapters.process import windows_tool
from .registry import Config
from .runtime import LIVE_DATA_DIR, build_live_adapters
from .swarm import SwarmGovernor

PROBE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok", "echo"],
                "properties": {"ok": {"type": "boolean"}, "echo": {"type": "string"}}}
IMAGES = {"claude": "claude.exe", "codex": "codex.exe"}


def _memory_mb(image: str) -> float | None:
    if os.name != "nt":
        return None
    try:
        out = subprocess.run([windows_tool("tasklist.exe"), "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image}"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    total_kb = 0
    for row in csv.reader(io.StringIO(out)):
        if len(row) >= 5 and row[0].lower() == image.lower():
            total_kb += int("".join(ch for ch in row[4] if ch.isdigit()) or 0)
    return total_kb / 1024


async def _sample_memory(image: str, stop: asyncio.Event, baseline: float, peak: list[float]) -> None:
    while not stop.is_set():
        value = await asyncio.to_thread(_memory_mb, image)
        if value is not None:
            peak[0] = max(peak[0], value - baseline)
        try:
            await asyncio.wait_for(stop.wait(), 0.5)
        except asyncio.TimeoutError:
            pass


async def run_probe(providers: list[str], levels: list[int]) -> dict:
    # Adapters only: a live Silicate host may be running, and it owns the runtime journal.
    config = Config.load()
    adapters = build_live_adapters(config, LIVE_DATA_DIR)
    report = {"started_at": time.time(), "results": []}
    for provider in providers:
        adapter = adapters.get(provider)
        if adapter is None:
            print(f"{provider}: CLI not found — skipped")
            continue
        binding = next((b for b in config.tier_bindings("efficiency") if b.provider == provider), None)
        ceiling = config.provider_ceiling(provider)
        print(f"\n{provider} (efficiency binding: {binding.label() if binding else provider}; ceiling {ceiling})")
        for level in levels:
            if level > ceiling:
                print(f"  level {level}: above the configured ceiling ({ceiling}) — skipped")
                continue
            baseline = await asyncio.to_thread(_memory_mb, IMAGES.get(provider, "")) or 0.0
            stop, peak = asyncio.Event(), [0.0]
            sampler = asyncio.create_task(_sample_memory(IMAGES.get(provider, ""), stop, baseline, peak))
            invocations = [Invocation(
                run_id="probe", label=f"probe-{i}", role="efficiency",
                system_prompt="You are a connectivity probe. Reply with JSON only.",
                prompt=f'Return {{"ok": true, "echo": "{i}"}} exactly.', schema=PROBE_SCHEMA,
                model=binding.model if binding else None, effort=binding.effort if binding else None,
                timeout_s=180, max_budget_usd=0.5,
                extra_args=SwarmGovernor.provider_settings(provider, 0)[1]) for i in range(level)]
            started = time.monotonic()
            results = await asyncio.gather(*(adapter.invoke(inv) for inv in invocations))
            elapsed = time.monotonic() - started
            stop.set()
            await sampler
            ok = sum(1 for r in results if r.ok)
            limited = sum(1 for r in results if r.rate_limited)
            latencies = sorted(r.duration_s for r in results)
            row = {"provider": provider, "level": level, "ok": ok, "rate_limited": limited,
                   "failed": level - ok, "wall_s": round(elapsed, 2),
                   "p50_s": round(latencies[len(latencies) // 2], 2),
                   "max_s": round(latencies[-1], 2),
                   "approx_mb_per_process": round(peak[0] / level, 1) if peak[0] else None,
                   "errors": sorted({r.detail[:160] for r in results if not r.ok})[:3],
                   "usage_known": all(r.usage.known for r in results if r.ok)}
            report["results"].append(row)
            memory = f"{row['approx_mb_per_process']} MB/process" if row["approx_mb_per_process"] else "memory n/a"
            print(f"  level {level:>2}: {ok}/{level} ok, {limited} rate-limited, wall {row['wall_s']}s, "
                  f"p50 {row['p50_s']}s, {memory}")
            for error in row["errors"]:
                print(f"            error: {error}")
            if limited:
                print("            rate limiting reached — stopping this provider's ramp")
                break
    logs = LIVE_DATA_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved {path}")
    return report
