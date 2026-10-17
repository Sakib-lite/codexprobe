from codex_probe.sse import extract_data_payloads


def test_splits_events_in_order() -> None:
    raw = b'data: {"n": 1}\n\ndata: {"n": 2}\n\ndata: [DONE]\n\n'
    assert extract_data_payloads(raw) == ['{"n": 1}', '{"n": 2}']


def test_done_marker_is_not_a_payload() -> None:
    assert extract_data_payloads(b"data: [DONE]\n\n") == []


def test_comment_and_event_lines_ignored() -> None:
    raw = b': keep-alive\n\nevent: ping\n\nevent: message\ndata: {"n": 1}\n\n'
    assert extract_data_payloads(raw) == ['{"n": 1}']


def test_multi_line_data_joined_with_newline() -> None:
    raw = b"data: first\ndata: second\n\n"
    assert extract_data_payloads(raw) == ["first\nsecond"]


def test_crlf_line_endings_accepted() -> None:
    raw = b'data: {"n": 1}\r\n\r\ndata: [DONE]\r\n\r\n'
    assert extract_data_payloads(raw) == ['{"n": 1}']


def test_missing_space_after_colon_accepted() -> None:
    assert extract_data_payloads(b'data:{"n": 1}\n\n') == ['{"n": 1}']


def test_empty_stream_gives_no_payloads() -> None:
    assert extract_data_payloads(b"") == []
