"""Permission Manager (Schematic §3): deterministic role -> action grants."""

from __future__ import annotations

_WORKER = {"publish_result", "request_subtask", "request_shift", "request_swarm",
           "request_data", "request_verify"}

ROLE_ACTIONS: dict[str, set[str]] = {
    "router": {"answer", "route", "classify_steer"},
    "planner": {"publish_result", "propose_graph"},
    "efficiency": _WORKER,
    "performance": _WORKER,
    "flagship": _WORKER,
    "retrieval": _WORKER,
    "context-prep": {"publish_result"},
    "verifier": {"publish_result", "request_data"},
    "synthesizer": _WORKER - {"request_swarm"},
    "output-assembler": {"publish_result"},
    "service": {"publish_result"},
    "swarm-child": {"return_result"},  # children never publish canonical results directly
    "operator": {"submit", "steer", "cap", "cancel", "status", "replay"},
}


class Permissions:
    def allows(self, role: str, action: str) -> bool:
        return action in ROLE_ACTIONS.get(role, set())

    def actions(self, role: str) -> list[str]:
        return sorted(ROLE_ACTIONS.get(role, set()))

    def tools_for(self, role: str, swarm_grant: int) -> list[str]:
        """Provider tools a role may use. v1 cores get context in the prompt and need no tools."""
        return ["Agent"] if swarm_grant > 0 and self.allows(role, "request_swarm") else []
