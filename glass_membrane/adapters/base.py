"""Model Adapter contract (Arch §6).

The adapter separates runtime contracts from provider-specific invocation.
It reports results, errors, usage, and capability status honestly: data a
provider does not expose is `None` (unknown), never zero.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

_RESET_AT = re.compile(r"(?:try again (?:at|after)|resets?(?: at)?)\s+(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?", re.I)


def quota_reset_time(text: str, now: float | None = None) -> float | None:
    """If a provider message says a usage limit was hit, return when to try again (epoch seconds).

    Parses "try again at 5:19 PM" / "resets 5pm" in local time; falls back to 30 minutes.
    Returns None when the message is not about an exhausted usage allowance.
    """
    low = (text or "").lower()
    if "usage limit" not in low and "quota" not in low:
        return None
    now = time.time() if now is None else now
    match = _RESET_AT.search(text)
    if not match:
        return now + 1800
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower().replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    local = time.localtime(now)
    target = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0, 0, 0, -1))
    return target + 86400 if target <= now else target


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cost_usd: float | None = None  # client-side estimate where the provider reports one

    @property
    def known(self) -> bool:
        return any(v is not None for v in (self.input_tokens, self.output_tokens,
                                           self.cached_input_tokens, self.cost_usd))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["known"] = self.known
        return data


@dataclass
class Invocation:
    run_id: str
    label: str                     # slot or core id, e.g. "R1" or "P2"
    role: str
    system_prompt: str
    prompt: str
    schema: dict
    model: str | None = None
    effort: str | None = None
    timeout_s: float = 300.0
    swarm_grant: int = 0           # native children granted by the Swarm Governor
    max_budget_usd: float | None = None
    env: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)  # structured copy of the prompt inputs


@dataclass
class ModelResult:
    ok: bool
    output: dict | None = None
    raw_text: str = ""
    usage: Usage = field(default_factory=Usage)
    children_spawned: int | None = 0   # None = provider does not expose it
    failure: str | None = None         # FailureCode value
    detail: str = ""
    rate_limited: bool = False
    duration_s: float = 0.0
    retry_after: float | None = None   # epoch seconds: the provider's usage allowance is exhausted until then


class ModelAdapter:
    provider = "abstract"
    capabilities: dict[str, str] = {}
    provenance = "adapter declaration"

    async def invoke(self, inv: Invocation) -> ModelResult:  # pragma: no cover - interface
        raise NotImplementedError
