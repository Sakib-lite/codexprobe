import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WIRE_APIS = ("chat", "responses")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8135
DEFAULT_LOG_DIR = "logs"


class ConfigError(ValueError):
    """Raised when the probe configuration dict is invalid."""


@dataclass(frozen=True)
class ProbeConfig:
    """Validated, immutable configuration for a ProxyRecorder."""

    upstream_base_url: str
    wire_api: str
    api_key: str | None
    listen_host: str
    listen_port: int
    log_dir: Path
    seed: int | None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProbeConfig":
        """Build a ProbeConfig from a plain config dict, validating every field."""
        upstream = _require_section(raw, "upstream")
        return cls(
            upstream_base_url=_parse_base_url(upstream),
            wire_api=_parse_wire_api(upstream),
            api_key=_resolve_api_key(upstream),
            listen_host=str(raw.get("listen", {}).get("host", DEFAULT_HOST)),
            listen_port=_parse_port(raw.get("listen", {})),
            log_dir=Path(str(raw.get("log_dir", DEFAULT_LOG_DIR))),
            seed=_parse_seed(raw),
        )


def _require_section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    section = raw.get(key)
    if not isinstance(section, dict):
        raise ConfigError(f"config must contain an '{key}' section")
    return section


def _parse_base_url(upstream: dict[str, Any]) -> str:
    base_url = upstream.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
        raise ConfigError("upstream.base_url must be an http(s) URL")
    return base_url.rstrip("/")


def _parse_wire_api(upstream: dict[str, Any]) -> str:
    wire_api = upstream.get("wire_api", "chat")
    if wire_api not in WIRE_APIS:
        raise ConfigError(f"upstream.wire_api must be one of {WIRE_APIS}, got {wire_api!r}")
    return str(wire_api)


def _resolve_api_key(upstream: dict[str, Any]) -> str | None:
    api_key = upstream.get("api_key")
    if api_key is not None:
        if not isinstance(api_key, str) or not api_key:
            raise ConfigError("upstream.api_key must be a non-empty string")
        return api_key
    env_name = upstream.get("api_key_env")
    if env_name is None:
        return None
    value = os.environ.get(str(env_name))
    if not value:
        raise ConfigError(f"environment variable {env_name!r} is not set")
    return value


def _parse_port(listen: dict[str, Any]) -> int:
    port = listen.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError(f"listen.port must be an integer in 1..65535, got {port!r}")
    return port


def _parse_seed(raw: dict[str, Any]) -> int | None:
    seed = raw.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise ConfigError(f"seed must be an integer, got {seed!r}")
    return seed


# NOTE: turns the raw config dict into a frozen object. all the validation happens here once
