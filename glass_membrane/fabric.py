"""Typed event fabric and per-core mailboxes (Arch §9).

Each mailbox has logically separate control, result, and data queues. Control
is always delivered first, so cancellation cannot wait behind bulk traffic.
Queues are bounded: a full queue refuses work (backpressure) instead of
growing without limit.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Callable

from .events import Envelope

DEFAULT_CAPACITIES = {"control": 64, "result": 32, "data": 32}


class Backpressure(Exception):
    """A bounded queue is full; the producer must pause, throttle, or defer."""


class Mailbox:
    def __init__(self, owner: str, capacities: dict[str, int] | None = None) -> None:
        self.owner = owner
        self.capacities = dict(DEFAULT_CAPACITIES, **(capacities or {}))
        self.queues: dict[str, deque[Envelope]] = {k: deque() for k in ("control", "result", "data")}
        self.refused = {k: 0 for k in self.queues}
        self._nonempty = asyncio.Event()
        self._space = asyncio.Event()

    def try_put(self, env: Envelope) -> bool:
        queue = self.queues[env.traffic]
        if len(queue) >= self.capacities[env.traffic]:
            self.refused[env.traffic] += 1
            return False
        queue.append(env)
        self._nonempty.set()
        return True

    async def put(self, env: Envelope, timeout: float | None = None) -> None:
        """Wait for space; raise Backpressure if none appears in time."""
        while not self.try_put(env):
            self._space.clear()
            try:
                await asyncio.wait_for(self._space.wait(), timeout)
            except asyncio.TimeoutError as exc:
                raise Backpressure(f"{self.owner}:{env.traffic} is full") from exc

    def get_nowait(self) -> Envelope | None:
        for traffic in ("control", "result", "data"):
            if self.queues[traffic]:
                self._space.set()
                return self.queues[traffic].popleft()
        return None

    async def get(self, timeout: float | None = None) -> Envelope:
        while True:
            env = self.get_nowait()
            if env is not None:
                return env
            self._nonempty.clear()
            await asyncio.wait_for(self._nonempty.wait(), timeout)

    def pending(self) -> dict[str, int]:
        return {k: len(q) for k, q in self.queues.items()}


class Fabric:
    def __init__(self) -> None:
        self.mailboxes: dict[str, Mailbox] = {}
        self.subscriptions: dict[str, set[str]] = {}
        self.observers: list[Callable[[Envelope, bool], None]] = []

    def mailbox(self, owner: str, capacities: dict[str, int] | None = None) -> Mailbox:
        if owner not in self.mailboxes:
            self.mailboxes[owner] = Mailbox(owner, capacities)
        return self.mailboxes[owner]

    def _observe(self, env: Envelope, delivered: bool) -> None:
        for fn in list(self.observers):
            fn(env, delivered)

    def send(self, env: Envelope) -> bool:
        """Addressed delivery. Returns False when the recipient's queue is full."""
        if env.to is None:
            raise ValueError("addressed send needs a destination")
        delivered = self.mailbox(env.to).try_put(env)
        self._observe(env, delivered)
        return delivered

    def subscribe(self, owner: str, topic: str) -> None:
        self.subscriptions.setdefault(topic, set()).add(owner)

    def unsubscribe(self, owner: str, topic: str | None = None) -> None:
        topics = [topic] if topic else list(self.subscriptions)
        for t in topics:
            self.subscriptions.get(t, set()).discard(owner)

    def publish(self, env: Envelope) -> dict[str, bool]:
        """Task-relevant publication to subscribers of env.topic."""
        results = {}
        for owner in sorted(self.subscriptions.get(env.topic or "", set())):
            results[owner] = self.mailbox(owner).try_put(env)
        self._observe(env, all(results.values()) if results else True)
        return results
