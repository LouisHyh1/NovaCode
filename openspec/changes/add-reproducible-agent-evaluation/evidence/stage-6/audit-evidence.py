"""归档先前尝试、核验最终源码一致性并扫描真实凭据；不调用模型。"""

import argparse
import gzip
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

from novacode.config import load

REPO = Path(__file__).resolve().parents[5]
EVIDENCE = Path(__file__).resolve().parent


def main(root: Path) -> None:
    debug = EVIDENCE / "debug"
    debug.mkdir(exist_ok=True)
    rows = {}
    for name in ("auto", "tmux"):
        source = root / "runs" / name
        target = debug / name
        if target.exists():
            rows[name] = json.loads((target / "result.json").read_text())
            continue
        target.mkdir()
        for filename in ("ledger.jsonl", "result.json", "exited"):
            if (source / filename).exists():
                shutil.copy2(source / filename, target / filename)
        if (source / "tmux-pane.txt").exists():
            with gzip.open(target / "tmux-pane.txt.gz", "wb") as stream:
                stream.write((source / "tmux-pane.txt").read_bytes())
        with tarfile.open(target / "artifacts.tar.gz", "w:gz") as archive:
            archive.add(source / "artifacts", arcname="artifacts")
        rows[name] = json.loads((source / "result.json").read_text())
    (EVIDENCE / "debug-summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )
    identities = []
    for name in ("auto-final", "tmux-final"):
        identity = json.loads((EVIDENCE / "final" / name / "source-identity.json").read_text())
        for relative, digest in identity["files"].items():
            assert hashlib.sha256((REPO / relative).read_bytes()).hexdigest() == digest, relative
        identities.append(identity)
    assert identities[0] == identities[1]
    secrets = [p.api_key.encode() for p in load(str(REPO / ".novacode/config.yaml")).providers]
    count = 0
    for path in EVIDENCE.rglob("*"):
        if not path.is_file():
            continue
        if path.name.endswith(".tar.gz"):
            with tarfile.open(path) as archive:
                raw = b"".join(archive.extractfile(m).read() for m in archive if m.isfile())
        elif path.suffix == ".gz":
            raw = gzip.decompress(path.read_bytes())
        else:
            raw = path.read_bytes()
        assert all(not secret or secret not in raw for secret in secrets), path.name
        count += 1
    summary = {
        "same_final_runtime_source": True,
        "credentials_scan": "passed",
        "scanned_files": count,
        "debug_measured_tokens": sum(r["metrics"]["measured_tokens"] for r in rows.values()),
        "prior_measured_tokens": sum(
            json.loads((EVIDENCE / "prior-final" / n / "result.json").read_text())["metrics"][
                "measured_tokens"
            ]
            for n in ("auto-final", "tmux-final")
        ),
        "final_measured_tokens": sum(
            json.loads((EVIDENCE / "final" / n / "result.json").read_text())["metrics"][
                "measured_tokens"
            ]
            for n in ("auto-final", "tmux-final")
        ),
    }
    (EVIDENCE / "audit.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    main(parser.parse_args().root)
