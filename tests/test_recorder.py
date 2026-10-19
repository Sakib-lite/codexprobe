from pathlib import Path
from typing import Any

import httpx
import pytest

from codex_probe import ProxyRecorder
from tests.conftest import MOCK_COMPLETION, MockBackend, free_port, live_server

CHAT_REQUEST = {"model": "mock-model", "messages": [{"role": "user", "content": "hi"}]}


def recorder_config(backend_url: str, tmp_path: Path) -> dict[str, Any]:
    return {
        "upstream": {"base_url": f"{backend_url}/v1", "api_key": "sk-upstream"},
        "listen": {"host": "127.0.0.1", "port": free_port()},
        "log_dir": str(tmp_path / "logs"),
    }


def test_start_serves_endpoint_and_stop_returns_calls(backend: MockBackend, tmp_path: Path) -> None:
    with live_server(backend.app()) as backend_url:
        recorder = ProxyRecorder(recorder_config(backend_url, tmp_path))
        endpoint = recorder.start()
        assert endpoint.startswith("http://127.0.0.1:")
        assert endpoint.endswith("/v1")
        response = httpx.post(f"{endpoint}/chat/completions", json=CHAT_REQUEST)
        calls = recorder.stop()
    assert response.status_code == 200
    assert response.json() == MOCK_COMPLETION
    assert len(calls) == 1
    assert calls[0]["index"] == 0
    assert calls[0]["request_body"] == CHAT_REQUEST
    assert calls[0]["response_body"] == MOCK_COMPLETION
    assert calls[0]["streamed"] is False


def test_streamed_call_recorded_through_recorder(backend: MockBackend, tmp_path: Path) -> None:
    backend.stream_chunks = [
        b'data: {"id": "s", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    with live_server(backend.app()) as backend_url:
        recorder = ProxyRecorder(recorder_config(backend_url, tmp_path))
        endpoint = recorder.start()
        with httpx.stream(
            "POST", f"{endpoint}/chat/completions", json={**CHAT_REQUEST, "stream": True}
        ) as response:
            streamed_bytes = response.read()
        calls = recorder.stop()
    assert b"Hello" in streamed_bytes
    assert calls[0]["streamed"] is True
    message = calls[0]["response_body"]["choices"][0]["message"]
    assert message == {"role": "assistant", "content": "Hello"}


def test_each_start_opens_a_fresh_session_file(backend: MockBackend, tmp_path: Path) -> None:
    with live_server(backend.app()) as backend_url:
        config = recorder_config(backend_url, tmp_path)
        recorder = ProxyRecorder(config)
        for _ in range(2):
            endpoint = recorder.start()
            httpx.post(f"{endpoint}/chat/completions", json=CHAT_REQUEST)
            calls = recorder.stop()
            assert [call["index"] for call in calls] == [0]
    session_files = sorted((tmp_path / "logs").glob("calls-*.jsonl"))
    assert len(session_files) == 2


def test_stop_without_start_is_a_noop() -> None:
    recorder = ProxyRecorder({"upstream": {"base_url": "https://backend.test/v1", "api_key": "sk"}})
    assert recorder.stop() == []
    assert recorder.stop() == []


def test_start_twice_rejected(backend: MockBackend, tmp_path: Path) -> None:
    with live_server(backend.app()) as backend_url:
        recorder = ProxyRecorder(recorder_config(backend_url, tmp_path))
        recorder.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                recorder.start()
        finally:
            recorder.stop()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_start_fails_fast_when_port_is_taken(backend: MockBackend, tmp_path: Path) -> None:
    with live_server(backend.app()) as backend_url:
        config = recorder_config(backend_url, tmp_path)
        first = ProxyRecorder(config)
        second = ProxyRecorder(config)
        first.start()
        try:
            with pytest.raises(RuntimeError, match="failed to start"):
                second.start()
        finally:
            first.stop()
