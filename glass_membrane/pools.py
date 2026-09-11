"""Elastic per-provider process admission (implementation of Arch §5 capacity).

No process exists while idle: a slot here is permission to start one CLI
process, released when that process exits. Work may not take the last
`headroom` slots of a provider, so filter/steer traffic can always start.
A rate-limit signal halves that provider's effective cap; successes and
quiet time raise it back toward the ceiling.
"""

from __future__ import annotations

import asyncio
import time

from .events import Signal


class ProviderPool:
    def __init__(self, provider: str, ceiling: int, headroom: int = 1) -> None:
        self.provider = provider
        self.ceiling = max(1, ceiling)
        self.cap = self.ceiling
        self.headroom = max(0, headroom)
        self.live = 0
        self.peak = 0
        self.rate_limit_events = 0
        self.last_cut = 0.0
        self.unavailable_until = 0.0   # usage allowance exhausted until this time (epoch seconds)
        self._successes = 0

    @property
    def available(self) -> bool:
        return time.time() >= self.unavailable_until

    def mark_unavailable(self, until: float) -> None:
        self.unavailable_until = max(self.unavailable_until, until)
        self.rate_limit_events += 1

    def limit(self, priority: str) -> int:
        if priority == "control" or self.cap <= 1:
            return self.cap
        return self.cap - min(self.headroom, self.cap - 1)

    def try_acquire(self, priority: str = "work") -> bool:
        if not self.available:
            return False
        if self.live < self.limit(priority):
            self.live += 1
            self.peak = max(self.peak, self.live)
            return True
        return False

    def release(self) -> None:
        self.live = max(0, self.live - 1)

    def on_rate_limit(self, now: float) -> None:
        self.rate_limit_events += 1
        self.cap = max(1, self.cap // 2)
        self.last_cut = now
        self._successes = 0

    def on_success(self) -> None:
        self._successes += 1
        if self.cap < self.ceiling and self._successes >= 3:
            self.cap += 1
            self._successes = 0

    def recover(self, now: float, quiet_s: float = 20.0) -> None:
        if self.cap < self.ceiling and now - self.last_cut >= quiet_s:
            self.cap += 1
            self.last_cut = now

    def snapshot(self) -> dict:
        return {"provider": self.provider, "live": self.live, "cap": self.cap, "ceiling": self.ceiling,
                "peak": self.peak, "work_limit": self.limit("work"),
                "rate_limit_events": self.rate_limit_events, "backing_off": self.cap < self.ceiling,
                "unavailable_until": None if self.available else self.unavailable_until}


class Pools:
    def __init__(self, ceilings: dict[str, int], headroom: int = 1, signal: Signal | None = None) -> None:
        self.pools = {p: ProviderPool(p, c, headroom) for p, c in ceilings.items()}
        self.signal = signal or Signal()

    def try_acquire(self, provider: str, priority: str = "work") -> bool:
        pool = self.pools.get(provider)
        return bool(pool and pool.try_acquire(priority))

    async def acquire_any(self, providers: list[str], priority: str = "control") -> str:
        while True:
            for provider in providers:
                if self.try_acquire(provider, priority):
                    return provider
            since = self.signal.version
            await self.signal.wait(timeout=1.0, since=since)

    def release(self, provider: str) -> None:
        if provider in self.pools:
            self.pools[provider].release()
        self.signal.notify()

    def on_rate_limit(self, provider: str) -> None:
        if provider in self.pools:
            self.pools[provider].on_rate_limit(time.time())

    def on_success(self, provider: str) -> None:
        if provider in self.pools:
            self.pools[provider].on_success()

    def mark_unavailable(self, provider: str, until: float) -> None:
        if provider in self.pools:
            self.pools[provider].mark_unavailable(until)

    def available(self, provider: str) -> bool:
        pool = self.pools.get(provider)
        return bool(pool and pool.available)

    def recover(self) -> None:
        now = time.time()
        for pool in self.pools.values():
            before = pool.cap
            pool.recover(now)
            if pool.unavailable_until and now >= pool.unavailable_until:
                pool.unavailable_until = 0.0
                self.signal.notify()
            if pool.cap != before:
                self.signal.notify()

    @property
    def total_live(self) -> int:
        return sum(p.live for p in self.pools.values())

    def snapshot(self) -> dict:
        return {p: pool.snapshot() for p, pool in self.pools.items()}
