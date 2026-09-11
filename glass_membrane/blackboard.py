"""Task Blackboard: structured, read-only views of the task graph (Arch §8).

Cores read views and submit proposals; only the State Manager mutates
canonical state.
"""

from __future__ import annotations

import copy

# Node execution status. "ready" is derived: a waiting node whose deps are done.
WAITING = "waiting"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

TIERS = ("efficiency", "performance", "flagship")

ROLES = (
    "router", "planner", "efficiency", "performance", "flagship", "retrieval",
    "context-prep", "verifier", "swarm-child", "synthesizer", "output-assembler", "service",
)


class ResultValidity:
    """Validity of a node's result relative to current state (Arch §11)."""
    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"
    NEEDS_RECHECK = "NEEDS_RECHECK"
    CONTESTED = "CONTESTED"


class InfoStatus:
    """Processing/validity status of information objects (Arch §10, Schematic §8)."""
    RAW = "RAW"
    EXTRACTED = "EXTRACTED"
    VERIFIED = "VERIFIED"
    CONTESTED = "CONTESTED"
    STALE = "STALE"
    INVALID = "INVALID"


def normalize_node(spec: dict, contract_version: int) -> dict:
    """Canonical node record built from a (validated) node proposal."""
    return {
        "id": spec["id"],
        "title": spec.get("title") or spec["id"],
        "role": spec.get("role", "performance"),
        "tier": spec.get("tier", "performance"),
        "instructions": spec.get("instructions", ""),
        "deps": list(spec.get("deps", [])),
        "service": spec.get("service"),
        "swarm_request": spec.get("swarm_request"),
        "partition": spec.get("partition"),
        "checkpoint_ref": spec.get("checkpoint_ref"),
        "status": WAITING,
        "contract_version": contract_version,
        "owner": None,
        "lease_id": None,
        "binding": None,
        "result_ref": None,
        "validity": None,
        "attempt": 0,
        "escalations": 0,
        "rejections": [],
        "superseded_results": [],
        "history": [],
    }


class Blackboard:
    def __init__(self, state) -> None:
        self._state = state

    def _task(self, task_id: str) -> dict:
        task = self._state.task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def ready_nodes(self, task_id: str) -> list[dict]:
        nodes = self._task(task_id)["nodes"]
        return [
            n for n in nodes.values()
            if n["status"] == WAITING and all(nodes[d]["status"] == DONE for d in n["deps"])
        ]

    def dependents(self, task_id: str, node_id: str) -> set[str]:
        """All nodes that transitively depend on node_id."""
        nodes = self._task(task_id)["nodes"]
        found: set[str] = set()
        frontier = [node_id]
        while frontier:
            current = frontier.pop()
            for n in nodes.values():
                if current in n["deps"] and n["id"] not in found:
                    found.add(n["id"])
                    frontier.append(n["id"])
        return found

    def is_complete(self, task_id: str) -> bool:
        nodes = self._task(task_id)["nodes"].values()
        return bool(nodes) and all(n["status"] in (DONE, CANCELLED) for n in nodes)

    def failed_nodes(self, task_id: str) -> list[dict]:
        return [n for n in self._task(task_id)["nodes"].values() if n["status"] == FAILED]

    def phase(self, task_id: str, node: dict) -> str:
        """Display phase: ready / waiting / running / done / failed / cancelled."""
        if node["status"] == WAITING:
            nodes = self._task(task_id)["nodes"]
            if all(nodes[d]["status"] == DONE for d in node["deps"]):
                return "ready"
        return node["status"]

    def view(self, task_id: str) -> dict:
        task = copy.deepcopy(self._task(task_id))
        for node in task["nodes"].values():
            node["phase"] = self.phase(task_id, node)
        return task
