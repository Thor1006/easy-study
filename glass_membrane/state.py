"""Canonical state and validated transactions (Arch §2, §12, §13).

Mutation sequence: read versioned state -> propose -> validate -> record a
durable journal transition -> commit the new version. Canonical state belongs
to the runtime, never to a model.
"""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .blackboard import DONE, RUNNING, ROLES, TIERS, WAITING, ResultValidity, normalize_node
from .journal import Journal


def empty_state() -> dict:
    return {"tasks": {}, "leases": {}, "last_seq": 0}


def apply_op(state: dict, op: dict) -> None:
    """Apply one journaled operation. Deterministic: timestamps travel inside ops."""
    kind = op["op"]
    tasks = state["tasks"]

    if kind == "create_task":
        tasks[op["task_id"]] = {
            "id": op["task_id"], "goal": op["goal"], "run_id": op.get("run_id"),
            "packet": op.get("packet"), "version": 1, "constraints": [], "status": "open",
            "nodes": {}, "created_at": op["ts"], "finished_at": None,
            "answer": None, "answered_by": None,
        }
        return
    if kind == "grant_lease":
        lease = dict(op["lease"])
        lease.setdefault("revoked", False)
        lease.setdefault("released", False)
        state["leases"][lease["id"]] = lease
        return
    if kind == "renew_lease":
        state["leases"][op["lease_id"]]["expires_at"] = op["expires_at"]
        return
    if kind == "revoke_lease":
        lease = state["leases"][op["lease_id"]]
        lease["revoked"] = True
        lease["revoked_reason"] = op.get("reason", "")
        return
    if kind == "release_lease":
        state["leases"][op["lease_id"]]["released"] = True
        return

    task = tasks[op["task_id"]]
    if kind == "add_nodes":
        for spec in op["nodes"]:
            task["nodes"][spec["id"]] = normalize_node(spec, task["version"])
    elif kind == "update_node":
        task["nodes"][op["node_id"]].update(op["fields"])
    elif kind == "node_history":
        task["nodes"][op["node_id"]]["history"].append(op["entry"])
    elif kind == "add_constraint":
        task["version"] += 1
        task["constraints"].append({
            "id": op["constraint_id"], "text": op["text"], "scope": op.get("scope", "global"),
            "added_in_version": task["version"], "ts": op["ts"],
        })
    elif kind == "reissue_nodes":
        for node_id in op["node_ids"]:
            node = task["nodes"][node_id]
            if node["result_ref"]:
                node["superseded_results"].append(node["result_ref"])
            node.update(
                status=WAITING, owner=None, lease_id=None, result_ref=None,
                validity=op.get("validity", ResultValidity.STALE),
                contract_version=task["version"], attempt=node["attempt"] + 1,
            )
            node["history"].append({"event": "reissued", "reason": op.get("reason", ""),
                                    "version": task["version"], "ts": op["ts"]})
    elif kind == "accept_result":
        node = task["nodes"][op["node_id"]]
        node.update(status=DONE, result_ref=op["result_ref"], validity=ResultValidity.VALID,
                    owner=None, lease_id=None, accepted_in_version=task["version"])
        node["history"].append({"event": "accepted", "result_ref": op["result_ref"], "ts": op["ts"]})
    elif kind == "reject_result":
        node = task["nodes"][op["node_id"]]
        node["rejections"].append({"result_ref": op.get("result_ref"), "reasons": op["reasons"],
                                   "core": op.get("core"), "ts": op["ts"]})
    elif kind == "finish_task":
        task.update(status=op["status"], answer=op.get("answer"),
                    answered_by=op.get("answered_by"), finished_at=op["ts"])
    else:
        raise ValueError(f"unknown operation {kind!r}")


@dataclass
class Decision:
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    seq: int | None = None


@dataclass
class ResultProposal:
    task_id: str
    node_id: str
    core_id: str
    lease_id: str
    state_version: int
    dep_refs: dict[str, str]
    result_ref: str
    role: str
    acceptance_passed: bool = True
    acceptance_detail: str = ""


class StateManager:
    def __init__(self, journal: Journal, *, clock: Callable[[], float] = time.time,
                 permissions=None, state: dict | None = None) -> None:
        self.journal = journal
        self.clock = clock
        self.permissions = permissions
        self.state = state if state is not None else empty_state()
        self._lock = threading.RLock()
        self._listeners: list[Callable[[list[dict], dict], None]] = []

    # -- transactions --------------------------------------------------------
    def add_listener(self, fn: Callable[[list[dict], dict], None]) -> None:
        self._listeners.append(fn)

    def transact(self, ops: list[dict], meta: dict | None = None) -> int:
        with self._lock:
            ts = self.clock()
            for op in ops:
                op.setdefault("ts", ts)
            seq = self.journal.begin(ops, meta)
            self.journal.commit(seq)
            for op in ops:
                apply_op(self.state, op)
            self.state["last_seq"] = seq
        for fn in list(self._listeners):
            fn(ops, meta or {})
        return seq

    @classmethod
    def recover(cls, journal: Journal, checkpoint: str | None = None, **kwargs) -> StateManager:
        state, last_seq = empty_state(), 0
        if checkpoint:
            loaded = Journal.load_checkpoint(checkpoint)
            if loaded:
                state, last_seq = loaded
        for seq, ops, _meta in journal.committed_transactions(after_seq=last_seq):
            for op in ops:
                apply_op(state, op)
            state["last_seq"] = seq
        return cls(journal, state=state, **kwargs)

    def checkpoint(self, path) -> None:
        with self._lock:
            Journal.write_checkpoint(path, self.state, self.state["last_seq"])

    # -- reads -----------------------------------------------------------------
    def task(self, task_id: str) -> dict | None:
        return self.state["tasks"].get(task_id)

    def node(self, task_id: str, node_id: str) -> dict | None:
        task = self.task(task_id)
        return task["nodes"].get(node_id) if task else None

    def lease(self, lease_id: str) -> dict | None:
        return self.state["leases"].get(lease_id)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.state)

    def lease_is_live(self, lease_id: str | None, now: float | None = None) -> bool:
        lease = self.lease(lease_id) if lease_id else None
        if not lease or lease["revoked"] or lease["released"]:
            return False
        return (now if now is not None else self.clock()) < lease["expires_at"]

    # -- validation ------------------------------------------------------------
    def validate_result(self, p: ResultProposal) -> list[str]:
        reasons: list[str] = []
        task = self.task(p.task_id)
        if task is None:
            return [f"unknown task {p.task_id}"]
        if task["status"] != "open":
            reasons.append(f"task is {task['status']}")
        node = task["nodes"].get(p.node_id)
        if node is None:
            return reasons + [f"unknown node {p.node_id}"]
        if node["status"] != RUNNING:
            reasons.append(f"node is {node['status']}, not running")
        lease = self.lease(p.lease_id)
        if lease is None:
            reasons.append("no such lease")
        else:
            if lease["revoked"]:
                reasons.append(f"lease revoked ({lease.get('revoked_reason', '')})")
            elif lease["released"]:
                reasons.append("lease already released")
            elif self.clock() >= lease["expires_at"]:
                reasons.append("lease expired")
            if lease["owner"] != p.core_id or lease["node_id"] != p.node_id:
                reasons.append("lease does not belong to this core/node")
        if node["lease_id"] != p.lease_id:
            reasons.append("node is owned under a different lease")
        if p.state_version != node["contract_version"]:
            reasons.append(f"stale state version v{p.state_version} (current contract v{node['contract_version']})")
        for dep in node["deps"]:
            dep_node = task["nodes"][dep]
            if dep_node["status"] != DONE:
                reasons.append(f"dependency {dep} is not accepted")
            elif p.dep_refs.get(dep) != dep_node["result_ref"]:
                reasons.append(f"dependency {dep} changed since this result was computed")
        if self.permissions is not None and not self.permissions.allows(p.role, "publish_result"):
            reasons.append(f"role {p.role} may not publish results")
        if not p.acceptance_passed:
            reasons.append(f"acceptance criteria not met: {p.acceptance_detail}")
        return reasons

    def propose_result(self, p: ResultProposal) -> Decision:
        with self._lock:
            reasons = self.validate_result(p)
            if self.task(p.task_id) is None or self.node(p.task_id, p.node_id) is None:
                return Decision(False, reasons)
            if reasons:
                seq = self.transact([{"op": "reject_result", "task_id": p.task_id, "node_id": p.node_id,
                                      "result_ref": p.result_ref, "reasons": reasons, "core": p.core_id}],
                                    {"kind": "reject_result"})
                return Decision(False, reasons, seq)
            seq = self.transact([
                {"op": "accept_result", "task_id": p.task_id, "node_id": p.node_id, "result_ref": p.result_ref},
                {"op": "release_lease", "lease_id": p.lease_id},
            ], {"kind": "accept_result"})
            return Decision(True, [], seq)

    def propose_graph(self, task_id: str, expected_version: int, nodes: list[dict],
                      max_nodes: int = 24) -> Decision:
        with self._lock:
            task = self.task(task_id)
            if task is None:
                return Decision(False, [f"unknown task {task_id}"])
            reasons: list[str] = []
            if task["version"] != expected_version:
                reasons.append(f"stale graph proposal (v{expected_version}, current v{task['version']})")
            ids = [n.get("id") for n in nodes]
            if any(not i for i in ids) or len(set(ids)) != len(ids):
                reasons.append("node ids must be present and unique")
            if set(ids) & set(task["nodes"]):
                reasons.append("node ids already exist")
            if len(task["nodes"]) + len(nodes) > max_nodes:
                reasons.append(f"graph exceeds {max_nodes} nodes")
            known = set(task["nodes"]) | set(ids)
            for n in nodes:
                for dep in n.get("deps", []):
                    if dep not in known:
                        reasons.append(f"{n.get('id')} depends on unknown node {dep}")
                if n.get("tier", "performance") not in TIERS:
                    reasons.append(f"{n.get('id')} has invalid tier {n.get('tier')}")
                if n.get("role", "performance") not in ROLES:
                    reasons.append(f"{n.get('id')} has invalid role {n.get('role')}")
            if not reasons and _has_cycle(task["nodes"], nodes):
                reasons.append("graph contains a cycle")
            if reasons:
                return Decision(False, reasons)
            seq = self.transact([{"op": "add_nodes", "task_id": task_id, "nodes": nodes}],
                                {"kind": "add_nodes"})
            return Decision(True, [], seq)


def _has_cycle(existing: dict, new: list[dict]) -> bool:
    deps = {nid: list(n["deps"]) for nid, n in existing.items()}
    for n in new:
        deps[n["id"]] = list(n.get("deps", []))
    visiting, done = set(), set()

    def visit(nid: str) -> bool:
        if nid in done:
            return False
        if nid in visiting:
            return True
        visiting.add(nid)
        if any(visit(d) for d in deps.get(nid, [])):
            return True
        visiting.discard(nid)
        done.add(nid)
        return False

    return any(visit(nid) for nid in deps)
