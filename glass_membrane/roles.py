"""Role instructions, output schemas, and prompt rendering.

Role instructions live in runtime/skills/*.md (Schematic §14), derived from
Ops §7. Schemas are strict (every property required, no extras) so the same
schema works for Claude `--json-schema` and Codex `--output-schema`.
"""

from __future__ import annotations

import json
from functools import lru_cache

from .registry import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "runtime" / "skills"

SKILL_FILES = {
    "router": "router.md", "planner": "planner.md", "efficiency": "efficiency.md",
    "performance": "performance.md", "flagship": "flagship.md", "retrieval": "retrieval.md",
    "context-prep": "context-prep.md", "verifier": "verifier.md", "swarm-child": "swarm-child.md",
    "synthesizer": "flagship.md", "output-assembler": "output-assembler.md",
}

FALLBACK_SKILL = (
    "You are one component of the Glass Membrane runtime. Models request; the runtime allocates. "
    "Stay within your focus contract, report uncertainty honestly, and return one JSON object "
    "matching the output schema."
)


@lru_cache(maxsize=None)
def load_skill(role: str) -> str:
    path = SKILLS_DIR / SKILL_FILES.get(role, "performance.md")
    return path.read_text(encoding="utf-8") if path.exists() else FALLBACK_SKILL


def _nullable(schema: dict) -> dict:
    return {"anyOf": [{"type": "null"}, schema]}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


PACKET_SCHEMA = _obj({
    "task": {"type": "string"},
    "domain": {"type": "string"},
    "difficulty": {"type": "string", "enum": ["low", "medium", "high"]},
    "uncertainty": {"type": "string", "enum": ["low", "medium", "high"]},
    "parallelizable": {"type": "boolean"},
    "parallel_items": {"type": "array", "items": {"type": "string"}},
    "needs_retrieval": {"type": "boolean"},
    "needs_compute": {"type": "boolean"},
    "freshness_needed": {"type": "boolean"},
    "modalities": {"type": "array", "items": {"type": "string"}},
    "verification": {"type": "string", "enum": ["none", "light", "independent"]},
    "recommended_tier": {"type": "string", "enum": ["efficiency", "performance", "flagship"]},
    "recommended_effort": {"type": "string", "enum": ["low", "medium", "high"]},
})

STEER_SCHEMA = _obj({
    "scope": {"type": "string", "enum": ["global", "nodes", "cancel"]},
    "affected_nodes": {"type": "array", "items": {"type": "string"}},
    "constraint": {"type": "string"},
})

ROUTER_SCHEMA = _obj({
    "mode": {"type": "string", "enum": ["answer", "route", "steer"]},
    "answer_text": {"type": ["string", "null"]},
    "answer_confidence": {"type": "number"},
    "routing_confidence": {"type": "number"},
    "kind": {"type": "string", "enum": ["new_task", "steer"]},
    "packet": _nullable(PACKET_SCHEMA),
    "steer": _nullable(STEER_SCHEMA),
})

SHIFT_SCHEMA = _obj({
    "type": {"type": "string", "enum": ["ESCALATION_REQUEST", "DOWNSHIFT_REQUEST", "SUBTASK_REQUEST",
                                        "SWARM_REQUEST", "RELEASE"]},
    "reason": {"type": "string"},
    "attempted": {"type": "string"},
    "requested_capability": {"type": "string"},
    "expected_benefit": {"type": "string"},
    "checkpoint_next_action": {"type": "string"},
    "subtask_title": {"type": "string"},
    "swarm_children": {"type": "integer"},
})

WORKER_SCHEMA = _obj({
    "answer": {"type": "string"},
    "summary": {"type": "string"},
    "confidence": {"type": "number"},
    "uncertainty": {"type": ["string", "null"]},
    "status": {"type": "string", "enum": ["COMPLETE", "BLOCKED", "INSUFFICIENT_EVIDENCE", "CONFLICT",
                                          "CAPABILITY_LIMIT"]},
    "shift": _nullable(SHIFT_SCHEMA),
})

PLANNER_SCHEMA = _obj({
    "rationale": {"type": "string"},
    "nodes": {"type": "array", "items": _obj({
        "id": {"type": "string"},
        "title": {"type": "string"},
        "instructions": {"type": "string"},
        "role": {"type": "string", "enum": ["retrieval", "efficiency", "performance", "flagship",
                                            "verifier", "synthesizer"]},
        "tier": {"type": "string", "enum": ["efficiency", "performance", "flagship"]},
        "deps": {"type": "array", "items": {"type": "string"}},
        "swarm_children": {"type": "integer"},
    })},
})


def schema_for(role: str) -> dict:
    if role == "router":
        return ROUTER_SCHEMA
    if role == "planner":
        return PLANNER_SCHEMA
    return WORKER_SCHEMA


def render_router_prompt(*, slot: str, tier: str, text: str, active_task: dict | None,
                         kind_hint: str) -> str:
    lines = [f"Front-end slot {slot} ({tier} tier).", "", "INPUT:", "<<<", text, ">>>"]
    if active_task:
        lines += ["", "ACTIVE TASK (this input may steer it instead of starting new work):",
                  f"goal: {active_task['goal']}", "nodes:"]
        lines += [f"  - {n['id']}: {n['title']} [{n['status']}]" for n in active_task["nodes"]]
    if kind_hint == "steer":
        lines += ["", "The operator sent this input as a STEER for the active task. Classify its scope."]
    lines += ["", "Return one JSON object matching the output schema."]
    return "\n".join(lines)


def render_core_prompt(contract, package: dict, role: str) -> str:
    parts = ["FOCUS CONTRACT", contract.render(), "",
             f"CONTEXT PACKAGE (state v{package['state_version']})", f"goal: {package['goal']}"]
    if package["constraints"]:
        parts.append("current constraints (apply every one):")
        parts += [f"  - {c}" for c in package["constraints"]]
    if package["dependencies"]:
        parts.append("accepted dependency results:")
        for dep in package["dependencies"]:
            tail = " [truncated; full text at the ref]" if dep["truncated"] else ""
            parts.append(f"  - {dep['title']} ({dep['ref']}):\n    {dep['answer']}{tail}")
    if package.get("checkpoint"):
        parts += ["continuation checkpoint from the previous binding:",
                  json.dumps(package["checkpoint"], ensure_ascii=False, indent=2)]
    parts += ["", "Return one JSON object matching the output schema."]
    return "\n".join(parts)
