from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from codex_probe.config import ConfigError, ProbeConfig


def full_config() -> dict[str, Any]:
    return {
        "upstream": {
            "base_url": "https://api.example.com/v1/",
            "wire_api": "responses",
            "api_key": "sk-test",
        },
        "listen": {"host": "0.0.0.0", "port": 9000},
        "log_dir": "/tmp/probe-logs",
        "seed": 42,
    }


def test_full_config_round_trip() -> None:
    config = ProbeConfig.from_dict(full_config())
    assert config.upstream_base_url == "https://api.example.com/v1"
    assert config.wire_api == "responses"
    assert config.api_key == "sk-test"
    assert config.listen_host == "0.0.0.0"
    assert config.listen_port == 9000
    assert config.log_dir == Path("/tmp/probe-logs")
    assert config.seed == 42


def test_defaults_applied() -> None:
    config = ProbeConfig.from_dict({"upstream": {"base_url": "http://localhost:11434/v1"}})
    assert config.wire_api == "chat"
    assert config.api_key is None
    assert config.listen_host == "127.0.0.1"
    assert config.listen_port == 8135
    assert config.log_dir == Path("logs")
    assert config.seed is None


def test_api_key_resolved_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROBE_TEST_KEY", "sk-from-env")
    raw = {"upstream": {"base_url": "https://api.example.com", "api_key_env": "PROBE_TEST_KEY"}}
    assert ProbeConfig.from_dict(raw).api_key == "sk-from-env"


def test_missing_environment_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROBE_TEST_KEY", raising=False)
    raw = {"upstream": {"base_url": "https://api.example.com", "api_key_env": "PROBE_TEST_KEY"}}
    with pytest.raises(ConfigError, match="PROBE_TEST_KEY"):
        ProbeConfig.from_dict(raw)


def test_explicit_api_key_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROBE_TEST_KEY", "sk-from-env")
    raw = full_config()
    raw["upstream"]["api_key_env"] = "PROBE_TEST_KEY"
    assert ProbeConfig.from_dict(raw).api_key == "sk-test"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.pop("upstream"),
        lambda raw: raw["upstream"].pop("base_url"),
        lambda raw: raw["upstream"].update(base_url="ftp://api.example.com"),
        lambda raw: raw["upstream"].update(base_url=123),
        lambda raw: raw["upstream"].update(wire_api="grpc"),
        lambda raw: raw["upstream"].update(api_key=""),
        lambda raw: raw["listen"].update(port=0),
        lambda raw: raw["listen"].update(port=70000),
        lambda raw: raw["listen"].update(port="8135"),
        lambda raw: raw["listen"].update(port=True),
        lambda raw: raw.update(seed="42"),
    ],
)
def test_invalid_configs_rejected(mutation: Callable[[dict[str, Any]], object]) -> None:
    raw = full_config()
    mutation(raw)
    with pytest.raises(ConfigError):
        ProbeConfig.from_dict(raw)


def test_config_is_immutable() -> None:
    config = ProbeConfig.from_dict(full_config())
    with pytest.raises(AttributeError):
        config.listen_port = 1234  # type: ignore[misc]
