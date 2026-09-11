"""Budget Governor (Schematic §3): admission, warning, and stop decisions."""

from __future__ import annotations


class BudgetGovernor:
    def __init__(self, max_calls_per_run: int = 60, max_usd_per_run: float | None = None) -> None:
        self.max_calls = max_calls_per_run
        self.max_usd = max_usd_per_run

    def admit(self, run) -> tuple[bool, str]:
        if run.calls >= self.max_calls:
            return False, f"call budget of {self.max_calls} per run exhausted"
        if self.max_usd is not None and run.cost_usd >= self.max_usd:
            return False, "spend-estimate budget exhausted"
        return True, ""

    def charge(self, run) -> None:
        run.calls += 1

    def record(self, run, usage) -> None:
        if usage.cost_usd:
            run.cost_usd += usage.cost_usd

    def warning(self, run) -> bool:
        return run.calls >= 0.8 * self.max_calls
