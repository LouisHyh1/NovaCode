"""官方镜像准备的可选 curl 通道；完整字节校验仍由 OCI 下载器执行。"""

from __future__ import annotations

import email
import os
import re
import signal
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO


def run_preparation(command: list[str], log: TextIO, timeout: float) -> int:
    """Linux准备进程拥有下载子进程；超时或中断后一起停止，保留日志和缓存。"""
    with subprocess.Popen(
        command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
    ) as process:
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


def safe_error(raw: bytes, request: Any) -> str:
    error = re.sub(r"https?://\S+", "[redacted-url]", raw.decode(errors="replace"))
    for name, value in request.header_items():
        if name.casefold() == "authorization":
            error = error.replace(value, "[redacted-auth]")
            error = error.replace(value.rsplit(" ", 1)[-1], "[redacted-auth]")
    return error[:300]


def windows_temp() -> str:
    """允许准备身份固定宿主临时目录，省去额外的 Windows 进程。"""
    temp_root = os.environ.get("NOVACODE_REGISTRY_TEMP")
    if temp_root:
        return temp_root
    native_temp = subprocess.check_output(
        ["/mnt/c/Windows/System32/cmd.exe", "/c", "echo", "%TEMP%"],
        cwd="/mnt/c",
        text=True,
    ).strip()
    return subprocess.check_output(["wslpath", "-u", native_temp], text=True).strip()


def curl_environment() -> tuple[str, str | None, Callable[[Path], str]]:
    if os.environ.get("NOVACODE_REGISTRY_TRANSPORT") == "curl":
        return (
            os.environ.get("NOVACODE_REGISTRY_CURL", "/usr/bin/curl"),
            os.environ.get("NOVACODE_REGISTRY_TEMP"),
            str,
        )

    def windows(path: Path) -> str:
        return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()

    return (
        os.environ.get("NOVACODE_REGISTRY_CURL", "/mnt/c/Windows/System32/curl.exe"),
        windows_temp(),
        windows,
    )


@contextmanager
def open_registry(request: Any, timeout: float = 60) -> Iterator[Any]:
    if os.environ.get("NOVACODE_REGISTRY_TRANSPORT") not in ("windows-curl", "curl"):
        with urllib.request.urlopen(request, timeout=timeout) as response:
            yield response
        return
    req = urllib.request.Request(request) if isinstance(request, str) else request
    if req.host not in ("auth.docker.io", "registry-1.docker.io"):
        raise ValueError("准备通道仅用于官方 Registry")
    binary, temp_root, native_path = curl_environment()
    # 临时盘与客户端处于同一系统，避免经UNC逐块写入；最终仍核对完整字节。
    with tempfile.TemporaryDirectory(prefix="nova-registry-", dir=temp_root) as directory:
        root = Path(directory)

        args = [
            binary,
            "--silent",
            "--show-error",
            "--location",
            "--fail",
            "--max-time",
            str(timeout),
            "--dump-header",
            native_path(root / "headers"),
            "--output",
            "-",
        ]
        route = os.environ.get("NOVACODE_REGISTRY_PROXY", "http://127.0.0.1:7897")
        args.extend(["--noproxy", "*"] if route == "direct" else ["--proxy", route])
        if "/blobs/" not in req.full_url:
            args.extend(
                [
                    "--retry",
                    "2",
                    "--retry-all-errors",
                    "--retry-delay",
                    "1",
                    "--retry-max-time",
                    str(timeout),
                ]
            )
        for name, value in req.header_items():
            args.extend(["--header", name + ": " + value])
        try:
            # Windows仅负责网络；二进制stdout由WSL写入指定存储，避免宿主大文件落地。
            with (root / "body").open("wb") as body:
                result = subprocess.run(
                    [*args, req.full_url],
                    stdout=body,
                    stderr=subprocess.PIPE,
                    timeout=timeout + 5,
                )
        except subprocess.SubprocessError:
            result = subprocess.CompletedProcess([], 28, b"", b"request timed out")
        # blob超时后的已下载前缀交给既有.part/Range流程保留；最终大小及SHA仍必须全匹配。
        partial_blob = (
            "/blobs/" in req.full_url
            and result.returncode in (18, 28, 56)
            and (root / "body").exists()
            and (root / "body").stat().st_size > 0
            and (root / "headers").exists()
        )
        if result.returncode and not partial_blob:
            # 不输出 curl 参数、认证头或重定向签名 URL。
            error = safe_error(result.stderr, req)
            raise RuntimeError(f"Registry 网络请求失败，exit={result.returncode}: {error[:300]}")
        sections = (root / "headers").read_text().strip().split("\n\n")
        final = sections[-1].splitlines()
        status = int(final[0].split()[1])
        if partial_blob and status not in (200, 206):
            raise RuntimeError("Registry断流前未取得合法blob响应")
        headers = email.message_from_string("\n".join(final[1:]))
        with (root / "body").open("rb") as stream:

            class Response:
                def read(self, size: int = -1) -> bytes:
                    return stream.read(size)

            response = Response()
            response.status = status  # type: ignore[attr-defined]
            response.headers = headers  # type: ignore[attr-defined]
            yield response
