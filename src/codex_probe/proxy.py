import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Lifespan

from codex_probe.config import ProbeConfig
from codex_probe.logbook import CallLog
from codex_probe.reassembly import Reassembler, reassembler_for
from codex_probe.records import CallRecord

DROPPED_REQUEST_HEADERS = frozenset({"host", "content-length", "accept-encoding", "connection"})
DROPPED_RESPONSE_HEADERS = frozenset(
    {"content-length", "content-encoding", "transfer-encoding", "connection"}
)
EVENT_STREAM_CONTENT_TYPE = "text/event-stream"


class RecordingProxy:
    def __init__(
        self,
        config: ProbeConfig,
        client: httpx.AsyncClient,
        log: CallLog,
        reassembler: Reassembler,
    ) -> None:
        self._config = config
        self._client = client
        self._log = log
        self._reassembler = reassembler
        self._index = 0

    async def handle(self, request: Request) -> Response:
        index = self._next_index()
        started_at = datetime.now(UTC).isoformat()
        clock_start = time.perf_counter()
        request_body = await request.body()
        upstream_request = self._client.build_request(
            request.method,
            self._upstream_url(request),
            content=request_body,
            headers=self._upstream_headers(request),
        )
        try:
            upstream = await self._client.send(upstream_request, stream=True)
        except httpx.HTTPError as error:
            return self._upstream_unreachable(
                index, started_at, request, request_body, error, clock_start
            )
        if _is_event_stream(upstream):
            relay = self._relay_recording(
                index, started_at, request, request_body, upstream, clock_start
            )
            return StreamingResponse(
                relay, status_code=upstream.status_code, headers=_relayed_headers(upstream)
            )
        content = await upstream.aread()
        await upstream.aclose()
        self._record(
            index=index,
            started_at=started_at,
            request=request,
            request_body=request_body,
            response_status=upstream.status_code,
            response_body=_json_body(content),
            streamed=False,
            latency_ms=_elapsed_ms(clock_start),
        )
        return Response(
            content=content,
            status_code=upstream.status_code,
            headers=_relayed_headers(upstream),
        )

    async def _relay_recording(
        self,
        index: int,
        started_at: str,
        request: Request,
        request_body: bytes,
        upstream: httpx.Response,
        clock_start: float,
    ) -> AsyncIterator[bytes]:
        received: list[bytes] = []
        try:
            async for chunk in upstream.aiter_bytes():
                received.append(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            self._record(
                index=index,
                started_at=started_at,
                request=request,
                request_body=request_body,
                response_status=upstream.status_code,
                response_body=self._reassembler.reassemble(b"".join(received)),
                streamed=True,
                latency_ms=_elapsed_ms(clock_start),
            )

    def _upstream_unreachable(
        self,
        index: int,
        started_at: str,
        request: Request,
        request_body: bytes,
        error: httpx.HTTPError,
        clock_start: float,
    ) -> Response:
        body = {"error": {"message": str(error), "type": "upstream_connection_error"}}
        self._record(
            index=index,
            started_at=started_at,
            request=request,
            request_body=request_body,
            response_status=502,
            response_body=body,
            streamed=False,
            latency_ms=_elapsed_ms(clock_start),
        )
        return Response(content=json.dumps(body), status_code=502, media_type="application/json")

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
        response_status: int,
        response_body: dict[str, Any] | None,
        streamed: bool,
        latency_ms: float,
    ) -> None:
        self._log.append(
            CallRecord(
                index=index,
                started_at=started_at,
                method=request.method,
                path=request.url.path,
                request_body=_json_body(request_body),
                response_status=response_status,
                response_body=response_body,
                streamed=streamed,
                latency_ms=latency_ms,
                backend_base_url=self._config.upstream_base_url,
            )
        )


def build_app(
    config: ProbeConfig,
    client: httpx.AsyncClient,
    log: CallLog,
    lifespan: Lifespan[Starlette] | None = None,
) -> Starlette:
    proxy = RecordingProxy(config, client, log, reassembler_for(config.wire_api))
    routes = [Route("/v1/{path:path}", proxy.handle, methods=["GET", "POST"])]
    return Starlette(routes=routes, lifespan=lifespan)


def _is_event_stream(upstream: httpx.Response) -> bool:
    content_type: str = upstream.headers.get("content-type", "")
    return content_type.startswith(EVENT_STREAM_CONTENT_TYPE)


def _elapsed_ms(clock_start: float) -> float:
    return round((time.perf_counter() - clock_start) * 1000, 3)


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
