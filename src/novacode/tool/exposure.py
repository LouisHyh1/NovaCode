"""会话私有的工具曝光；注册、连接和业务授权仍由原模块负责。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from novacode.llm import ToolDefinition
from novacode.permission import Mode
from novacode.tool import Registry, Result

BASE_TOOLS = frozenset({"read_file", "write_file", "edit_file", "bash", "glob", "grep"})
DISCOVER = "discover_tools"
MAX_MATCHES = 5
MAX_SCHEMA_CHARS = 32_000
MAX_CATALOG = 64


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


class ToolExposure:
    def __init__(self, registry: Registry, *, progressive: bool = False, role: str = "") -> None:
        if registry.get(DISCOVER) is not None:
            raise ValueError("discover_tools 是会话发现入口的保留名称")
        self.registry = registry
        self.progressive = progressive
        self.role = role
        self._identity = ""
        self._eligible: list[ToolDefinition] = []
        self._exposed: set[str] = set()

    def prepare(self, mode: Mode, allowed: frozenset[str] | None) -> None:
        definitions = (
            self.registry.read_only_definitions()
            if mode == Mode.PLAN
            else self.registry.definitions()
        )
        eligible = [t for t in definitions if allowed is None or t.name in allowed]
        identity = digest(
            [
                self.progressive,
                self.role,
                mode.value,
                sorted(allowed) if allowed is not None else None,
                [asdict(t) for t in eligible],
            ]
        )
        if identity != self._identity:
            self._identity = identity
            self._exposed = {
                t.name for t in eligible if not self.progressive or t.name in BASE_TOOLS
            }
        self._eligible = eligible

    def reset(self) -> None:
        self._identity = ""
        self._exposed.clear()

    def definitions(self) -> list[ToolDefinition]:
        return [t for t in self._eligible if t.name in self._exposed] + [
            ToolDefinition(
                name=DISCOVER,
                description=(
                    "按工具精确名称或职责词项发现工具，取得完整 Schema，下一轮可调用。"
                    "不执行业务操作。每次最多返回5项；无匹配时缩短查询。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "maxLength": 200}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
        ]

    def catalog(self) -> str:
        if not self.progressive:
            return ""
        entries = sorted(
            (t.name, t.description[:160]) for t in self._eligible if t.name not in BASE_TOOLS
        )
        return "工具目录（完整 Schema 请调用 discover_tools）：\n" + json.dumps(
            {
                "tools": entries[:MAX_CATALOG],
                "total": len(entries),
                "truncated": len(entries) > MAX_CATALOG,
            },
            ensure_ascii=False,
        )

    def can_execute(self, name: str) -> bool:
        return name == DISCOVER or name in self._exposed

    def discover(self, args: str) -> Result:
        try:
            data = json.loads(args)
            query = data.get("query") if isinstance(data, dict) else None
            if (
                not isinstance(query, str)
                or not query.strip()
                or len(query) > 200
                or set(data) != {"query"}
            ):
                raise ValueError("query 必须为1–200字符的非空字符串")
        except (ValueError, TypeError) as exc:
            return Result(str(exc), is_error=True)
        query = normalized(query.strip())
        exact = [t for t in self._eligible if normalized(t.name) == query]
        terms = re.findall(r"\w+", query)
        matches = exact or [
            t
            for t in self._eligible
            if terms and all(word in normalized(t.name + " " + t.description) for word in terms)
        ]
        matches.sort(key=lambda t: t.name)
        selected: list[dict[str, Any]] = []
        for tool in matches[:MAX_MATCHES]:
            schema = asdict(tool)
            if len(json.dumps(selected + [schema], ensure_ascii=False)) > MAX_SCHEMA_CHARS:
                break
            selected.append(schema)
        added = sorted({t["name"] for t in selected} - self._exposed)
        self._exposed.update(t["name"] for t in selected)
        truncated = len(selected) < len(matches)
        return Result(
            json.dumps(
                {
                    "query": query,
                    "tools": selected,
                    "matched": len(matches),
                    "added": added,
                    "truncated": truncated,
                    "hint": "请用更精确名称缩小查询；过大 Schema 不会被截断。"
                    if truncated
                    else (
                        "无匹配，请缩短查询或使用目录中的名称。"
                        if not matches
                        else "下一轮可调用。"
                    ),
                },
                ensure_ascii=False,
            )
        )

    def snapshot(self) -> dict[str, Any]:
        return {"schema_version": 1, "identity": self._identity, "exposed": sorted(self._exposed)}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(self.snapshot(), ensure_ascii=False) + "\n", encoding="utf-8"
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def state_path(messages: Path) -> Path:
        return messages.parent / messages.stem / "exposure.json"

    @staticmethod
    def read(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def restore(self, state: Any) -> bool:
        # 不兼容或损坏时从当前基础集合重建，绝不导入其他角色的权限。
        names = state.get("exposed") if isinstance(state, dict) else None
        eligible = {t.name for t in self._eligible}
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or state.get("identity") != self._identity
            or not isinstance(names, list)
            or not all(isinstance(n, str) for n in names)
            or not set(names) <= eligible
        ):
            self._exposed = {
                t.name for t in self._eligible if not self.progressive or t.name in BASE_TOOLS
            }
            return False
        self._exposed.update(names)
        return True
