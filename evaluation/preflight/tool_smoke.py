"""在任务容器中检查独立解释器与六个正式工具，不创建 Provider。"""

import asyncio
import json
import sys
from pathlib import Path

import novacode
from novacode.agent import Agent
from novacode.agent.context_manager import ContextManager
from novacode.agent.tool_runner import ToolRunner
from novacode.session import SessionService
from novacode.tool import new_default_registry, with_cwd


async def main():
    assert sys.version_info >= (3, 12)
    assert all((Agent, ContextManager, ToolRunner, SessionService))
    assert sys.executable.startswith("/opt/novacode/venv/"), sys.executable
    registry = new_default_registry()
    assert registry.count() == 6
    probe = Path("/testbed/nova-stage1-probe.txt")
    assert not probe.exists(), "不得覆盖目标项目文件"
    calls = [
        ("write_file", {"path": probe.name, "content": "nova-stage1-before\n"}, ""),
        ("read_file", {"path": probe.name}, "nova-stage1-before"),
        (
            "edit_file",
            {
                "path": probe.name,
                "old_string": "nova-stage1-before",
                "new_string": "nova-stage1-after",
            },
            "",
        ),
        ("glob", {"pattern": probe.name}, probe.name),
        ("grep", {"pattern": "nova-stage1-after", "glob": probe.name}, "nova-stage1-after"),
        ("bash", {"command": "pwd && python --version && cat nova-stage1-probe.txt"}, "/testbed"),
    ]
    try:
        with with_cwd("/testbed"):
            for name, args, expected in calls:
                result = await registry.execute(name, json.dumps(args))
                assert not result.is_error, (name, result.content)
                assert expected in result.content, (name, result.content)
                if name == "bash":
                    assert "exit_code: 0" in result.content, result.content
                    assert "nova-stage1-after" in result.content, result.content
                print(json.dumps({"tool": name, "result": result.content}, ensure_ascii=False))
        assert probe.read_text() == "nova-stage1-after\n"
        print(
            json.dumps(
                {
                    "novacode_version": novacode.__version__,
                    "agent_python": sys.executable,
                    "python_version": sys.version,
                    "novacode_module": novacode.__file__,
                    "model_calls_started": 0,
                }
            )
        )
    finally:
        if probe.exists():
            probe.unlink()


if __name__ == "__main__":
    asyncio.run(main())
