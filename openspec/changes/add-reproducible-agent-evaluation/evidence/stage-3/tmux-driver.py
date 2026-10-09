"""仅驱动本阶段隔离 tmux 验收；不连接模型或访问真实用户状态。"""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
EVIDENCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
EVIDENCE.mkdir(parents=True, exist_ok=True)
WORKSPACE = Path(tempfile.mkdtemp(prefix="novacode-stage3-tmux-"))
SESSION = WORKSPACE.name


def tmux(*args):
    return subprocess.run(["tmux", *args], check=True, text=True, capture_output=True).stdout


def pane():
    return tmux("capture-pane", "-p", "-S", "-", "-t", SESSION)


def wait_for(predicate):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise TimeoutError(pane())


def send(value):
    tmux("send-keys", "-t", SESSION, "-l", value)
    tmux("send-keys", "-t", SESSION, "Enter")


def main():
    (WORKSPACE / "observation-one.txt").write_text("业务内容一\n", encoding="utf-8")
    (WORKSPACE / "observation-two.txt").write_text("业务内容二\n", encoding="utf-8")
    tmux("new-session", "-d", "-s", SESSION, "-x", "120", "-y", "45", "-c", str(WORKSPACE))
    tmux("set-option", "-t", SESSION, "remain-on-exit", "on")
    captures = []
    try:
        command = shlex.join(
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "evaluation/examples/tmux_observation_probe.py"),
                str(WORKSPACE),
                str(EVIDENCE),
            ]
        )
        send("exec " + command)
        wait_for(lambda: "stage3-scripted" in pane() or "mechanism-only-no-model" in pane())
        request = (
            "请读取 observation-one.txt 和 observation-two.txt，"
            "分别报告结果，并说明调用关联是否正确。"
        )
        send(request)
        wait_for(lambda: "观测机制验收通过" in pane())
        captures.append(pane())
        send("/compact")
        wait_for(lambda: '"role": "summary"' in (EVIDENCE / "tmux-ledger.jsonl").read_text())
        time.sleep(0.5)
        captures.append(pane())
        tmux("send-keys", "-t", SESSION, "C-c")
        wait_for(
            lambda: tmux("display-message", "-p", "-t", SESSION, "#{pane_dead}").strip() == "1"
        )
        status = tmux("display-message", "-p", "-t", SESSION, "#{pane_dead_status}").strip()
        captures.append(pane())
        (EVIDENCE / "tmux-pane.txt").write_text("\n".join(captures), encoding="utf-8")
        assert status == "0", status
        session_files = list((WORKSPACE / ".novacode/sessions").glob("*.jsonl"))
        assert len(session_files) == 1, session_files
        shutil.copyfile(session_files[0], EVIDENCE / "tmux-session.jsonl")
        verification = json.loads((EVIDENCE / "tmux-verification.json").read_text())
        verification.update({"pane_exit_code": 0, "request": request, "manual_summary": True})
        (EVIDENCE / "tmux-verification.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(verification, ensure_ascii=False, indent=2))
    finally:
        tmux("kill-session", "-t", SESSION)


if __name__ == "__main__":
    main()
