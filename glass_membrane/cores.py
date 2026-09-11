"""Logical slots: 8 front-end filter slots and 12 reasoning cores (Arch §4, §5).

A core is a runtime execution slot, not a model. E/P/F labels are reservation
classes: the scheduler prefers a matching class but may rebind any idle core.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from .blackboard import TIERS

CORE_LAYOUT = [("E0", "efficiency"), ("E1", "efficiency"), ("E2", "efficiency"), ("E3", "efficiency"),
               ("E4", "efficiency"), ("E5", "efficiency"), ("P0", "performance"), ("P1", "performance"),
               ("P2", "performance"), ("P3", "performance"), ("F0", "flagship"), ("F1", "flagship")]
FILTER_LAYOUT = [("R0", "efficiency"), ("R1", "efficiency"), ("R2", "efficiency"), ("R3", "efficiency"),
                 ("R4", "performance"), ("R5", "performance"), ("R6", "flagship"), ("R7", "flagship")]


@dataclass
class Slot:
    id: str
    kind: str
    reservation: str
    status: str = "idle"
    run_id: str | None = None
    task_id: str | None = None
    node_id: str | None = None
    binding: dict | None = None
    lease_id: str | None = None
    children: int = 0
    started_at: float | None = None
    assignments: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class SlotTable:
    def __init__(self) -> None:
        self.cores = {sid: Slot(sid, "core", tier) for sid, tier in CORE_LAYOUT}
        self.filters = {sid: Slot(sid, "filter", tier) for sid, tier in FILTER_LAYOUT}

    def _get(self, slot_id: str) -> Slot:
        return self.cores.get(slot_id) or self.filters[slot_id]

    def idle_core(self, tier: str) -> Slot | None:
        idle = [s for s in self.cores.values() if s.status == "idle"]
        if not idle:
            return None
        want = TIERS.index(tier) if tier in TIERS else 1
        return min(idle, key=lambda s: (abs(TIERS.index(s.reservation) - want), s.id))

    def idle_filter(self, tier: str) -> Slot | None:
        for slot in self.filters.values():
            if slot.reservation == tier and slot.status == "idle":
                return slot
        return None

    def occupy(self, slot_id: str, **fields) -> Slot:
        slot = self._get(slot_id)
        slot.status = "busy"
        slot.started_at = time.time()
        slot.assignments += 1
        for key, value in fields.items():
            setattr(slot, key, value)
        return slot

    def update(self, slot_id: str, **fields) -> None:
        slot = self._get(slot_id)
        for key, value in fields.items():
            setattr(slot, key, value)

    def free(self, slot_id: str) -> None:
        slot = self._get(slot_id)
        slot.status = "idle"
        slot.run_id = slot.task_id = slot.node_id = slot.lease_id = None
        slot.binding = None
        slot.children = 0
        slot.started_at = None

    def idle_core_count(self) -> int:
        return sum(1 for s in self.cores.values() if s.status == "idle")

    def snapshot(self) -> dict:
        return {"filters": [s.to_dict() for s in self.filters.values()],
                "cores": [s.to_dict() for s in self.cores.values()]}
