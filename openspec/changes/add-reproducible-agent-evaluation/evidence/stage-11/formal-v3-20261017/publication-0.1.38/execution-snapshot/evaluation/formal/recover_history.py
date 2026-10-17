"""在无网络、无凭证的新容器重建历史工具变更；输出仅是新增候选证据。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from evaluation.formal.preserve_history import HISTORY, REPO, sha_file
from evaluation.formal.run import save
from novacode.evaluation.ledger import read_records

DOCKER = "/snap/bin/docker"

# 历史命令只进入新容器的 /bin/sh；宿主从不解释或执行历史脚本文本。
TOOL = r"""
import json,sys,subprocess,os,signal,re,hashlib
from pathlib import Path
d=json.load(sys.stdin); a=json.loads(d['full_args']); name=d['name']
if name=='bash':
 p=subprocess.Popen(['/bin/sh','-c',a['command']],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
 try:
  out,err=p.communicate(timeout=30)
  result={'returncode':p.returncode,'stdout':out.decode(errors='replace'),'stderr':err.decode(errors='replace')}
 except subprocess.TimeoutExpired:
  os.killpg(p.pid,signal.SIGKILL); out,err=p.communicate()
  result={'timeout':True,'stdout':out.decode(errors='replace'),'stderr':err.decode(errors='replace')}
else:
 p=Path(a['path']); p=p if p.is_absolute() else Path('/testbed')/p
 if not p.resolve().is_relative_to('/testbed'):
  raise ValueError('非业务路径，拒绝重建')
 if name=='write_file':
  p.parent.mkdir(parents=True,exist_ok=True); p.write_text(a['content'],encoding='utf8')
 else:
  text=p.read_text(encoding='utf8')
  restored=[]
  if text.count(a['old_string'])!=1 and '[REDACTED]' in a['old_string']:
   pattern='(.*?)'.join(re.escape(s) for s in a['old_string'].split('[REDACTED]'))
   matches=list(re.finditer(pattern,text))
   if len(matches)!=1 or a['new_string'].count('[REDACTED]')!=len(matches[0].groups()):
    raise ValueError('遮蔽业务字符串无法由固定内容和唯一上下文确定')
   restored=list(matches[0].groups()); a['old_string']=matches[0].group()
   pieces=a['new_string'].split('[REDACTED]')
   a['new_string']=''.join(x+y for x,y in zip(pieces,restored))+pieces[-1]
  if text.count(a['old_string'])!=1: raise ValueError('历史唯一编辑前置不成立')
  p.write_text(text.replace(a['old_string'],a['new_string'],1),encoding='utf8')
 result={'path':str(p),'sha256':__import__('hashlib').sha256(p.read_bytes()).hexdigest()}
 if name=='edit_file' and restored:
  result['resolved_business_redactions']=[hashlib.sha256(s.encode()).hexdigest() for s in restored]
print(json.dumps(result))
"""


def docker(*args: str, stdin: bytes | None = None, timeout: int = 120) -> bytes:
    result = subprocess.run([DOCKER, *args], input=stdin, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            f"受控重建命令失败: {args[0]}, exit={result.returncode}: "
            + result.stderr.decode(errors="replace")[:2000]
        )
    return result.stdout


def snapshot(container: str, path: Path) -> None:
    with path.open("xb") as stream:
        result = subprocess.run(
            [DOCKER, "exec", container, "tar", "-czf", "-", "-C", "/testbed", "."],
            stdout=stream,
            stderr=subprocess.PIPE,
            timeout=240,
        )
        if result.returncode:
            raise RuntimeError("候选工作区快照失败；保留新容器")
        stream.flush()
        os.fsync(stream.fileno())


def verify_tool_outputs(original: Path, ends: list[dict[str, Any]]) -> None:
    for record in ends:
        data = record["data"]
        if "output" in data:
            assert (
                sha_file(original / "worker" / data["output"]["path"]) == data["output"]["sha256"]
            )


def recover(output: Path, retry_partial: bool = False, revision: int = 1) -> None:
    assert (output / "preservation-verified.json").exists(), "先完成保全"
    destination = output / (f"recoveries-v{revision}" if revision > 1 else "recoveries")
    destination.mkdir(exist_ok=False)
    gaps = json.loads((output.parent / "interruption-20261015/artifact-gaps.json").read_text())[
        "rows"
    ]
    selected = [row for row in gaps if not row["supplemental_recovery_patch"]]
    assert len(selected) == 11
    rows = []
    if retry_partial:
        previous = json.loads((output / "recovery-summary.json").read_text())["rows"]
        partial = {r["run_id"] for r in previous if r["classification"] == "deterministic-partial"}
        selected = [r for r in selected if r["run_id"] in partial]
        rows = [r for r in previous if r["run_id"] not in partial]
    for row in selected:
        original = HISTORY / "runs" / row["run_id"]
        folder = destination / row["run_id"]
        folder.mkdir()
        records, truncated = read_records(original / "worker/ledger.jsonl")
        starts = {r["data"]["invocation_id"]: r for r in records if r["kind"] == "tool_start"}
        ends = [r for r in records if r["kind"] == "tool_end"]
        sources = {
            str(p.relative_to(original)): sha_file(p)
            for p in original.rglob("*")
            if p.is_file() and not p.is_symlink()
        }
        verify_tool_outputs(original, ends)
        task_id = json.loads((original / "result.json").read_text())["task_id"]
        public = REPO / "evaluation/frozen/live-assets/public" / task_id
        environment = json.loads((public / "environment.json").read_text())
        contract = json.loads(
            (REPO / "evaluation/frozen/live-assets/contracts" / (task_id + ".json")).read_text()
        )
        container = "novacode-history-" + uuid4().hex
        info = {
            "run_id": row["run_id"],
            "new_identity": container,
            "sources": sources,
            "method": "isolated-original-tool-arguments-v1; "
            "exact-unique-edits; shell-inside-container-only",
            "image": environment["image"],
            "base_commit": contract["base_commit"],
            "network": "none",
            "credentials": "not-provided",
            "model_calls": 0,
            "original_scores_unchanged": True,
            "tool_events": [],
            "gaps": [
                "无原最终业务树指纹；不能由重放/测试通过证明等于历史状态",
                "候选 diff 先保留跟踪及非忽略新增路径；完整新树另存供类型化基线复核",
            ],
            "classification": "candidate",
        }
        if truncated or len(starts) != len(ends):
            info["gaps"].append("工具账本有截断或未结束调用")
        ready = False
        try:
            docker(
                "create",
                "--name",
                container,
                "--network",
                "none",
                "--cpus",
                "2",
                "--memory",
                "4g",
                "--pids-limit",
                "256",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--entrypoint",
                "tail",
                environment["image"],
                "-f",
                "/dev/null",
            )
            docker("start", container)
            docker("exec", container, "mkdir", "-p", "/isolated-user")
            actual = (
                docker("exec", "-w", "/testbed", container, "git", "rev-parse", "HEAD")
                .decode()
                .strip()
            )
            assert actual == contract["base_commit"]
            initial = destination / (task_id + "-initial.tar.gz")
            if not initial.exists():
                snapshot(container, initial)
            info["initial_snapshot"] = {"path": initial.name, "sha256": sha_file(initial)}
            for r in ends:
                data = r["data"]
                assert starts[data["invocation_id"]]["data"]["full_args"] == data["full_args"]
                if data["name"] not in ("bash", "edit_file", "write_file") or data[
                    "execution"
                ] not in ("succeeded", "failed"):
                    continue
                if data["name"] == "edit_file" and data["is_error"]:
                    # 原工具在唯一匹配检查失败时无写入；失败返回也属于成功执行的工具调用。
                    info["tool_events"].append(
                        {
                            "sequence": r["sequence"],
                            "name": data["name"],
                            "original_record_sha256": r["sha256"],
                            "original_no_write_error": True,
                        }
                    )
                    continue
                result = json.loads(
                    docker(
                        "exec",
                        "-i",
                        "-w",
                        "/testbed",
                        "-e",
                        "HOME=/isolated-user",
                        container,
                        "python",
                        "-c",
                        TOOL,
                        stdin=json.dumps(data).encode(),
                        timeout=45,
                    )
                )
                artifact = folder / (str(r["sequence"]) + "-tool.json")
                save(artifact, result)
                info["tool_events"].append(
                    {
                        "sequence": r["sequence"],
                        "name": data["name"],
                        "original_record_sha256": r["sha256"],
                        "new_output": artifact.name,
                        "new_output_sha256": sha_file(artifact),
                        "returncode": result.get("returncode"),
                        "timeout": result.get("timeout", False),
                    }
                )
            # 这里只生成诊断候选，不使用参考补丁或隐藏答案补齐任何修改。
            script = (
                "import os,subprocess,tempfile; from pathlib import Path; base="
                + repr(actual)
                + "; env=dict(os.environ,GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_NOSYSTEM='1',"
                "GIT_NO_REPLACE_OBJECTS='1'); t=tempfile.NamedTemporaryFile(); os.unlink(t.name); "
                "env['GIT_INDEX_FILE']=t.name; git=lambda *a:subprocess.check_output("
                "['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',*a],env=env); "
                "git('read-tree',base); git('add','-A','--','.'); "
                "print(git('diff','--cached','--binary','--no-ext-diff','--no-textconv',base)"
                ".decode(),end='')"
            )
            patch = docker("exec", "-w", "/testbed", container, "python", "-c", script)
            (folder / "candidate.diff").write_bytes(patch)
            snapshot(container, folder / "candidate-workspace.tar.gz")
            info["outputs"] = {p.name: sha_file(p) for p in folder.iterdir() if p.is_file()}
            ready = True
        except Exception as exc:
            info["classification"] = (
                "deterministic-partial" if info["tool_events"] else "insufficient"
            )
            info["gaps"].append(f"{type(exc).__name__}: {exc}")
            info["retained_container"] = container
        finally:
            # 失败的新现场也不删除；完整候选树耐久保存后才释放可再生副本。
            docker("stop", "-t", "1", container)
            if ready:
                docker("rm", container)
        save(folder / "recovery.json", info)
        rows.append(
            {
                "run_id": row["run_id"],
                "classification": info["classification"],
                "events_replayed": len(info["tool_events"]),
                "gaps": info["gaps"],
                "receipt_sha256": sha_file(folder / "recovery.json"),
            }
        )
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    save(
        output
        / (f"recovery-summary-v{revision}.json" if revision > 1 else "recovery-summary.json"),
        {"schema_version": 1, "model_calls": 0, "rows": rows},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--retry-partial", action="store_true")
    parser.add_argument("--revision", type=int, default=1)
    args = parser.parse_args()
    recover(args.output.resolve(), args.retry_partial, args.revision)
