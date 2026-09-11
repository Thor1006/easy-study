"""Deterministic simulated adapter for tests and demo mode.

It never contacts a provider. Its "brain" returns plausible structured output
for each role so the whole runtime (and Silicate) can be exercised for free.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Callable

from ..services import safe_calc
from .base import Invocation, ModelAdapter, ModelResult, Usage

SORTING_NOTES = {
    "insertion sort": "Insertion sort runs in near-linear time on nearly-sorted input (O(n + inversions)) "
                      "with tiny constant factors, so it shines on small or almost-ordered arrays.",
    "timsort": "Timsort detects existing ascending runs and merges them, so nearly-sorted data approaches "
               "O(n) while keeping an O(n log n) worst case; it is the default sort in Python and Java.",
    "quicksort": "Quicksort gains little from presortedness; naive pivot choices degrade to O(n²) on sorted "
                 "input, so it needs median-of-three or randomized pivots.",
}

_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_STEER_OPENERS = ("actually", "instead", "also", "make it", "change", "only", "stop", "cancel",
                  "focus", "don't", "do not", "answer in", "keep it", "use ")


def extract_items(text: str) -> list[str]:
    low = text.lower()
    if "sort" in low and re.search(r"\b(compare|3|three)\b", low):
        return ["Insertion sort", "Timsort", "Quicksort"]
    match = re.search(r"(?:compare|contrast)\s+(.+)", text, re.I)
    body = match.group(1) if match else (text if re.search(r"\bvs\.?\b|\bversus\b", low) else "")
    if not body:
        return []
    body = re.split(r"\b(?:for|in|on|when|with regard to)\b", body, maxsplit=1)[0]
    parts = [p.strip(" .?!") for p in re.split(r",|\band\b|\bvs\.?\b|\bversus\b", body) if p.strip(" .?!")]
    if len(parts) >= 2:
        return parts[:6]
    counted = re.match(r"(\d+|two|three|four|five|six)\s+(.+)", body.strip(), re.I)
    if counted:
        raw = counted.group(1).lower()
        count = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        noun = counted.group(2).strip().rstrip("s")
        return [f"{noun.capitalize()} option {i}" for i in range(1, min(count, 6) + 1)]
    return []


def _first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text.strip(), re.S)
    return match.group(1) if match else text.strip()


def _constraint_note(data: dict) -> str:
    constraints = data.get("constraints") or []
    return f" (Applied: {'; '.join(constraints)})" if constraints else ""


def _router(data: dict) -> dict:
    text = data.get("text", "")
    low = text.lower().strip()
    active = data.get("active_task")
    if data.get("kind_hint") == "steer" or (active and low.startswith(_STEER_OPENERS)):
        nodes = (active or {}).get("nodes", [])
        affected = [n["id"] for n in nodes if len(n.get("title", "")) >= 3 and n["title"].lower() in low]
        scope = "cancel" if low.rstrip(".!") in ("stop", "cancel") else ("nodes" if affected else "global")
        return {"mode": "steer", "answer_text": None, "answer_confidence": 0.0, "routing_confidence": 0.9,
                "kind": "steer", "packet": None,
                "steer": {"scope": scope, "affected_nodes": affected, "constraint": text}}
    value = safe_calc(text)
    if value is not None:
        return {"mode": "answer", "answer_text": str(value), "answer_confidence": 0.98,
                "routing_confidence": 0.95, "kind": "new_task", "packet": None, "steer": None}
    items = extract_items(text)
    hard = any(w in low for w in ("prove", "derive", "design", "architecture", "why "))
    difficulty = "high" if hard else ("low" if len(text.split()) < 6 else "medium")
    ambiguous = "ambiguous" in low and data.get("tier") == "efficiency"
    packet = {
        "task": text[:200], "domain": "sorting algorithms" if "sort" in low else "general",
        "difficulty": difficulty, "uncertainty": "high" if ambiguous else "medium",
        "parallelizable": len(items) >= 2, "parallel_items": items, "needs_retrieval": False,
        "needs_compute": False, "freshness_needed": False, "modalities": ["text"],
        "verification": "light", "recommended_tier": "flagship" if hard else "performance",
        "recommended_effort": "high" if hard else "medium",
    }
    return {"mode": "route", "answer_text": None, "answer_confidence": 0.05,
            "routing_confidence": 0.4 if ambiguous else 0.88, "kind": "new_task",
            "packet": packet, "steer": None}


def _planner(data: dict) -> dict:
    goal = data.get("goal", "")
    packet = data.get("packet") or {}
    items = packet.get("parallel_items") or extract_items(goal) or [goal[:60]]
    tier = "efficiency" if packet.get("difficulty") == "low" else "performance"
    nodes = [{"id": f"part-{i}", "title": item, "instructions": f"Analyse {item} for: {goal}",
              "role": tier, "tier": tier, "deps": [], "swarm_children": 0}
             for i, item in enumerate(items[:6], start=1)]
    if len(nodes) > 1:
        nodes.append({"id": "synthesize", "title": "Synthesize findings",
                      "instructions": "Combine the accepted part results into one recommendation.",
                      "role": "synthesizer", "tier": "flagship", "deps": [n["id"] for n in nodes],
                      "swarm_children": 0})
    return {"rationale": f"{len(items)} independent part(s), then synthesis.", "nodes": nodes}


def _worker(inv: Invocation) -> dict:
    data = inv.data
    title = data.get("title", "the task")
    goal = data.get("goal", "")
    instructions = data.get("instructions") or ""
    if "[escalate]" in instructions and data.get("tier") != "flagship":
        return {"answer": "", "summary": f"Attempted {title} at {data.get('tier')} tier; derivations disagree.",
                "confidence": 0.3, "uncertainty": "unresolved conflicting derivations",
                "status": "CAPABILITY_LIMIT",
                "shift": {"type": "ESCALATION_REQUEST", "reason": "Two derivations disagree under the same assumptions.",
                          "attempted": "A direct derivation and a check by example.",
                          "requested_capability": "Stronger mathematical reasoning",
                          "expected_benefit": "Resolve the disputed step needed for acceptance.",
                          "checkpoint_next_action": "Re-derive the disputed step.",
                          "subtask_title": "", "swarm_children": 0}}
    if "[swarm]" in instructions and not inv.swarm_grant:
        return {"answer": "", "summary": "Bulk sub-work would benefit from parallel children.",
                "confidence": 0.5, "uncertainty": None, "status": "COMPLETE",
                "shift": {"type": "SWARM_REQUEST", "reason": f"Scan the sources for {title} in parallel",
                          "attempted": "Sequential scan of the first source.", "requested_capability": "",
                          "expected_benefit": "Cover all sources within the time budget.",
                          "checkpoint_next_action": "Merge child findings.", "subtask_title": "",
                          "swarm_children": 2}}
    note = next((v for k, v in SORTING_NOTES.items() if k in title.lower()), None)
    answer = note or f"{title}: the key considerations, trade-offs, and a recommendation for “{goal}”."
    if data.get("partition"):
        answer = f"[part {data['partition']}] {answer}"
    if data.get("checkpoint"):
        answer += " (Continued from a checkpoint after re-binding.)"
    if any(word in goal.lower() for word in ("illustrate", "diagram", "chart")):
        visual = {"type": "flow", "title": "From question to answer",
                  "steps": ["Understand your question", "Gather relevant context", "Reason and check", "Explain the answer"],
                  "caption": "Simulated example: no provider was contacted."}
        answer += "\n\n```silicate\n" + json.dumps(visual) + "\n```"
    answer += _constraint_note(data)
    return {"answer": answer, "summary": _first_sentence(answer), "confidence": 0.8,
            "uncertainty": None, "status": "COMPLETE", "shift": None}


def _synthesizer(data: dict) -> dict:
    deps = data.get("deps") or []
    lines = [f"- {d.get('title')}: {_first_sentence(d.get('answer') or d.get('summary') or '')}" for d in deps]
    if "sort" in data.get("goal", "").lower():
        verdict = ("Recommendation: for nearly-sorted data prefer Timsort (or insertion sort for small arrays); "
                   "avoid quicksort with naive pivots.")
    else:
        verdict = "Recommendation: weigh the trade-offs above against your constraints."
    answer = "Across the parts:\n" + "\n".join(lines) + "\n" + verdict + _constraint_note(data)
    return {"answer": answer, "summary": verdict, "confidence": 0.8, "uncertainty": None,
            "status": "COMPLETE", "shift": None}


def simulated_brain(inv: Invocation) -> dict:
    if inv.role == "router":
        return _router(inv.data)
    if inv.role == "planner":
        return _planner(inv.data)
    if inv.role == "synthesizer":
        return _synthesizer(inv.data)
    return _worker(inv)


def demo_delays() -> dict[str, Callable[[Invocation], float]]:
    """Human-visible, deterministic delays so Silicate shows work moving."""

    def jitter(base: float, spread: float):
        def delay(inv: Invocation) -> float:
            key = f"{inv.label}{inv.data.get('node', '')}{inv.data.get('attempt', 0)}".encode()
            return base + (int(hashlib.sha1(key).hexdigest(), 16) % 1000) / 1000 * spread
        return delay

    return {"router": jitter(0.5, 0.5), "planner": jitter(1.2, 0.6), "synthesizer": jitter(1.4, 0.6),
            "efficiency": jitter(1.5, 1.5), "performance": jitter(2.0, 2.0), "flagship": jitter(2.0, 1.5),
            "retrieval": jitter(1.5, 1.0), "verifier": jitter(1.5, 1.0)}


class FakeAdapter(ModelAdapter):
    provenance = "simulated (no provider calls)"

    def __init__(self, provider: str = "claude", *, brain: Callable | None = None,
                 delays: dict | None = None, default_delay: float = 0.02) -> None:
        self.provider = provider
        self.brain = brain or simulated_brain
        self.delays = dict(delays or {})
        self.default_delay = default_delay
        self.capabilities = {"structured_output": "simulated", "swarm": "simulated",
                             "usage": "simulated", "cancellation": "simulated"}
        self.calls: list[Invocation] = []
        self.live = 0
        self.peak = 0
        self.overspawn = 0
        self.cancelled = 0

    def _delay(self, inv: Invocation) -> float:
        for key in (inv.label, inv.role):
            if key in self.delays:
                value = self.delays[key]
                return value(inv) if callable(value) else value
        return self.default_delay

    async def invoke(self, inv: Invocation) -> ModelResult:
        self.calls.append(inv)
        self.live += 1
        self.peak = max(self.peak, self.live)
        started = time.monotonic()
        try:
            await asyncio.sleep(self._delay(inv))
            produced = self.brain(inv)
            if isinstance(produced, ModelResult):
                produced.duration_s = time.monotonic() - started
                return produced
            text = json.dumps(produced, ensure_ascii=False)
            usage = Usage(input_tokens=(len(inv.prompt) + len(inv.system_prompt)) // 4,
                          output_tokens=max(1, len(text) // 4), cost_usd=0.0)
            children = inv.swarm_grant + self.overspawn if inv.swarm_grant else 0
            return ModelResult(ok=True, output=produced, raw_text=text, usage=usage,
                               children_spawned=children, duration_s=time.monotonic() - started)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.live -= 1
