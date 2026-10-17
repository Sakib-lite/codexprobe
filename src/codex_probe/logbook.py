import json
from pathlib import Path
from typing import Protocol

from codex_probe.records import CallRecord


class CallLog(Protocol):
    def append(self, record: CallRecord) -> None: ...

    def load(self) -> list[CallRecord]: ...


class JsonlCallLog:
    def __init__(self, log_dir: Path, session_id: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self._path = log_dir / f"calls-{session_id}.jsonl"

    @property
    def path(self) -> Path:
        """Location of this session's log file."""
        return self._path

    def append(self, record: CallRecord) -> None:
        """Append one call to the session file immediately."""
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def load(self) -> list[CallRecord]:
        """Read back every call recorded in this session, in order."""
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as file:
            return [CallRecord.from_dict(json.loads(line)) for line in file if line.strip()]
