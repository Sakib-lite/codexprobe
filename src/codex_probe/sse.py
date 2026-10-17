DATA_PREFIX = "data:"
DONE_MARKER = "[DONE]"


def extract_data_payloads(raw: bytes) -> list[str]:
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    payloads: list[str] = []
    for event in text.split("\n\n"):
        data_lines = [
            _data_value(line) for line in event.split("\n") if line.startswith(DATA_PREFIX)
        ]
        if not data_lines:
            continue
        payload = "\n".join(data_lines)
        if payload != DONE_MARKER:
            payloads.append(payload)
    return payloads


def _data_value(line: str) -> str:
    return line[len(DATA_PREFIX) :].removeprefix(" ")


# NOTE: just drops the "data:" and "[DONE]" text from the stream. simple
