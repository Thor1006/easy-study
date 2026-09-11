"""Configuration and the compatibility registry (Arch §6).

Capability tiers map to ordered provider bindings. Profiles distinguish
documented, measured, simulated, unknown, and unsupported behaviour; nothing
here is a permanent model ranking.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .blackboard import TIERS

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "runtime" / "config" / "models.toml"

TIER_UP = {"efficiency": "performance", "performance": "flagship"}
TIER_DOWN = {"flagship": "performance", "performance": "efficiency"}

DEFAULTS: dict = {
    "providers": {
        "claude": {"command": "claude", "max_processes": 12},
        "codex": {"command": "codex", "max_processes": 12},
    },
    "tiers": {
        "efficiency": {"bindings": [{"provider": "claude", "model": "haiku"},
                                    {"provider": "codex", "effort": "low"}]},
        "performance": {"bindings": [{"provider": "codex", "effort": "medium"},
                                     {"provider": "claude", "model": "sonnet", "effort": "medium"}]},
        "flagship": {"bindings": [{"provider": "claude", "model": "opus", "effort": "high"},
                                  {"provider": "codex", "effort": "high"}]},
    },
    "filter": {"answer_threshold": 0.85, "routing_threshold": 0.7},
    "runtime": {
        "call_timeout_s": 300.0, "lease_ttl_s": 360.0, "max_nodes": 24, "max_attempts": 3,
        "max_escalations": 2, "control_headroom": 1, "cancel_in_flight": True, "heartbeat": True,
        "watchdog_interval_s": 0.5, "context_max_chars": 12000, "fsync": True,
    },
    "swarm": {"max_children_total": 12, "max_children_per_core": 4, "prefer_idle_cores": True},
    "budget": {"max_calls_per_run": 60, "max_usd_per_call": 2.0},
}


def merge(base: dict, over: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


@dataclass(frozen=True)
class Binding:
    provider: str
    model: str | None = None
    effort: str | None = None

    def to_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "effort": self.effort}

    def label(self) -> str:
        return " ".join(x for x in (self.provider, self.model, self.effort) if x)


class Config:
    def __init__(self, data: dict | None = None) -> None:
        self.data = merge(DEFAULTS, data or {})

    @classmethod
    def load(cls, path: str | Path | None = None, overrides: dict | None = None) -> Config:
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        data = tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return cls(merge(data, overrides or {}))

    @property
    def runtime(self) -> dict:
        return self.data["runtime"]

    @property
    def filter(self) -> dict:
        return self.data["filter"]

    @property
    def swarm(self) -> dict:
        return self.data["swarm"]

    @property
    def budget(self) -> dict:
        return self.data["budget"]

    @property
    def providers(self) -> dict:
        return self.data["providers"]

    def provider_ceiling(self, name: str) -> int:
        return int(self.providers.get(name, {}).get("max_processes", 1))

    def tier_bindings(self, tier: str) -> list[Binding]:
        fields = ("provider", "model", "effort")
        return [Binding(**{k: b.get(k) for k in fields}) for b in self.data["tiers"][tier]["bindings"]]


class Registry:
    def __init__(self, config: Config, adapters: dict) -> None:
        self.config = config
        self.adapters = adapters

    def bindings(self, tier: str, avoid: str | None = None) -> list[Binding]:
        if tier not in TIERS:
            tier = "performance"
        usable = [b for b in self.config.tier_bindings(tier) if b.provider in self.adapters]
        preferred = [b for b in usable if b.provider != avoid]
        return preferred or usable

    def profile(self, provider: str) -> dict:
        adapter = self.adapters.get(provider)
        return {"provider": provider,
                "capabilities": dict(getattr(adapter, "capabilities", {}) or {}),
                "provenance": getattr(adapter, "provenance", "unknown")}

    def supports_swarm(self, provider: str) -> bool:
        status = self.profile(provider)["capabilities"].get("swarm", "unknown")
        return status in ("documented", "measured", "simulated")
