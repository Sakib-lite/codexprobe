#!/usr/bin/env bash
# Run a real Codex task through CodexProbe without touching ~/.codex/config.toml.
# Usage: ./examples/run_codex.sh <model> "<task>"
# Example: ./examples/run_codex.sh qwen2.5-coder:7b "create a hello.py that prints Hello World"
set -euo pipefail

MODEL="${1:?usage: run_codex.sh <model> \"<task>\"}"
TASK="${2:?usage: run_codex.sh <model> \"<task>\"}"
ENDPOINT="http://127.0.0.1:8135/v1"
WIRE_API="chat"

mkdir -p /tmp/codex-test
cd /tmp/codex-test

codex exec --skip-git-repo-check -s workspace-write \
  -c "model_providers.codex_probe.name=\"CodexProbe\"" \
  -c "model_providers.codex_probe.base_url=\"$ENDPOINT\"" \
  -c "model_providers.codex_probe.wire_api=\"$WIRE_API\"" \
  -c 'model_provider="codex_probe"' \
  -c "model=\"$MODEL\"" \
  "$TASK"
