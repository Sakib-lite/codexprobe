import json
from typing import Any

from codex_probe.reassembly import ChatCompletionsReassembler

BASE = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1760600000, "model": "m"}


def chunk(*choices: dict[str, Any], **extra: object) -> dict[str, Any]:
    return {**BASE, "choices": list(choices), **extra}


def delta(index: int = 0, finish_reason: str | None = None, **fields: object) -> dict[str, Any]:
    return {"index": index, "delta": fields, "finish_reason": finish_reason}


def stream(*chunks: dict[str, Any]) -> bytes:
    events = [f"data: {json.dumps(part)}\n\n" for part in chunks]
    return ("".join(events) + "data: [DONE]\n\n").encode()


def test_text_answer_reassembled() -> None:
    raw = stream(
        chunk(delta(role="assistant")),
        chunk(delta(content="Hel")),
        chunk(delta(content="lo!")),
        chunk(delta(finish_reason="stop")),
    )
    completion = ChatCompletionsReassembler().reassemble(raw)
    assert completion == {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1760600000,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
    }


def test_fragmented_tool_call_arguments_joined() -> None:
    raw = stream(
        chunk(delta(role="assistant")),
        chunk(
            delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "run_shell", "arguments": ""},
                    }
                ]
            )
        ),
        chunk(delta(tool_calls=[{"index": 0, "function": {"arguments": '{"cmd": '}}])),
        chunk(delta(tool_calls=[{"index": 0, "function": {"arguments": '"ls -la"}'}}])),
        chunk(delta(finish_reason="tool_calls")),
    )
    completion = ChatCompletionsReassembler().reassemble(raw)
    assert completion is not None
    message = completion["choices"][0]["message"]
    assert message["content"] is None
    assert message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "run_shell", "arguments": '{"cmd": "ls -la"}'},
        }
    ]
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"cmd": "ls -la"}
    assert completion["choices"][0]["finish_reason"] == "tool_calls"


def test_parallel_tool_calls_kept_separate() -> None:
    raw = stream(
        chunk(
            delta(
                tool_calls=[
                    {"index": 0, "id": "call_a", "function": {"name": "read", "arguments": "{}"}},
                    {"index": 1, "id": "call_b", "function": {"name": "write", "arguments": "{}"}},
                ]
            )
        ),
    )
    completion = ChatCompletionsReassembler().reassemble(raw)
    assert completion is not None
    tool_calls = completion["choices"][0]["message"]["tool_calls"]
    assert [call["id"] for call in tool_calls] == ["call_a", "call_b"]


def test_multiple_choices_merged_independently() -> None:
    raw = stream(
        chunk(delta(index=0, content="zero"), delta(index=1, content="one")),
        chunk(delta(index=1, content=" more"), delta(index=0, finish_reason="stop")),
    )
    completion = ChatCompletionsReassembler().reassemble(raw)
    assert completion is not None
    contents = [choice["message"]["content"] for choice in completion["choices"]]
    assert contents == ["zero", "one more"]


def test_usage_chunk_carried_into_completion() -> None:
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    raw = stream(chunk(delta(content="hi")), {**BASE, "choices": [], "usage": usage})
    completion = ChatCompletionsReassembler().reassemble(raw)
    assert completion is not None
    assert completion["usage"] == usage


def test_empty_stream_reassembles_to_none() -> None:
    assert ChatCompletionsReassembler().reassemble(b"data: [DONE]\n\n") is None
