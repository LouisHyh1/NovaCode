"""斜杠命令输入解析。"""


def parse(input_text: str) -> tuple[str, bool]:
    """返回命令名和是否为斜杠输入。"""
    text = input_text.strip()
    if not text.startswith("/"):
        return "", False
    body = text[1:]
    if not body:
        return "", True
    if body[0].isspace():
        return "", True
    return body.split(maxsplit=1)[0].lower(), True


def arguments(input_text: str) -> str:
    text = input_text.strip()
    if not text.startswith("/"):
        return ""
    parts = text[1:].split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""
