"""Write-ahead journal and checkpoints (Arch §13).

Every transaction is written as a `begin` record (carrying its operations)
followed by a `commit` record. Only transactions with a commit record are
authoritative; a `begin` without a commit is an incomplete attempt, and a
torn final line is ignored. Replay applies committed sequence numbers greater
than the checkpoint's `last_seq`, so no transition is applied twice.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class Journal:
    def __init__(self, path: Path | str | None = None, *, fsync: bool = True) -> None:
        self.path = Path(path) if path else None
        self.fsync = fsync
        self._lock = threading.Lock()
        self._mem: list[dict] = []
        self._seq = 0
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                data = self.path.read_bytes()
                if data and not data.endswith(b"\n"):
                    # A torn write left a partial line; start fresh records on a new line.
                    with open(self.path, "ab") as f:
                        f.write(b"\n")
            for record in self._read():
                self._seq = max(self._seq, int(record.get("seq", 0)))

    # -- writing -------------------------------------------------------------
    def _append(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                if self.fsync:
                    os.fsync(f.fileno())
        else:
            self._mem.append(json.loads(line))

    def begin(self, ops: list[dict], meta: dict | None = None) -> int:
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._append({"seq": seq, "phase": "begin", "ops": ops, "meta": meta or {}, "ts": time.time()})
            return seq

    def commit(self, seq: int) -> None:
        with self._lock:
            self._append({"seq": seq, "phase": "commit", "ts": time.time()})

    def abort(self, seq: int, reason: str = "") -> None:
        with self._lock:
            self._append({"seq": seq, "phase": "abort", "reason": reason, "ts": time.time()})

    # -- reading -------------------------------------------------------------
    def _read(self) -> list[dict]:
        if not self.path:
            return list(self._mem)
        if not self.path.exists():
            return []
        records = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # torn write: an incomplete attempt, never authoritative
        return records

    def records(self) -> list[dict]:
        with self._lock:
            return self._read()

    def committed_transactions(self, after_seq: int = 0) -> list[tuple[int, list[dict], dict]]:
        begins: dict[int, dict] = {}
        committed: set[int] = set()
        aborted: set[int] = set()
        for record in self.records():
            seq = record.get("seq")
            phase = record.get("phase")
            if phase == "begin":
                begins.setdefault(seq, record)
            elif phase == "commit":
                committed.add(seq)
            elif phase == "abort":
                aborted.add(seq)
        return [
            (seq, begins[seq]["ops"], begins[seq].get("meta", {}))
            for seq in sorted(committed - aborted)
            if seq > after_seq and seq in begins
        ]

    def incomplete_transactions(self) -> list[int]:
        begins, finished = set(), set()
        for record in self.records():
            if record.get("phase") == "begin":
                begins.add(record["seq"])
            elif record.get("phase") in ("commit", "abort"):
                finished.add(record["seq"])
        return sorted(begins - finished)

    # -- checkpoints ---------------------------------------------------------
    @staticmethod
    def write_checkpoint(path: Path | str, state: dict, last_seq: int) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_seq": last_seq, "state": state, "ts": time.time()}, f, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    @staticmethod
    def load_checkpoint(path: Path | str) -> tuple[dict, int] | None:
        path = Path(path)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["state"], int(data["last_seq"])
