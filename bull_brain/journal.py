"""Append-only paper-trading journal (JSONL).

Records every plan, gate result, fill, exit, and review so outcomes stay
separate from hypotheses. Each line carries a sequence number and the SHA-256
of the previous line, so silent edits are detectable via `verify()`.
No account credentials belong here.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

EVENT_TYPES = {"plan", "gate", "fill", "exit", "review", "reject"}


class JournalError(Exception):
    pass


class Journal:
    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else None
        self._entries: list[dict[str, Any]] = []
        if self.path and self.path.exists():
            self._entries = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]
            self.verify()

    @staticmethod
    def _hash(entry: dict[str, Any]) -> str:
        body = {k: v for k, v in entry.items() if k != "hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def append(self, event: str, plan_id: str, data: dict[str, Any],
               at: Optional[datetime] = None) -> dict[str, Any]:
        if event not in EVENT_TYPES:
            raise JournalError(f"unknown event type {event!r}")
        entry = {
            "seq": len(self._entries),
            "at": (at or datetime.now(timezone.utc)).isoformat(),
            "event": event,
            "plan_id": plan_id,
            "data": data,
            "prev": self._entries[-1]["hash"] if self._entries else "",
        }
        entry["hash"] = self._hash(entry)
        self._entries.append(entry)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        return entry

    def entries(self, plan_id: Optional[str] = None, event: Optional[str] = None) -> list[dict[str, Any]]:
        return [e for e in self._entries
                if (plan_id is None or e["plan_id"] == plan_id)
                and (event is None or e["event"] == event)]

    def verify(self) -> None:
        prev = ""
        for i, e in enumerate(self._entries):
            if e["seq"] != i or e["prev"] != prev or e["hash"] != self._hash(e):
                raise JournalError(f"journal integrity failure at seq {i}")
            prev = e["hash"]
