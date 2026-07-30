"""Worktree 名称校验。"""

import re

_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_slug(name: str) -> None:
    """校验用户可控的 Worktree 名称，拒绝路径遍历。"""
    if not name:
        raise ValueError("Worktree 名称不能为空")
    if len(name) > 64:
        raise ValueError("Worktree 名称不能超过 64 个字符")
    if name.startswith("/") or name.endswith("/") or "//" in name:
        raise ValueError("Worktree 名称不能以 / 开头或结尾，也不能包含 //")
    for segment in name.split("/"):
        if segment in {".", ".."}:
            raise ValueError("Worktree 名称不能包含 . 或 .. 路径段")
        if not _SEGMENT.fullmatch(segment):
            raise ValueError(f"Worktree 名称包含非法字符: {segment!r}")


def flat_slug(name: str) -> str:
    return name.replace("/", "+")
