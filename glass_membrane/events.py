"""Typed events, requests, and failure codes (Arch §9, Ops §10, §14, Schematic §7)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum

from .refs import new_id


class EventType(str, Enum):
    TASK_ASSIGNED = "TASK_ASSIGNED"
    DATA_REQUEST = "DATA_REQUEST"
    DATA_READY = "DATA_READY"
    RESULT_READY = "RESULT_READY"
    VERIFY_REQUEST = "VERIFY_REQUEST"
    DEPENDENCY_COMPLETE = "DEPENDENCY_COMPLETE"
    STEER = "STEER"
    CONSTRAINT_CHANGED = "CONSTRAINT_CHANGED"
    CONFLICT = "CONFLICT"
    SUBTASK_REQUEST = "SUBTASK_REQUEST"
    ESCALATION_REQUEST = "ESCALATION_REQUEST"
    DOWNSHIFT_REQUEST = "DOWNSHIFT_REQUEST"
    RELEASE = "RELEASE"
    SWARM_REQUEST = "SWARM_REQUEST"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    CANCEL = "CANCEL"
    CORE_FAILED = "CORE_FAILED"
    BUDGET_WARNING = "BUDGET_WARNING"
    CAP_CHANGED = "CAP_CHANGED"


class FailureCode(str, Enum):
    BLOCKED = "BLOCKED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICT = "CONFLICT"
    CAPABILITY_LIMIT = "CAPABILITY_LIMIT"
    TOOL_FAILURE = "TOOL_FAILURE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    STATE_CHANGED = "STATE_CHANGED"


# Shift/scale requests a core may attach to its output (Ops §5, §9).
SHIFT_REQUESTS = {
    EventType.ESCALATION_REQUEST, EventType.DOWNSHIFT_REQUEST,
    EventType.SUBTASK_REQUEST, EventType.SWARM_REQUEST, EventType.RELEASE,
}

_CONTROL = {
    EventType.TASK_ASSIGNED, EventType.STEER, EventType.CONSTRAINT_CHANGED, EventType.PAUSE,
    EventType.RESUME, EventType.CANCEL, EventType.CAP_CHANGED, EventType.BUDGET_WARNING,
    EventType.CORE_FAILED,
}
_DATA = {EventType.DATA_REQUEST, EventType.DATA_READY}


def traffic_class(event_type: EventType) -> str:
    """Control traffic is scheduled ahead of result and data traffic (Arch §9)."""
    if event_type in _CONTROL:
        return "control"
    if event_type in _DATA:
        return "data"
    return "result"


@dataclass
class Envelope:
    type: EventType
    task: str | None
    node: str | None
    sender: str
    to: str | None = None
    topic: str | None = None
    state_version: int | None = None
    refs: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    correlation: str | None = None
    id: str = field(default_factory=lambda: new_id("msg-"))
    ts: float = field(default_factory=time.time)

    @property
    def message_ref(self) -> str:
        return f"message://{self.id}"

    @property
    def traffic(self) -> str:
        return traffic_class(self.type)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["type"] = self.type.value
        return data
