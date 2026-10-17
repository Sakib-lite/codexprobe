import json
from typing import Any

from codex_probe.config import ProbeConfig
from codex_probe.logbook import JsonlCallLog
from tests.conftest import MOCK_COMPLETION, MakeProxy, MockBackend

CHAT_REQUEST: dict[str, Any] = {
    "model": "mock-model",
    "stream": False,
    "messages": [
        {"role": "system", "content": "You are Codex."},
        {"role": "user", "content": "list the files"},
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            },
        }
    ],
}


async def test_request_body_reaches_backend_unchanged(
    backend: MockBackend, make_proxy: MakeProxy
) -> None:
    async with make_proxy() as client:
        await client.post("/v1/chat/completions", json=CHAT_REQUEST)
    received = backend.requests[0]
    assert json.loads(received["body"]) == CHAT_REQUEST
    assert received["method"] == "POST"
    assert received["path"] == "/v1/chat/completions"


async def test_response_relayed_unchanged(make_proxy: MakeProxy) -> None:
    async with make_proxy() as client:
        response = await client.post("/v1/chat/completions", json=CHAT_REQUEST)
    assert response.status_code == 200
    assert response.json() == MOCK_COMPLETION
    assert response.headers["x-request-id"] == "req-mock-1"


async def test_error_response_passed_through(
    backend: MockBackend, make_proxy: MakeProxy, log: JsonlCallLog
) -> None:
    backend.status_code = 401
    backend.body = {"error": {"message": "bad key", "type": "invalid_request_error"}}
    async with make_proxy() as client:
        response = await client.post("/v1/chat/completions", json=CHAT_REQUEST)
    assert response.status_code == 401
    assert response.json() == backend.body
    assert log.load()[0].response_status == 401


async def test_configured_api_key_replaces_client_auth(
    backend: MockBackend, make_proxy: MakeProxy
) -> None:
    async with make_proxy() as client:
        await client.post(
            "/v1/chat/completions",
            json=CHAT_REQUEST,
            headers={"authorization": "Bearer dummy-from-codex"},
        )
    assert backend.requests[0]["headers"]["authorization"] == "Bearer sk-upstream"


async def test_client_auth_forwarded_when_no_key_configured(
    backend: MockBackend, make_proxy: MakeProxy
) -> None:
    keyless = ProbeConfig.from_dict({"upstream": {"base_url": "https://backend.test/v1"}})
    async with make_proxy(keyless) as client:
        await client.post(
            "/v1/chat/completions",
            json=CHAT_REQUEST,
            headers={"authorization": "Bearer dummy-from-codex"},
        )
    assert backend.requests[0]["headers"]["authorization"] == "Bearer dummy-from-codex"


async def test_query_string_forwarded(backend: MockBackend, make_proxy: MakeProxy) -> None:
    async with make_proxy() as client:
        await client.get("/v1/models?limit=5")
    received = backend.requests[0]
    assert received["path"] == "/v1/models"
    assert received["query"] == "limit=5"


async def test_calls_recorded_fully_and_in_order(make_proxy: MakeProxy, log: JsonlCallLog) -> None:
    async with make_proxy() as client:
        await client.post("/v1/chat/completions", json=CHAT_REQUEST)
        await client.post("/v1/chat/completions", json=CHAT_REQUEST)
    records = log.load()
    assert [record.index for record in records] == [0, 1]
    first = records[0]
    assert first.request_body == CHAT_REQUEST
    assert first.response_body == MOCK_COMPLETION
    assert first.streamed is False
    assert first.latency_ms >= 0
    assert first.backend_base_url == "https://backend.test/v1"
    assert first.path == "/v1/chat/completions"
