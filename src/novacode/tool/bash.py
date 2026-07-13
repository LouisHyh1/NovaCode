"""Bash command execution tool."""

import asyncio
import json
import locale
import os
import signal
import subprocess

from novacode.tool import Result, _truncate


class BashTool:
    read_only = False

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return (
            "在当前工作目录下执行 shell 命令，返回 stdout、stderr 和退出码。"
            "命令受超时约束（30s）。"
            "Windows 下使用 cmd /C，Linux/Mac 下使用 /bin/sh -c。"
            "读文件、找文件、搜内容请优先用 read_file/glob/grep，不要用 bash 拼凑。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
            },
            "required": ["command"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(content=f"参数 JSON 解析失败: {e}", is_error=True)
        cmd = data.get("command")
        if not cmd:
            return Result(content="缺少必填参数: command", is_error=True)
        try:
            if os.name == "nt":
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
        except OSError as e:
            return Result(content=f"命令执行失败: {e}", is_error=True)

        try:
            stdout_b, stderr_b = await proc.communicate()
        except BaseException:
            await _terminate_process_tree(proc)
            await proc.communicate()
            raise
        finally:
            _close_pipe_transports(proc)

        stdout = _try_decode(stdout_b)
        stderr = _try_decode(stderr_b)

        output = f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        output = _truncate(output, max_lines=10000, max_chars=30000)
        return Result(content=output)


def _try_decode(data: bytes) -> str:
    """多编码尝试解码，优先系统编码（解决 Windows 中文版 GBK 乱码）。"""
    encodings = [locale.getpreferredencoding(False), "utf-8", "gbk", "cp936"]
    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return

    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.communicate()
            if killer.returncode == 0:
                return
        except OSError:
            pass
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            await asyncio.wait_for(proc.wait(), timeout=0.5)
            return
        except (ProcessLookupError, TimeoutError):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=0.5)
            return
        except ProcessLookupError:
            return
        except TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                return

    if proc.returncode is None:
        proc.kill()


def _close_pipe_transports(proc: asyncio.subprocess.Process) -> None:
    for reader in (proc.stdout, proc.stderr):
        transport = getattr(reader, "_transport", None)
        if transport is not None:
            transport.close()
