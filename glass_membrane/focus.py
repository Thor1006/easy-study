"""Focus contracts (Arch §8, Ops §3).

Every assignment carries a contract describing the job, its inputs, the
expected output, permissions, budget, lease, and stop conditions. A core may
not silently expand it; adjacent work becomes a SUBTASK_REQUEST.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class FocusContract:
    task_ref: str
    node_ref: str
    core_ref: str
    role: str
    goal: str
    job: str
    state_version: int
    constraints: list[str] = field(default_factory=list)
    input_refs: list[str] = field(default_factory=list)
    dependencies: dict[str, str] = field(default_factory=dict)
    expected_output: str = "A structured result matching the output schema."
    acceptance_criteria: str = "Addresses the job completely within the stated constraints."
    permissions: list[str] = field(default_factory=lambda: ["publish_result", "request_subtask", "request_shift"])
    budget: dict = field(default_factory=dict)
    lease: dict = field(default_factory=dict)
    side_tasks: str = "Request adjacent work with SUBTASK_REQUEST; do not expand scope."
    stop_conditions: list[str] = field(default_factory=lambda: [
        "completion", "cancellation", "lost lease", "budget exhausted", "blocking dependency"])
    partition: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = []
        for key, value in self.to_dict().items():
            if value in (None, [], {}, ""):
                continue
            if isinstance(value, list):
                lines.append(f"{key}:")
                lines.extend(f"  - {item}" for item in value)
            elif isinstance(value, dict):
                lines.append(f"{key}:")
                lines.extend(f"  {k}: {v}" for k, v in value.items())
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)
