import json
from pathlib import Path

import pytest

from codex_probe.records import CallRecord

SAMPLE_DIR = Path(__file__).parent.parent / "examples" / "sample_logs"
SAMPLE_FILES = sorted(SAMPLE_DIR.glob("*.jsonl"))


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=lambda path: path.name)
def test_sample_session_is_a_valid_ordered_call_log(path: Path) -> None:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [CallRecord.from_dict(json.loads(line)) for line in lines]
    assert records
    assert [record.index for record in records] == list(range(len(records)))
    for record in records:
        assert record.method == "POST"
        assert record.path.startswith("/v1/")
        assert record.request_body is not None
        assert record.request_body.get("messages") or record.request_body.get("input")
        assert record.request_body.get("tools")
        assert record.response_body is not None
        assert record.latency_ms > 0
        assert record.backend_base_url.startswith("http")


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=lambda path: path.name)
def test_sample_session_contains_no_auth_material(path: Path) -> None:
    raw = path.read_text(encoding="utf-8").lower()
    assert "authorization" not in raw
    assert "bearer " not in raw
