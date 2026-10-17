import json
from typing import Any, Protocol

from codex_probe.sse import extract_data_payloads

FINAL_RESPONSE_EVENTS = ("response.completed", "response.failed", "response.incomplete")


class Reassembler(Protocol):
    def reassemble(self, raw: bytes) -> dict[str, Any] | None: ...


def reassembler_for(wire_api: str) -> Reassembler:
    if wire_api == "chat":
        return ChatCompletionsReassembler()
    if wire_api == "responses":
        return ResponsesReassembler()
    raise ValueError(f"no reassembler for wire_api {wire_api!r}")


class ChatCompletionsReassembler:
    def reassemble(self, raw: bytes) -> dict[str, Any] | None:
        chunks = [json.loads(payload) for payload in extract_data_payloads(raw)]
        if not chunks:
            return None
        choices: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] | None = None
        for chunk in chunks:
            for choice in chunk.get("choices") or []:
                index = choice.get("index", 0)
                _merge_choice(choices.setdefault(index, _empty_choice()), choice)
            usage = chunk.get("usage") or usage
        return _completion(chunks[0], choices, usage)


def _empty_choice() -> dict[str, Any]:
    return {"role": None, "content": [], "tool_calls": {}, "finish_reason": None}


def _merge_choice(state: dict[str, Any], choice: dict[str, Any]) -> None:
    delta = choice.get("delta") or {}
    if role := delta.get("role"):
        state["role"] = role
    if (content := delta.get("content")) is not None:
        state["content"].append(content)
    for fragment in delta.get("tool_calls") or []:
        _merge_tool_call(state["tool_calls"], fragment)
    if finish_reason := choice.get("finish_reason"):
        state["finish_reason"] = finish_reason


def _merge_tool_call(calls: dict[int, dict[str, Any]], fragment: dict[str, Any]) -> None:
    slot = calls.setdefault(
        fragment.get("index", 0),
        {"id": None, "type": None, "name": [], "arguments": []},
    )
    if call_id := fragment.get("id"):
        slot["id"] = call_id
    if call_type := fragment.get("type"):
        slot["type"] = call_type
    function = fragment.get("function") or {}
    if name := function.get("name"):
        slot["name"].append(name)
    if arguments := function.get("arguments"):
        slot["arguments"].append(arguments)


def _completion(
    first_chunk: dict[str, Any],
    choices: dict[int, dict[str, Any]],
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    completion = {
        "id": first_chunk.get("id"),
        "object": "chat.completion",
        "created": first_chunk.get("created"),
        "model": first_chunk.get("model"),
        "choices": [_final_choice(index, state) for index, state in sorted(choices.items())],
    }
    if usage is not None:
        completion["usage"] = usage
    return completion


def _final_choice(index: int, state: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": state["role"] or "assistant",
        "content": "".join(state["content"]) if state["content"] else None,
    }
    if state["tool_calls"]:
        message["tool_calls"] = [
            _final_tool_call(slot) for _, slot in sorted(state["tool_calls"].items())
        ]
    return {"index": index, "message": message, "finish_reason": state["finish_reason"]}


def _final_tool_call(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": slot["id"],
        "type": slot["type"] or "function",
        "function": {"name": "".join(slot["name"]), "arguments": "".join(slot["arguments"])},
    }


class ResponsesReassembler:
    def reassemble(self, raw: bytes) -> dict[str, Any] | None:
        events = [json.loads(payload) for payload in extract_data_payloads(raw)]
        if not events:
            return None
        final = _final_response(events)
        if final is not None:
            return final
        return _partial_response(events)


def _final_response(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        response = event.get("response")
        if event.get("type") in FINAL_RESPONSE_EVENTS and isinstance(response, dict):
            return response
    return None


def _partial_response(events: list[dict[str, Any]]) -> dict[str, Any]:
    base: dict[str, Any] = next(
        (
            event["response"]
            for event in events
            if event.get("type") == "response.created" and isinstance(event.get("response"), dict)
        ),
        {},
    )
    text = "".join(
        event.get("delta") or ""
        for event in events
        if event.get("type") == "response.output_text.delta"
    )
    partial = dict(base)
    partial["status"] = "incomplete"
    if text:
        partial["output_text"] = text
    return partial


# NOTE: joins the delta chunks back into one full response. tool call args come in pieces too
# EXAMPLE: { "id": "c1", "object": "chat.completion", "created": 123, "model": "gpt-4",   "choices": [ { "index": 0, "message": { "role": "assistant", "content": "Hello!", },  "finish_reason": "stop", } ], "usage": {"total_tokens": 5}, }
# For Response api just find the last completed response. full final response is there. For chat completion just merge the chunks after filtering