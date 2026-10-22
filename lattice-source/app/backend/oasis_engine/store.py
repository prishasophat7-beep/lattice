"""Question history and answer feedback, kept on the machine as JSON-lines files.

    <data_dir>/history.jsonl    one line per answered question
    <data_dir>/feedback.jsonl   one line per "helpful" / "wrong" vote

Nothing leaves the machine; administrators export feedback from the app (Manage) to hand
to the web team, who can use it to fix sections, summaries or documents.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_READ = 500  # entries read back from the end of a log


class JsonlLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[int | None, tuple[tuple[int, int], list[dict]]] = {}  # limit -> (file stat, entries)

    def append(self, entry: dict) -> dict:
        entry = {"id": uuid.uuid4().hex[:12], "ts": datetime.now(timezone.utc).isoformat(), **entry}
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return entry

    def remove(self, matches) -> int:
        """Delete the entries for which matches(entry) is true (History: delete a conversation);
        returns how many. The file is rewritten whole and replaced in one step."""
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                return 0
            kept, removed = [], 0
            for line in lines:
                try:
                    hit = bool(line.strip()) and matches(json.loads(line))
                except ValueError:
                    hit = False
                if hit:
                    removed += 1
                else:
                    kept.append(line)
            if removed:
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
                tmp.replace(self.path)
            return removed

    def read(self, limit: int | None = MAX_READ) -> list[dict]:
        """Newest first; limit=None reads everything. Only the end of the file is read, so this
        stays fast as the log grows. Lines that do not parse (e.g. a write cut short by a crash)
        are skipped. Read again only when the file changed: one question looks at its conversation
        some seven times (2,000 entries took 230 ms to read each time on an 8 GB laptop). The
        entries are shared between callers, who must not change them."""
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return []
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(limit)
        if cached is not None and cached[0] == key:
            return list(cached[1])
        try:
            with open(self.path, "rb") as f:
                lines = _tail_lines(f, limit)
        except FileNotFoundError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        self._cache[limit] = (key, out)
        return list(out)


def _tail_lines(f, limit: int | None, chunk: int = 1 << 16) -> list[bytes]:
    """The last `limit` non-empty lines of a binary file (all of them when limit is None)."""
    f.seek(0, 2)
    pos, buf = f.tell(), b""
    while pos > 0 and (limit is None or buf.count(b"\n") <= limit):
        step = min(chunk, pos)
        pos -= step
        f.seek(pos)
        buf = f.read(step) + buf
    lines = [ln for ln in buf.split(b"\n") if ln.strip()]
    if pos > 0:  # the first line may be cut off in the middle
        lines = lines[1:]
    return lines if limit is None else lines[-limit:]
