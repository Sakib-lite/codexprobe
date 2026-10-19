# CodexProbe

A reverse proxy that sits between the [Codex CLI](https://github.com/openai/codex) and any
OpenAI-compatible LLM backend. It forwards every request untouched, relays streamed responses
live, and saves the full request and response of every LLM call to a JSONL file, one file per
Codex session. Changing the backend is a config file change, not a code change.

> **Note:** I ran and tested this project with Z.AI's `glm-5.3` model, because that is the API key
> I have access to. The steps below show OpenAI, and the GLM commands are right after them. The
> flow is the same, only the key, config and model change.

```
Codex CLI ──▶ CodexProbe (127.0.0.1:8135/v1) ──▶ real backend (OpenAI, Z.AI, local Ollama)
                        │
                        ▼
              logs/calls-<session>.jsonl
```

## 1. Install

Requires Python 3.12+ and the Codex CLI.

```bash
git clone https://github.com/Sakib-lite/codexprobe.git
cd codexprobe
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
npm i -g @openai/codex@0.80.0
```

## 2. Run

Open two terminals in the project folder.

Terminal 1, start the recorder:

```bash
export OPENAI_API_KEY="your-key"
.venv/bin/python examples/quickstart.py examples/config.openai.json
```

Terminal 2, run a Codex task through it:

```bash
./examples/run_codex.sh gpt-5-codex "create a hello.py that prints Hello World" responses
```

When Codex finishes, press Ctrl-C in terminal 1. It prints a summary of every call, and the full
session is saved in `logs/calls-*.jsonl`.

`run_codex.sh` passes the provider settings to Codex as `-c` flags, so `~/.codex/config.toml` is
never changed.

To run it with GLM instead:

```bash
export ZAI_API_KEY="your-key"
.venv/bin/python examples/quickstart.py examples/config.zai.json
```

```bash
./examples/run_codex.sh glm-5.3 "create a hello.py that prints Hello World"
```

## 3. Test

```bash
.venv/bin/pytest
```

73 tests, no network or API key needed. They cover request/response passthrough against a mock
OpenAI server, streaming reassembly, and the log format.

To look at a real recorded session without running anything:

```bash
python3 -m json.tool < examples/sample_logs/zai-glm-5.3-codex-session.jsonl
```

## Usage in Python

```python
from codex_probe import ProxyRecorder

config = {
    "upstream": {
        "base_url": "https://api.openai.com/v1",
        "wire_api": "responses",
        "api_key_env": "OPENAI_API_KEY",
    },
    "listen": {"host": "127.0.0.1", "port": 8135},
    "log_dir": "logs",
}

recorder = ProxyRecorder(config)
endpoint = recorder.start()
calls = recorder.stop()
```

`endpoint` is the URL to give Codex as its provider `base_url`. `calls` is a list with one dict
per LLM call, in order. An optional top-level `"seed"` is added to requests that don't carry one.

## Codex version note

Codex releases after 0.80.0 removed `wire_api = "chat"` for custom providers. OpenAI uses
`responses` and works with any version, but Z.AI, Ollama and vLLM only speak `chat`, so this repo
pins Codex to 0.80.0.

## Local model (backend swap)

The same recorder can forward to a model on your own machine. With Ollama:

```bash
ollama serve
ollama pull qwen2.5-coder:7b
.venv/bin/python examples/quickstart.py examples/config.qwen.json
```

```bash
./examples/run_codex.sh qwen2.5-coder:7b "create a hello.py that prints Hello World"
```

The 7B model needs about 5 GB of RAM. On a GPU machine, vLLM works too:
`vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --enable-auto-tool-choice --tool-call-parser hermes`.

The swap itself works: every call in the log shows `backend_base_url: http://127.0.0.1:11434/v1`.
The local models did not finish the task, though:

- `qwen2.5-coder:7b` writes its tool call as plain text instead of a real tool call, so Codex has
  nothing to run and stops after one call.
- `gpt-oss:20b` makes real tool calls (22 calls in the log) but never finishes the file edit.

Both logs are in `examples/sample_logs/`.

## What a recorded call looks like

Each line in the log file is one call:

```json
{
  "index": 0,
  "started_at": "2026-10-19T11:17:01.513+00:00",
  "method": "POST",
  "path": "/v1/chat/completions",
  "request_body": {"model": "glm-5.3", "messages": ["..."], "tools": ["..."], "stream": true},
  "response_status": 200,
  "response_body": {"choices": [{"message": {"tool_calls": ["..."]}, "finish_reason": "tool_calls"}]},
  "streamed": true,
  "latency_ms": 7711.3,
  "backend_base_url": "https://api.z.ai/api/coding/paas/v4"
}
```

Nothing is cut: the full system prompt, the whole message history and every tool schema are
saved. Streamed responses go to Codex chunk by chunk while the proxy keeps a copy, then the
chunks are joined into one complete response for the log. Headers are not saved, so API keys
never end up in the log.

## Responses vs Chat Completions

- **Chat Completions** (`/chat/completions`): a streamed answer arrives as many small pieces,
  including tool call arguments split across pieces. The recorder joins them back into one
  response.
- **Responses** (`/responses`): the stream ends with a `response.completed` event that already
  holds the whole response, so the recorder just keeps that one.

The `wire_api` value in the config decides which one the recorder expects.
