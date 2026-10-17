"""只读核验旧现场；新清单独占创建，包含工作区和停止容器的文件状态。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
import socket
import stat
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

REPO = Path(__file__).resolve().parents[2]
HISTORY = REPO / ".tmp/formal-stage11-v2"
CONTAINER = "novacode-eval-45b72375695f4bd0912308e1c2cfb0e5"


def sha_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stream_digest(stream: IO[bytes]) -> str:
    result = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        result.update(block)
    return result.hexdigest()


def docker_get(path: str) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(60)
    sock.connect("/var/run/docker.sock")
    conn = http.client.HTTPConnection("localhost")
    conn.sock = sock
    conn.request("GET", path)
    response = conn.getresponse()
    if response.status != 200:
        conn.close()
        raise RuntimeError(f"Docker 只读请求失败: {response.status} {path}")
    return conn, response


def docker_json(path: str) -> Any:
    conn, response = docker_get(path)
    try:
        return json.load(response)
    finally:
        conn.close()


def processes() -> list[dict[str, Any]]:
    found = []
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            args = (folder / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        # 不保存完整命令行，避免配置/凭证意外入盘；只检查实际 Python 调度入口。
        decoded = [arg.decode(errors="replace") for arg in args if arg]
        if (
            decoded
            and "python" in Path(decoded[0]).name
            and any(
                arg.endswith("evaluation/formal/run.py") or arg == "novacode.evaluation.worker"
                for arg in decoded[1:]
            )
        ):
            found.append({"pid": int(folder.name), "executable": Path(decoded[0]).name})
    if found:
        raise RuntimeError(f"旧评测进程仍在运行: {found}")
    return found


def entry(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    value: dict[str, Any] = {"mode": stat.S_IMODE(metadata.st_mode)}
    if stat.S_ISLNK(metadata.st_mode):
        value.update(type="symlink", target=os.readlink(path))
    elif stat.S_ISREG(metadata.st_mode):
        value.update(type="file", size=metadata.st_size, sha256=sha_file(path))
    elif stat.S_ISDIR(metadata.st_mode):
        value.update(type="directory")
    else:
        value.update(type="special", st_mode=metadata.st_mode)
    return value


def capture(roots: list[str], exclude: str) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for root_name in roots:
        root = REPO / root_name
        if not root.exists() and not root.is_symlink():
            raise RuntimeError(f"受保护路径缺失: {root_name}")
        paths = [root]
        while paths:
            path = paths.pop()
            name = path.relative_to(REPO).as_posix()
            if name == exclude or name.startswith(exclude + "/") or name in files:
                continue
            value = files[name] = entry(path)
            if value["type"] == "directory":
                paths.extend(path.iterdir())
    return dict(sorted(files.items()))


def container_state() -> dict[str, Any]:
    inspect = docker_json(f"/containers/{CONTAINER}/json")
    if inspect["State"]["Running"]:
        raise RuntimeError("保留容器正在运行，停止只读审计")
    conn, response = docker_get(f"/containers/{CONTAINER}/archive?path=/testbed")
    files = {}
    try:
        with tarfile.open(fileobj=response, mode="r|") as archive:
            for member in archive:
                value: dict[str, Any] = {"mode": member.mode, "type": member.type.decode()}
                if member.isfile():
                    stream = archive.extractfile(member)
                    assert stream is not None
                    value.update(size=member.size, sha256=stream_digest(stream))
                elif member.issym() or member.islnk():
                    value["target"] = member.linkname
                files[member.name] = value
    finally:
        conn.close()
    return {
        "id": inspect["Id"],
        "image": inspect["Image"],
        "state": inspect["State"],
        "changes": sorted(docker_json(f"/containers/{CONTAINER}/changes"), key=lambda v: v["Path"]),
        "testbed": dict(sorted(files.items())),
    }


def roots() -> list[str]:
    manifest = json.loads((REPO / "evaluation/frozen/campaign-v2/manifest.json").read_text())
    selected = [
        ".tmp/formal-stage11-v1",
        ".tmp/formal-stage11-v2",
        "evaluation/frozen/campaign",
        "evaluation/frozen/campaign-v2",
        "evaluation/frozen/live-assets",
        "evaluation/frozen/specialty-assets",
        "evaluation/frozen/specialty-assets-v2",
        "openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11",
    ]
    # 旧清单中的外部执行资产和历史文档保存清单也纳入；不读操作者配置。
    selected.extend(
        str(Path(p).relative_to(REPO))
        for p in manifest["external_files"]
        if Path(p).is_absolute() and Path(p).is_relative_to(REPO)
    )
    return sorted(set(selected))


def run(
    output: Path,
    verify: bool,
    derived_roots: tuple[str, ...] = (),
    receipt_name: str = "preservation-verified.json",
) -> None:
    exclude = output.relative_to(REPO).as_posix()
    live = processes()
    selected = roots()
    state = {"files": capture(selected, exclude), "container": container_state()}
    runs = sorted(
        p
        for p in (HISTORY / "runs").iterdir()
        if (p / "result.json").exists()
        and json.loads((p / "result.json").read_text())["termination"] is not None
    )
    pairs = [
        {
            "run_id": p.name,
            "result_sha256": sha_file(p / "result.json"),
            "archive_sha256": sha_file(HISTORY / "archives" / (p.name + ".tar.gz")),
        }
        for p in runs
    ]
    assert len(pairs) == 147
    assert len(list((HISTORY / "archives").glob("*.tar.gz"))) == 147
    target = output / "preservation.json.gz"
    if verify:
        with gzip.open(target, "rt") as stream:
            old = json.load(stream)
        changed = [k for k in old["files"] if old["files"][k] != state["files"].get(k)]
        added = set(state["files"]) - set(old["files"])
        unexpected = [
            p
            for p in added
            if not any(p == root or p.startswith(root + "/") for root in derived_roots)
        ]
        assert not changed, changed[:20]
        assert not unexpected, unexpected[:20]
        assert old["container"] == state["container"], "停止容器发生变化"
        assert old["pairs"] == pairs, "终态与归档对应变化"
        result = {
            "status": "PRESERVED",
            "utc": datetime.now(UTC).isoformat(),
            "inventory_sha256": sha_file(target),
            "entries": len(state["files"]),
            "container_entries": len(state["container"]["testbed"]),
            "terminal_archive_pairs": len(pairs),
            "changed": changed,
            "old_model_processes": live,
            "model_calls": 0,
            "explicit_new_derived_roots": derived_roots,
            "new_derived_entries": len(added),
        }
        assert Path(receipt_name).name == receipt_name
        with (output / receipt_name).open("x") as stream:
            json.dump(result, stream, indent=2)
    else:
        output.mkdir(parents=True, exist_ok=False)
        result = {
            "utc": datetime.now(UTC).isoformat(),
            "roots": selected,
            "pairs": pairs,
            "old_model_processes": live,
            **state,
        }
        with (
            target.open("xb") as binary_stream,
            gzip.GzipFile(fileobj=binary_stream, mode="wb", mtime=0) as zipped,
        ):
            zipped.write(json.dumps(result, ensure_ascii=True, sort_keys=True).encode())
        result = {
            "inventory_sha256": sha_file(target),
            "entries": len(state["files"]),
            "container_entries": len(state["container"]["testbed"]),
            "terminal_archive_pairs": len(pairs),
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--derived-root", action="append", default=[])
    parser.add_argument("--receipt", default="preservation-verified.json")
    args = parser.parse_args()
    run(args.output.resolve(), args.verify, tuple(args.derived_root), args.receipt)
