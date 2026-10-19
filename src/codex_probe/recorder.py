import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette

from codex_probe.config import ProbeConfig
from codex_probe.logbook import JsonlCallLog
from codex_probe.proxy import build_app

UPSTREAM_TIMEOUT = httpx.Timeout(600.0, connect=10.0)
STARTUP_DEADLINE_SECONDS = 10.0
SHUTDOWN_DEADLINE_SECONDS = 10.0


class ProxyRecorder:
    """Local recording proxy between the Codex CLI and an OpenAI-compatible backend."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = ProbeConfig.from_dict(config)
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._log: JsonlCallLog | None = None

    def start(self) -> str:
        """Start the proxy and return the base url for codex's model provider config."""
        if self._server is not None:
            raise RuntimeError("recorder is already running")
        self._log = JsonlCallLog(self._config.log_dir, _new_session_id())
        app = self._build_session_app(self._log)
        server_config = uvicorn.Config(
            app,
            host=self._config.listen_host,
            port=self._config.listen_port,
            log_level="warning",
        )
        self._server = uvicorn.Server(server_config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        self._wait_until_started()
        return f"http://{self._config.listen_host}:{self._config.listen_port}/v1"

    def stop(self) -> list[dict[str, Any]]:
        """Stop the proxy and return this session's recorded calls, in order."""
        if self._server is None or self._thread is None or self._log is None:
            return []
        self._server.should_exit = True
        self._thread.join(timeout=SHUTDOWN_DEADLINE_SECONDS)
        calls = [record.to_dict() for record in self._log.load()]
        self._server = None
        self._thread = None
        self._log = None
        return calls

    def _build_session_app(self, log: JsonlCallLog) -> Starlette:
        client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)

        @asynccontextmanager
        async def close_client_on_shutdown(_: Starlette) -> AsyncIterator[None]:
            try:
                yield
            finally:
                await client.aclose()

        return build_app(self._config, client, log, lifespan=close_client_on_shutdown)

    def _wait_until_started(self) -> None:
        if self._server is None or self._thread is None:
            raise RuntimeError("recorder is not starting")
        deadline = time.monotonic() + STARTUP_DEADLINE_SECONDS
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                self._server = None
                self._thread = None
                raise RuntimeError(
                    f"proxy failed to start on "
                    f"{self._config.listen_host}:{self._config.listen_port}"
                )
            time.sleep(0.01)


def _new_session_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
