import json
from pathlib import Path

from codex_probe.logbook import JsonlCallLog
from codex_probe.records import CallRecord


def make_record(index: int) -> CallRecord:
    return CallRecord(
        index=index,
        started_at=f"2026-10-16T10:00:0{index}+00:00",
        method="POST",
        path="/chat/completions",
        request_body={"model": "gpt-5", "messages": [{"role": "user", "content": f"q{index}"}]},
        response_status=200,
        response_body={"choices": [{"message": {"content": f"a{index} — বাংলা"}}]},
        streamed=index % 2 == 0,
        latency_ms=12.5 + index,
        backend_base_url="https://api.example.com/v1",
    )


def test_appended_records_round_trip_in_order(tmp_path: Path) -> None:
    log = JsonlCallLog(tmp_path, "session-1")
    written = [make_record(i) for i in range(3)]
    for record in written:
        log.append(record)
    assert log.load() == written


def test_file_has_one_valid_json_line_per_record(tmp_path: Path) -> None:
    log = JsonlCallLog(tmp_path, "session-1")
    for i in range(3):
        log.append(make_record(i))
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["index"] for line in lines] == [0, 1, 2]


def test_unicode_stored_readably(tmp_path: Path) -> None:
    log = JsonlCallLog(tmp_path, "session-1")
    log.append(make_record(0))
    assert "বাংলা" in log.path.read_text(encoding="utf-8")


def test_each_session_gets_its_own_file(tmp_path: Path) -> None:
    first = JsonlCallLog(tmp_path, "session-1")
    second = JsonlCallLog(tmp_path, "session-2")
    first.append(make_record(0))
    second.append(make_record(1))
    assert first.path != second.path
    assert [record.index for record in first.load()] == [0]
    assert [record.index for record in second.load()] == [1]


def test_load_without_any_append_returns_empty(tmp_path: Path) -> None:
    assert JsonlCallLog(tmp_path, "empty-session").load() == []


def test_log_dir_created_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "logs"
    log = JsonlCallLog(nested, "session-1")
    log.append(make_record(0))
    assert nested.is_dir()
    assert log.path.parent == nested


def test_records_survive_reopening_the_log(tmp_path: Path) -> None:
    JsonlCallLog(tmp_path, "session-1").append(make_record(0))
    reopened = JsonlCallLog(tmp_path, "session-1")
    reopened.append(make_record(1))
    assert [record.index for record in reopened.load()] == [0, 1]
