"""Renewable, bounded leases (Arch §12).

A lease ties an owner to work. Expiry or revocation lets the scheduler reclaim
the work; a worker that finishes after losing its lease cannot commit.
Leases are canonical state, so they are journaled and survive recovery.
"""

from __future__ import annotations

import time
from typing import Callable

from .refs import new_id


class LeaseManager:
    def __init__(self, state, clock: Callable[[], float] = time.time, default_ttl: float = 120.0) -> None:
        self.state = state
        self.clock = clock
        self.default_ttl = default_ttl

    def grant(self, task_id: str, node_id: str, owner: str, ttl: float | None = None,
              binding: dict | None = None) -> str:
        lease_id = new_id("lease-")
        now = self.clock()
        self.state.transact([
            {"op": "grant_lease", "lease": {
                "id": lease_id, "owner": owner, "task_id": task_id, "node_id": node_id,
                "granted_at": now, "expires_at": now + (ttl or self.default_ttl)}},
            {"op": "update_node", "task_id": task_id, "node_id": node_id,
             "fields": {"status": "running", "owner": owner, "lease_id": lease_id, "binding": binding}},
        ], {"kind": "grant_lease"})
        return lease_id

    def renew(self, lease_id: str, ttl: float | None = None) -> bool:
        if not self.state.lease_is_live(lease_id):
            return False
        self.state.transact([{"op": "renew_lease", "lease_id": lease_id,
                              "expires_at": self.clock() + (ttl or self.default_ttl)}],
                            {"kind": "renew_lease"})
        return True

    def revoke(self, lease_id: str, reason: str) -> None:
        lease = self.state.lease(lease_id)
        if lease and not lease["revoked"] and not lease["released"]:
            self.state.transact([{"op": "revoke_lease", "lease_id": lease_id, "reason": reason}],
                                {"kind": "revoke_lease"})

    def expired(self) -> list[dict]:
        now = self.clock()
        return [dict(l) for l in self.state.state["leases"].values()
                if not l["revoked"] and not l["released"] and now >= l["expires_at"]]
