"""Eight-slot filter/router front end (Arch §4, Ops §7).

R0–R3 efficiency, R4–R5 performance (intermediate), R6–R7 flagship (strong).
For one input the cascade is sequential: the cheapest suitable slot first,
escalating only when its answer or routing confidence is below threshold.
The filter may answer easy requests directly; its packet is advice to the
scheduler, never an allocation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .adapters.base import Invocation
from .blackboard import TIERS
from .roles import ROUTER_SCHEMA, load_skill, render_router_prompt

_PACKET_DEFAULTS = {
    "domain": "general", "difficulty": "medium", "uncertainty": "medium", "parallelizable": False,
    "parallel_items": [], "needs_retrieval": False, "needs_compute": False, "freshness_needed": False,
    "modalities": ["text"], "verification": "light", "recommended_tier": "performance",
    "recommended_effort": "medium",
}


def default_packet(text: str) -> dict:
    return {"task": text[:200], **_PACKET_DEFAULTS}


def sanitize_packet(packet: dict | None, text: str) -> dict:
    clean = default_packet(text)
    for key, value in (packet or {}).items():
        if key in clean and value is not None:
            clean[key] = value
    if clean["recommended_tier"] not in TIERS:
        clean["recommended_tier"] = "performance"
    clean["parallel_items"] = [str(i)[:120] for i in (clean["parallel_items"] or [])][:8]
    clean["parallelizable"] = bool(clean["parallelizable"])
    return clean


def _conf(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


@dataclass
class RouteDecision:
    mode: str                       # answer | route | steer
    answer_text: str | None = None
    answer_confidence: float = 0.0
    routing_confidence: float = 0.0
    tier: str | None = None
    slot: str | None = None
    provider: str | None = None
    packet: dict | None = None
    steer: dict | None = None
    attempts: list = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Router:
    def __init__(self, rt) -> None:
        self.rt = rt

    async def _take_slot(self, tier: str, run_id: str):
        while True:
            slot = self.rt.slots.idle_filter(tier)
            if slot is not None:
                return self.rt.slots.occupy(slot.id, run_id=run_id)
            since = self.rt.signal.version
            await self.rt.signal.wait(timeout=1.0, since=since)

    async def _take_process(self, tier: str):
        bindings = self.rt.registry.bindings(tier)
        while True:
            for binding in bindings:
                if self.rt.pools.try_acquire(binding.provider, "control"):
                    return binding
            since = self.rt.signal.version
            await self.rt.signal.wait(timeout=1.0, since=since)

    async def filter(self, run, inp, *, active_task: dict | None = None, allow_answer: bool = True,
                     kind_hint: str = "auto") -> RouteDecision:
        rt = self.rt
        answer_threshold = float(rt.config.filter["answer_threshold"])
        routing_threshold = float(rt.config.filter["routing_threshold"])
        attempts: list[dict] = []
        last_packet = None

        for tier in TIERS:
            final = tier == TIERS[-1]
            slot = await self._take_slot(tier, run.id)
            binding = None
            try:
                binding = await self._take_process(tier)
                rt.slots.update(slot.id, binding=binding.to_dict())
                run.live_processes += 1
                run.peak_processes = max(run.peak_processes, run.live_processes)
                rt.budget.charge(run)
                rt.emit("filter_started", run=run.id, message=f"{slot.id} ({tier}) on {binding.label()}",
                        status=f"Filter {slot.id} ({tier}) is reading your request",
                        data={"slot": slot.id, "tier": tier, "binding": binding.to_dict()})
                inv = Invocation(
                    run_id=run.id, label=slot.id, role="router", system_prompt=load_skill("router"),
                    prompt=render_router_prompt(slot=slot.id, tier=tier, text=inp.text,
                                                active_task=active_task, kind_hint=kind_hint),
                    schema=ROUTER_SCHEMA, model=binding.model, effort=binding.effort,
                    timeout_s=float(rt.config.runtime["call_timeout_s"]),
                    max_budget_usd=rt.config.budget.get("max_usd_per_call"),
                    data={"text": inp.text, "tier": tier, "slot": slot.id, "active_task": active_task,
                          "kind_hint": kind_hint})
                result = await rt.adapters[binding.provider].invoke(inv)
            finally:
                if binding is not None:
                    rt.pools.release(binding.provider)
                    run.live_processes -= 1
                rt.slots.free(slot.id)
                rt.signal.notify()

            rt.ledger.record(run_id=run.id, provider=binding.provider, model=binding.model, label=slot.id,
                             kind="filter", usage=result.usage, children=result.children_spawned,
                             duration_s=result.duration_s, ok=result.ok, failure=result.failure)
            if result.rate_limited:
                rt.pools.on_rate_limit(binding.provider)
            out = result.output if result.ok and isinstance(result.output, dict) else None
            attempt = {"tier": tier, "slot": slot.id, "provider": binding.provider, "ok": bool(out),
                       "mode": out.get("mode") if out else None,
                       "answer_confidence": _conf(out.get("answer_confidence")) if out else None,
                       "routing_confidence": _conf(out.get("routing_confidence")) if out else None,
                       "failure": result.failure}
            attempts.append(attempt)
            rt.emit("filter_result", run=run.id, message=f"{slot.id}: {attempt['mode'] or result.failure}",
                    data=attempt)
            if out is None:
                continue
            if result.ok:
                rt.pools.on_success(binding.provider)

            mode = out.get("mode")
            answer_conf = _conf(out.get("answer_confidence"))
            routing_conf = _conf(out.get("routing_confidence"))
            common = dict(answer_confidence=answer_conf, routing_confidence=routing_conf, tier=tier,
                          slot=slot.id, provider=binding.provider, attempts=attempts)
            if out.get("packet"):
                last_packet = out["packet"]

            if kind_hint == "steer" or (mode == "steer" and active_task is not None):
                if mode == "steer" and (routing_conf >= routing_threshold or final):
                    steer = out.get("steer") or {"scope": "global", "affected_nodes": [], "constraint": inp.text}
                    return RouteDecision("steer", steer=steer, **common)
                if kind_hint == "steer":
                    continue
            answer = (out.get("answer_text") or "").strip()
            if mode == "answer" and allow_answer and answer and answer_conf >= answer_threshold:
                return RouteDecision("answer", answer_text=answer, **common)
            if mode == "route" and out.get("packet") and (routing_conf >= routing_threshold or final):
                return RouteDecision("route", packet=sanitize_packet(out["packet"], inp.text), **common)

        if kind_hint == "steer":
            return RouteDecision("steer", steer={"scope": "global", "affected_nodes": [], "constraint": inp.text},
                                 attempts=attempts, note="fallback: filter gave no confident steer scope")
        return RouteDecision("route", packet=sanitize_packet(last_packet, inp.text), attempts=attempts,
                             note="fallback: filter cascade gave no confident decision")
