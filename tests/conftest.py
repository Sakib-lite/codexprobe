from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from codex_probe.config import ProbeConfig
from codex_probe.logbook import JsonlCallLog
from codex_probe.proxy import build_app

MakeProxy = Callable[..., httpx.AsyncClient]

MOCK_COMPLETION = {
    "id": "chatcmpl-mock",
    "object": "chat.completion",
    "created": 1760700000,
    "model": "mock-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "mock answer"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
}


class MockBackend:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status_code = 200
        self.body: dict[str, Any] = dict(MOCK_COMPLETION)
        self.headers: dict[str, str] = {"x-request-id": "req-mock-1"}

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/{path:path}", self._handle, methods=["GET", "POST"])])

    async def _handle(self, request: Request) -> JSONResponse:
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
                "headers": dict(request.headers),
                "body": await request.body(),
            }
        )
        return JSONResponse(self.body, status_code=self.status_code, headers=self.headers)


def default_config() -> ProbeConfig:
    return ProbeConfig.from_dict(
        {"upstream": {"base_url": "https://backend.test/v1", "api_key": "sk-upstream"}}
    )


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend()


@pytest.fixture
def log(tmp_path: Path) -> JsonlCallLog:
    return JsonlCallLog(tmp_path, "test-session")


@pytest.fixture
def make_proxy(backend: MockBackend, log: JsonlCallLog) -> MakeProxy:
    def _make(config: ProbeConfig | None = None) -> httpx.AsyncClient:
        upstream_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=backend.app()))
        app = build_app(config or default_config(), upstream_client, log)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://probe.test"
        )

    return _make
