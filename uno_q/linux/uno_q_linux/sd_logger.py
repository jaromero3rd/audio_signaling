"""Offline JSONL logger + optional backlog flush when ONLINE again."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator


class SdLogger:
    def __init__(self, directory: str | Path, filename: str = "telemetry.jsonl") -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / filename
        self._offset_path = self.dir / f".{filename}.flush_offset"

    def append(self, record: dict[str, Any]) -> Path:
        line = json.dumps(record, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return self.path

    def iter_unflushed(self) -> Iterator[tuple[int, dict[str, Any]]]:
        if not self.path.is_file():
            return
        start = self._read_offset()
        with self.path.open("r", encoding="utf-8") as f:
            f.seek(start)
            while True:
                pos = f.tell()
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    yield pos, json.loads(line)
                except json.JSONDecodeError:
                    continue

    def mark_flushed_through(self, file_pos_after_line: int) -> None:
        self._offset_path.write_text(str(file_pos_after_line), encoding="utf-8")

    def flush_to(
        self,
        send: Callable[[dict[str, Any]], bool],
        *,
        max_lines: int = 200,
    ) -> int:
        """Best-effort POST of backlog. Returns count successfully sent."""
        sent = 0
        last_pos = self._read_offset()
        for pos, record in self.iter_unflushed():
            if sent >= max_lines:
                break
            record = dict(record)
            record.setdefault("meta", {})
            if isinstance(record["meta"], dict):
                record["meta"]["flushed_at"] = time.time()
                record["meta"]["backlog"] = True
            if not send(record):
                break
            # advance past this line: reopen to find end of line is awkward;
            # store position of next read by re-seeking
            with self.path.open("r", encoding="utf-8") as f:
                f.seek(pos)
                f.readline()
                last_pos = f.tell()
            self.mark_flushed_through(last_pos)
            sent += 1
        return sent

    def _read_offset(self) -> int:
        if not self._offset_path.is_file():
            return 0
        try:
            return int(self._offset_path.read_text().strip() or "0")
        except ValueError:
            return 0
