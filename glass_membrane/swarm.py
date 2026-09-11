"""Swarm Governor (Arch §7, Ops §9, Schematic §11).

Cores may *request* provider-native children; only the governor grants them.
Idle logical cores are preferred; grants are bounded and non-recursive; and
the grant is enforced through provider settings rather than prompt compliance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class SwarmDecision:
    action: str      # grant | reduce | redirect | deny
    children: int
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class SwarmGovernor:
    def __init__(self, rt) -> None:
        self.rt = rt
        cfg = rt.config.swarm
        self.max_total = int(cfg["max_children_total"])
        self.max_per_core = int(cfg["max_children_per_core"])
        self.prefer_idle = bool(cfg["prefer_idle_cores"])
        self.live_children = 0
        self.granted_total = 0
        self.decisions: list[dict] = []

    def _decide(self, action: str, children: int, reason: str, node_id: str) -> SwarmDecision:
        decision = SwarmDecision(action, children, reason)
        self.decisions.append({"node": node_id, **decision.to_dict()})
        del self.decisions[:-50]
        return decision

    def consider(self, run, node: dict, requested: int, purpose: str, provider: str) -> SwarmDecision:
        nid = node["id"]
        if node["role"] == "swarm-child":
            return self._decide("deny", 0, "recursive expansion needs a new runtime grant", nid)
        if not (purpose or "").strip():
            return self._decide("deny", 0, "a bounded purpose is required", nid)
        if requested <= 0:
            return self._decide("deny", 0, "no children requested", nid)
        if not self.rt.permissions.allows(node["role"], "request_swarm"):
            return self._decide("deny", 0, f"role {node['role']} may not request children", nid)
        if not self.rt.registry.supports_swarm(provider):
            return self._decide("deny", 0, f"{provider} native children are unsupported or unknown", nid)
        if self.prefer_idle and requested >= 2 and self.rt.slots.idle_core_count() >= requested:
            return self._decide("redirect", requested, "idle logical cores are preferred over native children", nid)
        room_in_run = run.max_agents - run.live_agents - 1
        allowance = min(requested, self.max_per_core, self.max_total - self.live_children, room_in_run)
        if allowance <= 0:
            return self._decide("deny", 0, "no child capacity available", nid)
        action = "grant" if allowance == requested else "reduce"
        return self._decide(action, allowance, f"{allowance} of {requested} children, depth 1", nid)

    def acquire(self, n: int) -> None:
        self.live_children += n
        self.granted_total += n

    def release(self, n: int) -> None:
        self.live_children = max(0, self.live_children - n)

    @staticmethod
    def provider_settings(provider: str, n: int) -> tuple[dict[str, str], list[str]]:
        """Translate a grant into settings the provider CLI enforces itself."""
        if provider == "claude":
            if n <= 0:
                return {}, []  # the adapter removes the Agent tool entirely
            return {"CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": str(n),
                    "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"}, []
        if provider == "codex":
            if n <= 0:
                return {}, ["--disable", "multi_agent"]
            return {}, ["-c", f"agents.max_concurrent_threads_per_session={n}"]
        return {}, []

    def snapshot(self) -> dict:
        return {"live_children": self.live_children, "max_total": self.max_total,
                "max_per_core": self.max_per_core, "granted_total": self.granted_total,
                "recent": self.decisions[-12:]}
