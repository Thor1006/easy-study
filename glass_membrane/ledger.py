"""Token Ledger (Arch §14 TL).

One entry per provider call. Unknown usage stays unknown: totals count
unknown-usage calls separately instead of treating them as zero. Native
children are accounted once, inside their parent's call entry (Claude's
`modelUsage` already includes subagents), never as extra rows.
"""

from __future__ import annotations

import threading
import time


def _summarize(rows: list[dict]) -> dict:
    known = [r for r in rows if r["known"]]
    summary = {"calls": len(rows), "unknown_usage_calls": len(rows) - len(known)}
    for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
        summary[key] = sum(r[key] or 0 for r in known)
    costs = [r["cost_usd"] for r in known if r["cost_usd"] is not None]
    summary["cost_usd_estimate"] = round(sum(costs), 6) if costs else None
    children = [r["children"] for r in rows]
    summary["children"] = None if any(c is None for c in children) else sum(children)
    return summary


class Ledger:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self._lock = threading.Lock()

    def record(self, *, run_id, provider, model, label, kind, usage, children, duration_s,
               ok, failure=None, node=None) -> dict:
        entry = {"ts": time.time(), "run": run_id, "provider": provider, "model": model,
                 "label": label, "kind": kind, "node": node, "ok": ok, "failure": failure,
                 "duration_s": round(duration_s, 3), "children": children, **usage.to_dict()}
        with self._lock:
            self.entries.append(entry)
        return entry

    def for_run(self, run_id: str) -> list[dict]:
        with self._lock:
            return [e for e in self.entries if e["run"] == run_id]

    def totals(self, run_id: str | None = None) -> dict:
        with self._lock:
            rows = [e for e in self.entries if run_id is None or e["run"] == run_id]
        total = _summarize(rows)
        total["by_provider"] = {p: _summarize([r for r in rows if r["provider"] == p])
                                for p in sorted({r["provider"] for r in rows})}
        total["by_kind"] = {k: _summarize([r for r in rows if r["kind"] == k])
                            for k in sorted({r["kind"] for r in rows})}
        return total
