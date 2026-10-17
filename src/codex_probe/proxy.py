import json
import time
from datetime import UTC, datetime
from typing import Any
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from codex_probe.config import ProbeConfig
from codex_probe.logbook import CallLog
from codex_probe.records import CallRecord

DROPPED_REQUEST_HEADERS = frozenset({"host", "content-length", "accept-encoding", "connection"})
DROPPED_RESPONSE_HEADERS = frozenset(
    {"content-length", "content-encoding", "transfer-encoding", "connection"}
)


class RecordingProxy:
    def __init__(self, config: ProbeConfig, client: httpx.AsyncClient, log: CallLog) -> None:
        self._config = config
        self._client = client
        self._log = log
        self._index = 0

    async def handle(self, request: Request) -> Response:
        index = self._next_index()
        started_at = datetime.now(UTC).isoformat()
        clock_start = time.perf_counter()
        request_body = await request.body()
        upstream = await self._client.request(
            request.method,
            self._upstream_url(request),
            content=request_body,
            headers=self._upstream_headers(request),
        )
        latency_ms = round((time.perf_counter() - clock_start) * 1000, 3)
        self._record(index, started_at, request, request_body, upstream, latency_ms)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=_relayed_headers(upstream),
        )

    def _next_index(self) -> int:
        index = self._index
        self._index += 1
        return index

    def _upstream_url(self, request: Request) -> str:
        url = f"{self._config.upstream_base_url}/{request.path_params['path']}"
        if request.url.query:
            return f"{url}?{request.url.query}"
        return url

    def _upstream_headers(self, request: Request) -> dict[str, str]:
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in DROPPED_REQUEST_HEADERS
        }
        if self._config.api_key is not None:
            headers["authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _record(
        self,
        index: int,
        started_at: str,
        request: Request,
        request_body: bytes,
        upstream: httpx.Response,
        latency_ms: float,
    ) -> None:
        self._log.append(
            CallRecord(
                index=index,
                started_at=started_at,
                method=request.method,
                path=request.url.path,
                request_body=_json_body(request_body),
                response_status=upstream.status_code,
                response_body=_json_body(upstream.content),
                streamed=False,
                latency_ms=latency_ms,
                backend_base_url=self._config.upstream_base_url,
            )
        )


def build_app(config: ProbeConfig, client: httpx.AsyncClient, log: CallLog) -> Starlette:
    proxy = RecordingProxy(config, client, log)
    return Starlette(routes=[Route("/v1/{path:path}", proxy.handle, methods=["GET", "POST"])])


def _relayed_headers(upstream: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in DROPPED_RESPONSE_HEADERS
    }


def _json_body(body: bytes) -> dict[str, Any] | None:
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return {"unparsed": body.decode("utf-8", errors="replace")}
    if isinstance(parsed, dict):
        return parsed
    return {"unparsed_json": parsed}
