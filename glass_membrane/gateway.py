"""Input Gateway (Arch §3): every request and steer enters here."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .refs import new_id

MAX_INPUT_CHARS = 20000
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class Input:
    id: str
    text: str
    kind: str              # "auto" | "steer"
    target_run: str | None
    source: str
    received_at: float


class Gateway:
    def normalize(self, text: str, *, kind: str = "auto", target_run: str | None = None,
                  source: str = "api") -> Input:
        if not isinstance(text, str):
            raise ValueError("input must be text")
        cleaned = _CONTROL_CHARS.sub("", text).strip()
        if not cleaned:
            raise ValueError("input is empty")
        if len(cleaned) > MAX_INPUT_CHARS:
            raise ValueError(f"input exceeds {MAX_INPUT_CHARS} characters")
        if kind not in ("auto", "steer"):
            raise ValueError(f"unknown input kind {kind!r}")
        return Input(new_id("in-"), cleaned, kind, target_run, source, time.time())
