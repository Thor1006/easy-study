"""Interrupt Controller and vertical half-bypass (Arch §11, Schematic §9).

A steer reaches the affected work without restarting the task. Matching is
deterministic first (node titles in the steer text, explicit cancel words);
the filter's semantic hint is used only when that finds nothing. The change is
committed as one transaction: new constraint version, revoked leases, and
reissued nodes. Unaffected nodes keep their contract version, so their
results stay acceptable.
"""

from __future__ import annotations

import asyncio

from .blackboard import CANCELLED, DONE, FAILED, RUNNING, ResultValidity
from .events import Envelope, EventType
from .refs import new_id

_CANCEL_WORDS = ("stop", "cancel", "abort", "do not execute", "don't execute")


class InterruptController:
    def __init__(self, rt) -> None:
        self.rt = rt

    def match(self, task_id: str, text: str, hint: dict | None = None) -> tuple[str, list[str]]:
        nodes = self.rt.state.task(task_id)["nodes"]
        low = text.lower().strip().rstrip(".!")
        if low in _CANCEL_WORDS:
            return "cancel", []
        ids = [nid for nid, n in nodes.items()
               if n["role"] != "planner" and len(n["title"]) >= 3 and n["title"].lower() in low]
        if ids:
            return "nodes", ids
        if hint:
            scope = hint.get("scope")
            if scope == "cancel":
                return "cancel", []
            hinted = [i for i in (hint.get("affected_nodes") or []) if i in nodes]
            if scope == "nodes" and hinted:
                return "nodes", hinted
        return "global", []

    def apply(self, run, task_id: str, text: str, hint: dict | None = None) -> dict:
        rt = self.rt
        task = rt.state.task(task_id)
        if task is None or task["status"] != "open":
            return {"applied": False, "reason": "task is not active"}
        scope, ids = self.match(task_id, text, hint)
        if scope == "cancel":
            asyncio.get_running_loop().create_task(rt.cancel(run.id, "cancelled by steer"))
            return {"applied": True, "scope": "cancel", "affected": [], "kept": [],
                    "message": "Cancelling the run"}

        nodes = task["nodes"]
        if scope == "global":
            affected = {nid for nid, n in nodes.items()
                        if n["status"] not in (CANCELLED, FAILED)
                        and not (n["role"] == "planner" and n["status"] == DONE)}
        else:
            affected = set(ids)
            for nid in ids:
                affected |= rt.board.dependents(task_id, nid)

        running = [(nid, nodes[nid]["owner"], nodes[nid]["lease_id"])
                   for nid in sorted(affected) if nodes[nid]["status"] == RUNNING]
        had_work = sorted(n for n in affected if nodes[n]["result_ref"] or nodes[n]["status"] == RUNNING)
        not_started = sorted(n for n in affected if n not in had_work)

        ops = [{"op": "add_constraint", "task_id": task_id, "constraint_id": new_id("c-"), "text": text,
                "scope": "global" if scope == "global" else ",".join(sorted(ids))}]
        ops += [{"op": "revoke_lease", "lease_id": lease, "reason": "steer"} for _, _, lease in running if lease]
        if had_work:
            ops.append({"op": "reissue_nodes", "task_id": task_id, "node_ids": had_work,
                        "validity": ResultValidity.STALE, "reason": f"steer: {text[:80]}"})
        if not_started:
            ops.append({"op": "reissue_nodes", "task_id": task_id, "node_ids": not_started,
                        "validity": None, "reason": "steer (not started)", "count_attempt": False})
        rt.state.transact(ops, {"kind": "steer"})
        version = rt.state.task(task_id)["version"]

        # Control traffic goes out ahead of everything else. Where the provider can be
        # interrupted, CANCEL stops the obsolete generation; otherwise validation still
        # blocks its late result.
        control = EventType.CANCEL if rt.config.runtime["cancel_in_flight"] else EventType.STEER
        for nid, owner, lease in running:
            if owner:
                rt.fabric.send(Envelope(control, task_id, nid, "interrupt-controller", to=owner,
                                        state_version=version, payload={"lease_id": lease, "reason": "steer"}))
        rt.fabric.publish(Envelope(EventType.CONSTRAINT_CHANGED, task_id, None, "interrupt-controller",
                                   topic=f"task:{task_id}", state_version=version, payload={"text": text}))

        kept = sorted(nid for nid, n in nodes.items() if nid not in affected and n["status"] == DONE)
        stale = [n for n in had_work if n in affected]
        if scope == "global":
            message = f"Steer applied to the whole task; {len(stale)} result(s) are stale and will re-run"
        else:
            message = (f"Steer applied to {', '.join(nodes[i]['title'] for i in ids)}; "
                       f"{len(stale)} result(s) re-run, {len(kept)} kept")
        rt.emit("CONSTRAINT_CHANGED", run=run.id, task=task_id, message=message,
                status=f"Your steer made {len(stale)} result(s) stale — re-running them" if stale else message,
                data={"scope": scope, "affected": sorted(affected), "stale": stale, "kept": kept,
                      "version": version, "constraint": text})
        rt.signal.notify()
        return {"applied": True, "scope": scope, "affected": sorted(affected), "stale": stale,
                "kept": kept, "version": version, "message": message}
