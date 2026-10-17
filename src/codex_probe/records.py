from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CallRecord:
    index: int
    started_at: str
    method: str
    path: str
    request_body: dict[str, Any] | None
    response_status: int
    response_body: dict[str, Any] | None
    streamed: bool
    latency_ms: float
    backend_base_url: str

    def to_dict(self) -> dict[str, Any]:
        """Return the record as a JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CallRecord":
        """Rebuild a record from a dict produced by to_dict."""
        return cls(**raw)
