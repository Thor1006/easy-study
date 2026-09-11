"""Model Adapter contract (Arch §6).

The adapter separates runtime contracts from provider-specific invocation.
It reports results, errors, usage, and capability status honestly: data a
provider does not expose is `None` (unknown), never zero.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


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


class ModelAdapter:
    provider = "abstract"
    capabilities: dict[str, str] = {}
    provenance = "adapter declaration"

    async def invoke(self, inv: Invocation) -> ModelResult:  # pragma: no cover - interface
        raise NotImplementedError
