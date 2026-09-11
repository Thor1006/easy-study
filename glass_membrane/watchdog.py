"""Deterministic watchdog (Arch §13).

Detects expired leases and reclaims their work with predefined recovery
actions, and lets throttled provider caps recover. No model decides whether
runtime invariants hold.
"""

from __future__ import annotations

import asyncio
import logging

from .blackboard import FAILED, RUNNING
from .events import Envelope, EventType

log = logging.getLogger("glass_membrane.watchdog")


class Watchdog:
    def __init__(self, rt) -> None:
        self.rt = rt

    async def run(self) -> None:
        interval = float(self.rt.config.runtime["watchdog_interval_s"])
        while True:
            try:
                self.tick()
            except Exception:  # the watchdog must survive its own bugs
                log.exception("watchdog tick failed")
            await asyncio.sleep(interval)

    def tick(self) -> None:
        rt = self.rt
        max_attempts = int(rt.config.runtime["max_attempts"])
        for lease in rt.leases.expired():
            task_id, node_id = lease["task_id"], lease["node_id"]
            node = rt.state.node(task_id, node_id)
            ops = [{"op": "revoke_lease", "lease_id": lease["id"], "reason": "lease expired"}]
            reclaimed = node is not None and node["lease_id"] == lease["id"] and node["status"] == RUNNING
            if reclaimed:
                if node["attempt"] + 1 < max_attempts:
                    ops.append({"op": "reissue_nodes", "task_id": task_id, "node_ids": [node_id],
                                "validity": None, "reason": "lease expired"})
                else:
                    ops.append({"op": "update_node", "task_id": task_id, "node_id": node_id,
                                "fields": {"status": FAILED, "failure": "CORE_FAILED",
                                           "failure_detail": "lease expired repeatedly",
                                           "owner": None, "lease_id": None}})
            rt.state.transact(ops, {"kind": "watchdog"})
            if reclaimed and rt.config.runtime["cancel_in_flight"] and lease["owner"]:
                rt.fabric.send(Envelope(EventType.CANCEL, task_id, node_id, "watchdog", to=lease["owner"],
                                        payload={"lease_id": lease["id"], "reason": "lease expired"}))
            run = rt.run_for_task(task_id)
            rt.emit("CORE_FAILED", run=run.id if run else None, task=task_id, node=node_id,
                    message=f"{lease['owner']} lost its lease on {node_id}; work reclaimed",
                    data={"lease": lease["id"], "reclaimed": reclaimed})
            rt.signal.notify()
        rt.pools.recover()
