"""只读复核真实 tmux 策略证据，并归档原始字节。"""

import argparse
import gzip
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

from novacode.evaluation.ledger import read_records


def review(root: Path) -> dict:
    rows = {}
    identities = []
    for mode in ("on", "off", "overflow"):
        run = root / "runs" / mode
        records, truncated = read_records(run / "ledger.jsonl")
        assert not truncated

        def events(kind):
            return [r["data"] for r in records if r["kind"] == kind]

        def artifact(ref):
            path = run / ref["path"]
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
            return raw.decode()

        ready = events("ready")[0]
        identities.append(
            {
                k: ready[k]
                for k in (
                    "context_window",
                    "tools",
                    "facts_sha256",
                    "limits",
                    "output_limit",
                )
            }
        )
        assert ready["compression"] is (mode == "on")
        assert not ready["auxiliary_agents"]
        prepared = events("context_preparation")
        starts = events("request_start")
        ends = events("request_end")
        summaries = [e for e in starts if e["role"] == "summary"]
        assert len(starts) == len(ends)
        assert len(events("provider_close")) == 1
        assert (run / "exited").read_text() == "0"
        result = json.loads((run / "result.json").read_text())
        assert result["cleanup"] == "passed"
        final = artifact(events("turn_finished")[-1]["final"])
        session_ref = events("session")[0]["content"]
        session_text = artifact(session_ref)
        messages = [json.loads(line) for line in session_text.splitlines()]
        read = next(e for e in events("tool_end") if e["name"] == "read_file")
        read_output = artifact(read["output"])
        assert len(read_output.encode()) > 50_000 and "ORBIT-731" in read_output
        identities[-1]["read_output_sha256"] = read["output"]["sha256"]
        assert (
            hashlib.sha256((run / "workspace/facts.txt").read_bytes()).hexdigest()
            == hashlib.sha256(
                (
                    "".join(
                        f"record-{i:04d}: " + "configuration documentation " * 4 + "\n"
                        for i in range(1100)
                    )
                    + "FINAL-ID=ORBIT-731; VALUE=29\n"
                ).encode()
            ).hexdigest()
        )
        if mode == "on":
            assert any(e["offloaded"] for e in prepared)
            assert any(e["summarized"] and e["accepted"] for e in prepared)
            assert len(summaries) == 1
        else:
            assert all(
                e["disabled"] and not e["offloaded"] and not e["summarized"] for e in prepared
            )
            assert summaries == [] and events("compaction") == []
            assert not any(m["type"].startswith("compact_") for m in messages)
            assert "[tool result compacted]" not in session_text
        if mode == "overflow":
            assert result["termination"] == "context-overflow"
            errors = [e for e in ends if e["error_type"] == "PromptTooLongError"]
            assert len(errors) == 1
            assert errors[0]["usage"]["measured_total"] is None
            assert result["metrics"]["unknown_usage_requests"] == 1
            assert result["completed_requests"] == 1
            assert all(s["attempt"] == 1 for s in starts)
            assert session_text.count(" x") >= 1_100_000
            assert "maximum context length is 1048576" in (run / "tmux-pane.txt").read_text()
        else:
            assert result["termination"] == "completed" and result["completed_requests"] == 2
            assert "ORBIT-731" in final and "29" in final
            assert any(word in final for word in ("不允许", "不能", "禁止", "不可以", "只读"))
        rows[mode] = {
            **result,
            "offloads": sum(e["offloaded"] for e in prepared),
            "summary_requests": len(summaries),
            "history_accepted": sum(e["accepted"] for e in prepared),
            "raw_read_output_sha256": read["output"]["sha256"],
            "session_sha256": session_ref["sha256"],
        }
    assert identities[0] == identities[1] == identities[2]
    return {"same_inputs_window_tools_output_budget": True, "runs": rows}


def export(root: Path, output: Path) -> None:
    report = review(root)
    (campaign,) = (root / "runs").glob("campaign-*.jsonl")
    output.mkdir(exist_ok=False)
    for mode in ("on", "off", "overflow"):
        source = root / "runs" / mode
        target = output / mode
        target.mkdir()
        for name in ("ledger.jsonl", "result.json", "exited", "policy.yaml"):
            shutil.copy2(source / name, target / name)
        for name in ("manual-pane.txt", "tmux-pane.txt"):
            with gzip.open(target / (name + ".gz"), "wb") as stream:
                stream.write((source / name).read_bytes())
        with tarfile.open(target / "artifacts.tar.gz", "w:gz") as archive:
            archive.add(source / "artifacts", arcname="artifacts")
        (target / "artifacts-sha256.json").write_text(
            json.dumps(
                {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (source / "artifacts").glob("*")
                },
                indent=2,
            )
            + "\n"
        )
    shutil.copy2(campaign, output / "campaign.jsonl")
    (output / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.root, args.output)
