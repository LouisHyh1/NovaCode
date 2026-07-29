"""斜杠命令输入解析。"""


def parse(input_text: str) -> tuple[str, bool]:
    """返回命令名和是否为斜杠输入；本期不接受任何参数。"""
    text = input_text.strip()
    if not text.startswith("/"):
        return "", False
    body = text[1:]
    if not body:
        return "", True
    if body[0].isspace():
        return "", True
    parts = body.split(maxsplit=1)
    if len(parts) != 1 or not parts[0]:
        return "", True
    return parts[0].lower(), True
