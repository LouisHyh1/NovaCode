"""只读复核发现轨迹、业务事实、实际请求输入及资源关闭，并导出原始字节。"""

import argparse
import gzip
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path

from novacode.evaluation.ledger import read_records, rebuild
from novacode.session import load_session
from novacode.tool.exposure import BASE_TOOLS, DISCOVER

EXPECTED = {
    "build_id": "BUILD-731",
    "status": "FAILED",
    "reason": "TEST_TIMEOUT",
    "retry_allowed": True,
}
BUSINESS = {"ci_build_status", "mcp__fixed_ci__ci_build_failure"}


def review(run: Path) -> dict:
    records, truncated = read_records(run / "ledger.jsonl")
    assert not truncated
    result = json.loads((run / "result.json").read_text())
    assert result["termination"] == "completed" and result["completed_requests"] == 2
    assert result["cleanup"] == result["mcp_cleanup"] == "passed"
    assert len([r for r in records if r["kind"] == "provider_close"]) == 1
    metrics = rebuild(run / "ledger.jsonl")
    assert metrics["unknown_usage_requests"] == 0 and metrics["discovery_calls"] >= 1
    assert not metrics["incomplete_tools"] and not metrics["incomplete_phases"]
    if (run / "exited").exists():
        assert (run / "exited").read_text() == "0"

    def artifact(ref):
        raw = (run / ref["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
        return raw.decode()

    visible = set()
    exposed = BASE_TOOLS | {DISCOVER}
    business_calls = {name: 0 for name in BUSINESS}
    request_count = 0
    for record in records:
        data = record["data"]
        if record["kind"] == "request_start":
            request_count += 1
            visible = set(data["visible_tools"])
            assert exposed <= visible and visible <= exposed | BUSINESS
            if request_count == 1:
                assert visible == BASE_TOOLS | {DISCOVER}
            request = json.loads(artifact(data["input"]))
            env = request["system"]["environment"]
            assert "工具目录" in env and "input_schema" not in env
            assert {t["name"] for t in request["tools"]} == visible
            assert data["parameters"]["sdk_max_retries"] == 0
            assert data["parameters"]["max_output_tokens"] == 4096
        elif record["kind"] == "tool_end":
            if data["name"] == DISCOVER and not data["is_error"]:
                found = json.loads(artifact(data["output"]))
                exposed |= {t["name"] for t in found["tools"]}
            elif data["name"] in BUSINESS:
                if data["is_error"]:
                    assert data["execution"] == "not-executed"
                    assert data["name"] not in visible
                    assert "尚未曝光" in artifact(data["output"])
                else:
                    assert data["name"] in visible
                    business_calls[data["name"]] += 1
                    actual = json.loads(artifact(data["output"]))
                    assert all(EXPECTED[k] == v for k, v in actual.items())
            else:
                assert data["is_error"] and data["execution"] == "not-executed"
    assert business_calls["ci_build_status"] >= 2
    assert business_calls["mcp__fixed_ci__ci_build_failure"] >= 1
    session_record = next(r["data"] for r in records if r["kind"] == "session")
    session_text = artifact(session_record["content"])
    session_path = next((run / "workspace/.novacode/sessions").glob("*.jsonl"))
    assert session_path.read_text() == session_text
    loaded = load_session(session_path)
    finals = [m.content for m in loaded.messages if m.role == "assistant" and not m.tool_calls]
    assert len(finals) == 2
    for final in finals:
        facts = []
        for match in re.finditer(r"\{", final):
            try:
                data, _ = json.JSONDecoder().raw_decode(final[match.start() :])
                facts.append(data)
            except ValueError:
                pass
        assert EXPECTED in facts
    business_files = [
        p
        for p in (run / "workspace").rglob("*")
        if p.is_file() and ".novacode" not in p.relative_to(run / "workspace").parts
    ]
    assert business_files == []
    return {
        "termination": result["termination"],
        "cleanup": result["cleanup"],
        "mcp_cleanup": result["mcp_cleanup"],
        "business_calls": business_calls,
        "completed_requests": result["completed_requests"],
        "metrics": metrics,
        "facts": EXPECTED,
        "session_sha256": session_record["content"]["sha256"],
    }


def export(root: Path, output: Path) -> None:
    rows = {name: review(root / "runs" / name) for name in ("auto-final", "tmux-final")}
    output.mkdir(parents=True, exist_ok=False)
    for name in rows:
        run = root / "runs" / name
        target = output / name
        target.mkdir()
        for path in run.glob("*.json*"):
            shutil.copy2(path, target / path.name)
        for name in ("exited", "policy.yaml"):
            if (run / name).exists():
                shutil.copy2(run / name, target / name)
        if (run / "tmux-pane.txt").exists():
            with gzip.open(target / "tmux-pane.txt.gz", "wb") as stream:
                stream.write((run / "tmux-pane.txt").read_bytes())
        with tarfile.open(target / "artifacts.tar.gz", "w:gz") as archive:
            archive.add(run / "artifacts", arcname="artifacts")
        state = next((run / "workspace/.novacode/sessions").glob("*/exposure.json"))
        shutil.copy2(state, target / "exposure.json")
    campaign = next((root / "runs").glob("campaign-*.jsonl"))
    shutil.copy2(campaign, output / "campaign.jsonl")
    (output / "review.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.root, args.output)
