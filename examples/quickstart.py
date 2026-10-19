import json
import sys
import time
from pathlib import Path
from typing import Any

from codex_probe import ProxyRecorder

DEFAULT_CONFIG = "config.zai.json"

CODEX_PROVIDER_TEMPLATE = """\
# add to ~/.codex/config.toml, then run: codex exec "<task>"
model_provider = "codex_probe"
model = "{model_hint}"

[model_providers.codex_probe]
name = "CodexProbe recorder"
base_url = "{endpoint}"
wire_api = "{wire_api}"
env_key = "CODEX_PROBE_KEY"
"""

MODEL_HINTS = {"chat": "glm-5.3", "responses": "gpt-5-codex"}


def main() -> None:
    config = load_config(sys.argv[1:])
    recorder = ProxyRecorder(config)
    endpoint = recorder.start()
    wire_api = str(config["upstream"].get("wire_api", "chat"))
    print(f"recorder listening on {endpoint}")
    print(f"forwarding to {config['upstream']['base_url']}\n")
    print(
        CODEX_PROVIDER_TEMPLATE.format(
            endpoint=endpoint, wire_api=wire_api, model_hint=MODEL_HINTS[wire_api]
        )
    )
    print("run codex in another terminal, press Ctrl-C here when done")
    wait_for_interrupt()
    calls = recorder.stop()
    print(f"\nrecorded {len(calls)} calls -> {config.get('log_dir', 'logs')}/")
    for call in calls:
        print(summary_line(call))


def load_config(args: list[str]) -> dict[str, Any]:
    path = Path(args[0]) if args else Path(__file__).with_name(DEFAULT_CONFIG)
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def wait_for_interrupt() -> None:
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass


def summary_line(call: dict[str, Any]) -> str:
    kind = "stream" if call["streamed"] else "json"
    return (
        f"  [{call['index']}] {call['method']} {call['path']} "
        f"-> {call['response_status']} ({kind}, {call['latency_ms']:.0f} ms)"
    )


if __name__ == "__main__":
    main()
