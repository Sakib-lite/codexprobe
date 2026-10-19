import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
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
        self.stream_chunks: list[bytes] | None = None
        self.stream_delay = 0.0
        self.fail_after: int | None = None

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/{path:path}", self._handle, methods=["GET", "POST"])])

    async def _handle(self, request: Request) -> Response:
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
                "headers": dict(request.headers),
                "body": await request.body(),
            }
        )
        if self.stream_chunks is not None:
            return StreamingResponse(
                self._stream(self.stream_chunks),
                status_code=self.status_code,
                media_type="text/event-stream",
                headers=self.headers,
            )
        return JSONResponse(self.body, status_code=self.status_code, headers=self.headers)

    async def _stream(self, chunks: list[bytes]) -> AsyncIterator[bytes]:
        for position, chunk in enumerate(chunks):
            if self.fail_after is not None and position >= self.fail_after:
                raise RuntimeError("backend dropped the stream")
            if self.stream_delay:
                await asyncio.sleep(self.stream_delay)
            yield chunk


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


@contextmanager
def live_server(app: Starlette) -> Iterator[str]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("test server did not start in time")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def make_proxy(backend: MockBackend, log: JsonlCallLog) -> MakeProxy:
    def _make(config: ProbeConfig | None = None) -> httpx.AsyncClient:
        upstream_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=backend.app()))
        app = build_app(config or default_config(), upstream_client, log)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://probe.test"
        )

    return _make
