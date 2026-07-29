"""权限与 Hook 共用的四类字符串匹配器。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import translate as fnmatch_translate
from typing import Protocol


class Matcher(Protocol):
    """统一匹配接口。"""

    def match(self, value: str) -> bool: ...

    def __str__(self) -> str: ...


@dataclass(frozen=True)
class ExactMatcher:
    value: str

    def match(self, value: str) -> bool:
        return value == self.value

    def __str__(self) -> str:
        return f"={self.value}"


@dataclass(frozen=True)
class GlobMatcher:
    pattern: str
    is_command: bool = False

    def match(self, value: str) -> bool:
        if self.is_command:
            return match_command(self.pattern, value)
        return match_path(self.pattern, value)

    def __str__(self) -> str:
        return self.pattern


@dataclass(frozen=True)
class RegexMatcher:
    src: str
    compiled: re.Pattern[str]

    def match(self, value: str) -> bool:
        return self.compiled.search(value) is not None

    def __str__(self) -> str:
        return f"~{self.src}"


@dataclass(frozen=True)
class NotMatcher:
    inner: Matcher

    def match(self, value: str) -> bool:
        return not self.inner.match(value)

    def __str__(self) -> str:
        return f"!{self.inner}"


def compile_matcher(pattern: str, *, is_command: bool = False) -> Matcher:
    """解析 `=`/`~`/`!` 前缀；无前缀沿用 glob。"""
    if not pattern:
        raise ValueError("empty matcher pattern")
    head, rest = pattern[0], pattern[1:]
    if head == "=":
        return ExactMatcher(rest)
    if head == "~":
        try:
            return RegexMatcher(rest, re.compile(rest))
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
    if head == "!":
        if not rest:
            raise ValueError("not matcher requires an inner matcher")
        return NotMatcher(compile_matcher(rest, is_command=is_command))
    return GlobMatcher(pattern, is_command=is_command)


def match_command(pattern: str, target: str) -> bool:
    """命令 glob：`*` 与 `**` 都可跨空格匹配整串。"""
    regex: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            while index < len(pattern) and pattern[index] == "*":
                index += 1
            regex.append(".*")
        else:
            regex.append(re.escape(char))
            index += 1
    return re.fullmatch("".join(regex), target) is not None


def match_path(pattern: str, target: str) -> bool:
    """路径 glob：`*` 只匹配单段，`**` 可跨目录段。"""
    pat_parts = pattern.replace("\\", "/").split("/")
    tgt_parts = target.replace("\\", "/").split("/")
    rows, columns = len(pat_parts), len(tgt_parts)
    matched = [[False] * (columns + 1) for _ in range(rows + 1)]
    matched[0][0] = True
    for row in range(1, rows + 1):
        if pat_parts[row - 1] == "**":
            matched[row][0] = matched[row - 1][0]
    for row in range(1, rows + 1):
        part = pat_parts[row - 1]
        for column in range(1, columns + 1):
            if part == "**":
                matched[row][column] = matched[row - 1][column] or matched[row][column - 1]
            elif part == "*":
                matched[row][column] = matched[row - 1][column - 1]
            else:
                matched[row][column] = matched[row - 1][column - 1] and (
                    re.fullmatch(fnmatch_translate(part), tgt_parts[column - 1]) is not None
                )
    return matched[rows][columns]
