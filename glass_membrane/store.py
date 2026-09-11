"""Immutable, versioned object store (Arch §10, §12, §17).

Published objects never change. A correction is a new version that links to
the one it supersedes. Acceptance and validity live in canonical state, not
in the stored object.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .refs import Ref

# Physical layout from Arch §17; logical refs do not depend on it.
_DIRS = {"source": "source", "context": "context", "result": "results", "compute": "compute"}


class ImmutableWriteError(Exception):
    """Raised on any attempt to overwrite a published object version."""


class ObjectStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else None
        self._objects: dict[str, dict] = {}
        self._latest: dict[str, int] = {}
        self._lock = threading.Lock()

    # -- paths ---------------------------------------------------------------
    def _file(self, ref: Ref) -> Path:
        assert self.root is not None
        safe = ref.path.replace("/", "__").replace(":", "_")
        return self.root / _DIRS.get(ref.ns, ref.ns) / f"{safe}@v{ref.version}.json"

    def _scan_latest(self, ns: str, path: str) -> int:
        if not self.root:
            return 0
        safe = path.replace("/", "__").replace(":", "_")
        folder = self.root / _DIRS.get(ns, ns)
        best = 0
        if folder.exists():
            for f in folder.glob(f"{safe}@v*.json"):
                try:
                    best = max(best, int(f.stem.rsplit("@v", 1)[1]))
                except ValueError:
                    continue
        return best

    def _write(self, ref: Ref, record: dict) -> None:
        key = str(ref)
        if key in self._objects:
            raise ImmutableWriteError(f"{ref} is already published")
        if self.root:
            target = self._file(ref)
            if target.exists():
                raise ImmutableWriteError(f"{ref} is already published")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, target)
        self._objects[key] = record

    # -- public API ----------------------------------------------------------
    def put(self, ns: str, path: str, content, *, supersedes: Ref | None = None,
            meta: dict | None = None) -> Ref:
        with self._lock:
            key = f"{ns}://{path}"
            if key not in self._latest:
                self._latest[key] = self._scan_latest(ns, path)
            version = self._latest[key] + 1
            ref = Ref(ns, path, version)
            record = {
                "ref": str(ref),
                "content": content,
                "meta": meta or {},
                "supersedes": str(supersedes) if supersedes else None,
                "created_at": time.time(),
            }
            self._write(ref, record)
            self._latest[key] = version
            return ref

    def get(self, ref: Ref | str) -> dict:
        ref = Ref.parse(ref) if isinstance(ref, str) else ref
        if ref.version is None:
            latest = self.latest(ref.ns, ref.path)
            if latest is None:
                raise KeyError(str(ref))
            ref = latest
        key = str(ref)
        with self._lock:
            if key in self._objects:
                return self._objects[key]
            if self.root and self._file(ref).exists():
                record = json.loads(self._file(ref).read_text(encoding="utf-8"))
                self._objects[key] = record
                return record
        raise KeyError(key)

    def content(self, ref: Ref | str):
        return self.get(ref)["content"]

    def latest(self, ns: str, path: str) -> Ref | None:
        with self._lock:
            key = f"{ns}://{path}"
            if key not in self._latest:
                self._latest[key] = self._scan_latest(ns, path)
            version = self._latest[key]
        return Ref(ns, path, version) if version else None
