"""Logical references (Arch §17).

References identify runtime objects independently of where they are stored.
Messages carry references; large objects stay in the store (Arch §2.4, §9).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

NAMESPACES = ("task", "source", "context", "result", "compute", "core", "message")

_REF_RE = re.compile(r"^(?P<ns>[a-z]+)://(?P<path>[^@]+?)(?:@v(?P<version>\d+))?$")


@dataclass(frozen=True)
class Ref:
    ns: str
    path: str
    version: int | None = None

    def __post_init__(self) -> None:
        if self.ns not in NAMESPACES:
            raise ValueError(f"unknown namespace {self.ns!r}")
        if not self.path or "@" in self.path:
            raise ValueError(f"invalid reference path {self.path!r}")

    def __str__(self) -> str:
        base = f"{self.ns}://{self.path}"
        return base if self.version is None else f"{base}@v{self.version}"

    @classmethod
    def parse(cls, text: str) -> Ref:
        match = _REF_RE.match(text)
        if not match:
            raise ValueError(f"not a reference: {text!r}")
        version = match.group("version")
        return cls(match.group("ns"), match.group("path"), int(version) if version else None)

    def at(self, version: int) -> Ref:
        return Ref(self.ns, self.path, version)

    def unversioned(self) -> Ref:
        return Ref(self.ns, self.path)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"
