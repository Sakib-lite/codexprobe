import json
import time
from typing import Any

import httpx
import pytest

from codex_probe.config import ProbeConfig
from codex_probe.logbook import JsonlCallLog
from codex_probe.proxy import build_app
from codex_probe.records import CallRecord
from tests.conftest import MakeProxy, MockBackend, live_server

STREAM_REQUEST = {
    "model": "mock-model",
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
}


def sse_chunk(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def chat_chunks() -> list[bytes]:
    base = {
        "id": "chatcmpl-s",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "mock-model",
    }
    return [
        sse_chunk(
            {
                **base,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        ),
        sse_chunk(
            {**base, "choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]}
        ),
        sse_chunk(
            {**base, "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}]}
        ),
        sse_chunk({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n\n",
    ]


async def test_stream_relayed_byte_identical(backend: MockBackend, make_proxy: MakeProxy) -> None:
    backend.stream_chunks = chat_chunks()
    async with (
        make_proxy() as client,
        client.stream("POST", "/v1/chat/completions", json=STREAM_REQUEST) as response,
    ):
        relayed = b"".join([chunk async for chunk in response.aiter_bytes()])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert relayed == b"".join(chat_chunks())


async def test_chunks_arrive_live_not_buffered(backend: MockBackend, log: JsonlCallLog) -> None:
    backend.stream_chunks = chat_chunks()
    backend.stream_delay = 0.05
    arrivals: list[float] = []
    with live_server(backend.app()) as backend_url:
        config = ProbeConfig.from_dict(
            {"upstream": {"base_url": f"{backend_url}/v1", "api_key": "sk-upstream"}}
        )
        upstream_client = httpx.AsyncClient()
        with live_server(build_app(config, upstream_client, log)) as proxy_url:
            async with (
                httpx.AsyncClient() as client,
                client.stream(
                    "POST", f"{proxy_url}/v1/chat/completions", json=STREAM_REQUEST
                ) as response,
            ):
                async for _ in response.aiter_bytes():
                    arrivals.append(time.perf_counter())
    assert len(arrivals) >= 2
    assert arrivals[-1] - arrivals[0] >= 0.1


async def test_streamed_call_recorded_reassembled(
    backend: MockBackend, make_proxy: MakeProxy, log: JsonlCallLog
) -> None:
    backend.stream_chunks = chat_chunks()
    async with (
        make_proxy() as client,
        client.stream("POST", "/v1/chat/completions", json=STREAM_REQUEST) as response,
    ):
        await response.aread()
    record = log.load()[0]
    assert record.streamed is True
    assert record.request_body == STREAM_REQUEST
    assert record.response_status == 200
    assert record.response_body is not None
    message = record.response_body["choices"][0]["message"]
    assert message == {"role": "assistant", "content": "Hello"}
    assert record.response_body["choices"][0]["finish_reason"] == "stop"


async def test_broken_stream_still_logs_what_arrived(
    backend: MockBackend, log: JsonlCallLog
) -> None:
    backend.stream_chunks = chat_chunks()
    backend.fail_after = 2
    with live_server(backend.app()) as backend_url:
        config = ProbeConfig.from_dict(
            {"upstream": {"base_url": f"{backend_url}/v1", "api_key": "sk-upstream"}}
        )
        upstream_client = httpx.AsyncClient()
        with (
            live_server(build_app(config, upstream_client, log)) as proxy_url,
            pytest.raises(httpx.HTTPError),
        ):
            async with (
                httpx.AsyncClient() as client,
                client.stream(
                    "POST", f"{proxy_url}/v1/chat/completions", json=STREAM_REQUEST
                ) as response,
            ):
                await response.aread()
    record = _wait_for_record(log)
    assert record.streamed is True
    assert record.response_body is not None
    assert record.response_body["choices"][0]["message"]["role"] == "assistant"


def _wait_for_record(log: JsonlCallLog) -> CallRecord:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        records = log.load()
        if records:
            return records[0]
        time.sleep(0.01)
    raise AssertionError("no call was recorded")


async def test_responses_wire_api_recorded_via_responses_reassembler(
    backend: MockBackend, make_proxy: MakeProxy, log: JsonlCallLog
) -> None:
    final = {"id": "resp_1", "status": "completed", "output": [{"type": "message"}]}
    backend.stream_chunks = [
        sse_chunk({"type": "response.created", "response": {"id": "resp_1"}}),
        sse_chunk({"type": "response.output_text.delta", "delta": "hi"}),
        sse_chunk({"type": "response.completed", "response": final}),
    ]
    config = ProbeConfig.from_dict(
        {
            "upstream": {
                "base_url": "https://backend.test/v1",
                "wire_api": "responses",
                "api_key": "sk-upstream",
            }
        }
    )
    async with (
        make_proxy(config) as client,
        client.stream("POST", "/v1/responses", json=STREAM_REQUEST) as response,
    ):
        await response.aread()
    record = log.load()[0]
    assert record.streamed is True
    assert record.response_body == final


async def test_non_streaming_response_untouched_by_streaming_path(
    backend: MockBackend, make_proxy: MakeProxy, log: JsonlCallLog
) -> None:
    async with make_proxy() as client:
        response = await client.post("/v1/chat/completions", json=STREAM_REQUEST)
    assert response.json() == backend.body
    assert log.load()[0].streamed is False
