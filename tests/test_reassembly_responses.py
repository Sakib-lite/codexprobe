import json
from typing import Any

import pytest

from codex_probe.reassembly import (
    ChatCompletionsReassembler,
    ResponsesReassembler,
    reassembler_for,
)


def stream(*events: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def full_response() -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "model": "gpt-5",
        "output": [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "shell",
                "arguments": '{"command": ["ls"]}',
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Listing files."}],
            },
        ],
        "usage": {"input_tokens": 50, "output_tokens": 9, "total_tokens": 59},
    }


def test_completed_event_wins() -> None:
    raw = stream(
        {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
        {"type": "response.output_item.added", "item": {"type": "message"}},
        {"type": "response.output_text.delta", "delta": "Listing"},
        {"type": "response.output_text.delta", "delta": " files."},
        {"type": "response.completed", "response": full_response()},
    )
    assert ResponsesReassembler().reassemble(raw) == full_response()


def test_failed_event_also_counts_as_final() -> None:
    failed = {"id": "resp_1", "status": "failed", "error": {"message": "boom"}}
    raw = stream(
        {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
        {"type": "response.failed", "response": failed},
    )
    assert ResponsesReassembler().reassemble(raw) == failed


def test_interrupted_stream_keeps_partial_text() -> None:
    raw = stream(
        {"type": "response.created", "response": {"id": "resp_1", "model": "gpt-5"}},
        {"type": "response.output_text.delta", "delta": "Hel"},
        {"type": "response.output_text.delta", "delta": "lo"},
    )
    partial = ResponsesReassembler().reassemble(raw)
    assert partial == {
        "id": "resp_1",
        "model": "gpt-5",
        "status": "incomplete",
        "output_text": "Hello",
    }


def test_interrupted_stream_without_created_event() -> None:
    raw = stream({"type": "response.output_text.delta", "delta": "hi"})
    assert ResponsesReassembler().reassemble(raw) == {"status": "incomplete", "output_text": "hi"}


def test_empty_stream_reassembles_to_none() -> None:
    assert ResponsesReassembler().reassemble(b"") is None


def test_factory_picks_strategy_by_wire_api() -> None:
    assert isinstance(reassembler_for("chat"), ChatCompletionsReassembler)
    assert isinstance(reassembler_for("responses"), ResponsesReassembler)


def test_factory_rejects_unknown_wire_api() -> None:
    with pytest.raises(ValueError, match="grpc"):
        reassembler_for("grpc")
