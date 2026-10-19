# CodexProbe

A small recording reverse proxy that sits between the [Codex CLI](https://github.com/openai/codex)
and any OpenAI-compatible LLM backend. It forwards every request untouched, relays streamed
responses live, and writes the complete request and response of every LLM call to a JSONL file —
one file per Codex session. The same proxy is the switch point for swapping the backend: changing
one JSON config file is enough to move Codex from a hosted API to a model running on your own
machine.

```
Codex CLI ──HTTP──▶ CodexProbe (127.0.0.1:8135/v1) ──HTTP──▶ real backend
                         │                                   (Z.AI, OpenAI,
                         ▼                                    local Ollama…)
                 logs/calls-<session>.jsonl
                 (every request + response, in order)
```

## Install

Requires Python 3.12+.

```bash
git clone <this repo> && cd codex-llm
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest   # 70 tests, no network or API key needed
```

## Usage

```python
from codex_probe import ProxyRecorder

config = {
    "upstream": {
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "wire_api": "chat",            # "chat" or "responses"
        "api_key_env": "ZAI_API_KEY",  # or "api_key": "..." directly
    },
    "listen": {"host": "127.0.0.1", "port": 8135},
    "log_dir": "logs",
}

recorder = ProxyRecorder(config)
endpoint = recorder.start()   # "http://127.0.0.1:8135/v1"
# point Codex at `endpoint` (next section), run `codex exec "<task>"`, then:
calls = recorder.stop()       # list[dict] — every call, in order
```

Or use the walkthrough script, which prints the Codex config block for you and a per-call summary
on Ctrl-C:

```bash
export ZAI_API_KEY="..."
.venv/bin/python examples/quickstart.py examples/config.zai.json
```

Three ready configs live in `examples/`: `config.zai.json`, `config.openai.json`,
`config.qwen.json` (local Ollama, no key). Swapping the backend is swapping the file — no code
changes.

An optional `"seed": <int>` at the top level makes runs more reproducible: the proxy adds that
seed to any JSON request that doesn't already carry one. Without it (the default), request bodies
are forwarded byte for byte.

## Pointing Codex at the recorder

Add a provider to `~/.codex/config.toml` (or pass the same values with `-c` flags):

```toml
model_provider = "codex_probe"
model = "glm-5.3"                      # whatever the backend serves

[model_providers.codex_probe]
name = "CodexProbe recorder"
base_url = "http://127.0.0.1:8135/v1"
wire_api = "chat"
```

Then run Codex normally:

```bash
codex exec "add a fahrenheit_to_celsius function to temperature.py"
```

**Codex version note.** Recent Codex CLI releases removed `wire_api = "chat"` for custom
providers — only the Responses API is accepted (hard error, see Codex discussion #7782). Hosted
OpenAI speaks Responses, but local servers (Ollama, and most OpenAI-compatible hosts) speak Chat
Completions. For the chat-based runs in this repo we pinned the last version that supports both:

```bash
npm i -g @openai/codex@0.80.0
```

CodexProbe itself handles both wire formats; the pin is purely a Codex-side constraint.

## Serving a local model

With Ollama (what we used; runs on an Apple Silicon Mac with 16 GB RAM, the 7B model takes
~4.7 GB):

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

Then start the recorder with `examples/config.qwen.json` — it forwards to
`http://127.0.0.1:11434/v1` with no API key.

With vLLM on a GPU box (one 16 GB+ VRAM GPU for the 7B model in bf16):

```bash
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --enable-auto-tool-choice --tool-call-parser hermes
```

`--enable-auto-tool-choice` matters: it makes vLLM emit structured `tool_calls`, which Codex
needs (see findings below).

## What a captured call looks like

One JSON object per line in `logs/calls-<timestamp>-<id>.jsonl`:

```json
{
  "index": 0,
  "started_at": "2026-10-19T11:17:01.513+00:00",
  "method": "POST",
  "path": "/v1/chat/completions",
  "request_body": {
    "model": "glm-5.3",
    "messages": [{"role": "system", "content": "You are a coding agent running in the Codex CLI..."},
                 {"role": "user", "content": "Add a fahrenheit_to_celsius function..."}],
    "tools": [{"type": "function", "function": {"name": "shell", "...": "..."}}, "..."],
    "stream": true
  },
  "response_status": 200,
  "response_body": {
    "object": "chat.completion",
    "choices": [{"message": {"role": "assistant",
                             "tool_calls": [{"function": {"name": "shell",
                                             "arguments": "{\"command\":[\"cat temperature.py\"]}"}}]},
                 "finish_reason": "tool_calls"}]
  },
  "streamed": true,
  "latency_ms": 7711.3,
  "backend_base_url": "https://api.z.ai/api/coding/paas/v4"
}
```

Nothing is truncated: the full system prompt, the whole running message history, and all tool
schemas are in every record. Streamed responses are relayed to Codex chunk by chunk while the
proxy keeps a copy; when the stream ends, the chunks are reassembled into the complete response
shown above. Request headers are deliberately not recorded, so API keys never reach the log
(a test enforces this).

Three real sessions are committed under `examples/sample_logs/`:

| file | backend | result |
|---|---|---|
| `zai-glm-5.3-codex-session.jsonl` | Z.AI glm-5.3 (hosted) | 4 calls, task completed: history grows 4→7→10→12 messages, 3 shell tool calls, final answer |
| `qwen2.5-coder-7b-local-session.jsonl` | local Ollama | 1 call — see findings |
| `gpt-oss-20b-local-session.jsonl` | local Ollama | 22 calls, 21 tool rounds — see findings |

## Findings: open models and Codex's tool calling

The backend swap itself worked exactly as designed — every call in the local logs carries
`backend_base_url: http://127.0.0.1:11434/v1`, and the swap was a config file, not code. What
broke, honestly reported, is on the model side:

- **qwen2.5-coder:7b (Ollama)** never emits a structured `tool_calls` array. It writes the tool
  call as JSON *text* inside `content` with `finish_reason: "stop"`. Codex reads that as a final
  answer and exits after one call without touching any file. This is a chat-template limitation
  of the Ollama build; the committed log shows it verbatim. vLLM with `--enable-auto-tool-choice`
  is the documented way to get real tool calls out of the same model.
- **qwen3:30b-a3b (Ollama)** answers in prose instead of calling a tool — same single-call dead
  end.
- **gpt-oss:20b (Ollama)** does speak structured tool calls and drove a real 21-round agent loop
  through the proxy — but it never follows Codex's edit workflow (`apply_patch` via the shell
  tool) to completion, and ends by asking what it should do.

So with a hosted coding model Codex completes the task end to end through the recorder; with
small local models the recorder faithfully captures exactly where and how their tool-calling
behavior breaks down. Both outcomes are visible turn by turn in the committed logs.

## Responses API vs Chat Completions

Codex speaks one of two wire formats, chosen by `wire_api`:

- **Chat Completions** (`POST /chat/completions`): the request carries a flat `messages` list;
  a streamed response is a series of `chat.completion.chunk` deltas that the client must merge —
  content arrives in fragments, and tool-call *arguments* arrive as split JSON strings.
  CodexProbe reassembles these into one normal `chat.completion` object for the log.
- **Responses** (`POST /responses`): the request carries `input` items; the stream is typed
  events (`response.created`, `response.output_text.delta`, …) and the final
  `response.completed` event already contains the entire response object, so the recorder simply
  takes that. If the stream dies early, it stores what arrived with `status: "incomplete"`.

OpenAI serves both; nearly every other OpenAI-compatible server (Ollama, vLLM, Z.AI) serves Chat
Completions only. The recorder picks its reassembly strategy from the `wire_api` config value.

## Development

```bash
.venv/bin/ruff check src tests examples
.venv/bin/mypy
.venv/bin/pytest
```

The test suite runs an in-process mock OpenAI server for passthrough and reassembly tests, and
real local uvicorn servers for the streaming-liveness and crash-recovery tests. No test needs a
network connection or an API key.
