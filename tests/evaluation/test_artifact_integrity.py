"""真实文件状态的差分与恢复回归，不以补丁包含某行替代状态一致性。"""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from novacode.evaluation import artifacts
from tests.evaluation.test_runner import make_repo


def apply(root: Path, patch: str) -> None:
    if patch:
        subprocess.run(
            ["git", "apply", "--binary", "-"],
            cwd=root,
            input=patch.encode(),
            check=True,
            capture_output=True,
        )


def test_large_initial_paths_do_not_exceed_argv(tmp_path):
    root = tmp_path / "repo"
    _, base = make_repo(root)
    (root / "installed").write_text("initial")
    initial = {"installed": hashlib.sha256(b"initial").hexdigest()}
    initial.update({"./" * i + "installed": initial["installed"] for i in range(500, 1900)})
    # 旧 hash-only 合同仍用于这个最小重现；只修传参即可消除 E2BIG。
    (root / "value.txt").write_text("updated")
    patch, names = artifacts.extract_patch(root, base, initial)
    assert names == ["value.txt"] and "+updated" in patch


@pytest.mark.parametrize(
    "change",
    [
        "unchanged",
        "modify",
        "delete",
        "executable",
        "file-to-link",
        "link-target",
        "link-delete",
        "link-to-file",
        "binary",
        "committed",
        "new-ignored",
    ],
)
def test_typed_initial_baseline_roundtrips_real_state(tmp_path, change):
    root = tmp_path / "repo"
    git, base = make_repo(root)
    (root / ".gitignore").write_text("installed*\nnew-ignored\n")
    git("add", ".gitignore")
    git("commit", "-qm", "ignore initial assets")
    base = git("rev-parse", "HEAD").strip()
    asset = root / "installed token 中文\nfile"
    asset.write_bytes(b"initial\x00binary\xff")
    link = root / "installed-link"
    link.symlink_to(asset.name)
    original = tmp_path / "original"
    shutil.copytree(root, original, symlinks=True)
    baseline = tmp_path / "trusted-baseline"
    artifacts.capture_baseline(root, base, baseline)
    operator_index = (root / ".git/index").read_bytes()
    if change == "modify":
        asset.write_bytes(b"modified")
    elif change == "delete":
        asset.unlink()
    elif change == "executable":
        asset.chmod(0o755)
    elif change == "file-to-link":
        asset.unlink()
        asset.symlink_to("value.txt")
    elif change == "link-target":
        link.unlink()
        link.symlink_to("value.txt")
    elif change == "link-delete":
        link.unlink()
    elif change == "link-to-file":
        link.unlink()
        link.write_text("regular")
    elif change == "binary":
        asset.write_bytes(bytes(range(256)))
    elif change == "committed":
        (root / "value.txt").write_text("committed")
        git("add", "value.txt")
        git("commit", "-qm", "agent")
        operator_index = (root / ".git/index").read_bytes()
    elif change == "new-ignored":
        (root / "new-ignored").write_text("new")
    patch, paths = artifacts.extract_patch(root, base, baseline)
    assert (root / ".git/index").read_bytes() == operator_index
    if change == "unchanged":
        assert paths == [] and patch == ""
    else:
        assert paths
    apply(original, patch)
    assert artifacts.business_state(root) == artifacts.business_state(original)
    restored = tmp_path / "restored"
    artifacts.restore_workspace(baseline, patch, restored)
    assert artifacts.business_state(root) == artifacts.business_state(restored)


def test_baseline_rejects_changed_identity_and_agent_git_execution(tmp_path):
    root = tmp_path / "repo"
    git, base = make_repo(root)
    baseline = tmp_path / "baseline"
    artifacts.capture_baseline(root, base, baseline)
    marker = tmp_path / "escaped"
    git("config", "filter.evil.clean", f"touch {marker}")
    git("config", "core.fsmonitor", f"touch {marker}")
    (root / ".gitattributes").write_text("*.txt filter=evil text eol=crlf\n")
    (root / "value.txt").write_bytes(b"raw\r\nvalue\r\n")
    patch, _ = artifacts.extract_patch(root, base, baseline)
    assert not marker.exists()
    assert "raw" in patch
    with pytest.raises(ValueError, match="base"):
        artifacts.extract_patch(root, "0" * 40, baseline)
    (baseline / "manifest.json").write_text("{}")
    with pytest.raises(ValueError):
        artifacts.extract_patch(root, base, baseline)


def test_baseline_hashes_are_not_redacted_by_sensitive_path_names(tmp_path):
    from novacode.evaluation.ledger import Ledger, read_records

    root = tmp_path / "repo"
    _, base = make_repo(root)
    (root / "token-secret.conf").write_text("business data")
    info = artifacts.capture_baseline(root, base, tmp_path / "baseline")
    ledger = Ledger(tmp_path / "ledger.jsonl", "baseline")
    ledger.append("baseline", manifest=info)
    ledger.close()
    data = read_records(tmp_path / "ledger.jsonl")[0][0]["data"]["manifest"]
    item = next(e for e in data["entries"] if e["path"] == "token-secret.conf")
    assert item["sha256"] == hashlib.sha256(b"business data").hexdigest()


@pytest.mark.parametrize("failure", ["missing", "pack", "corrupt", "interrupt", "wrong-state"])
def test_archive_failure_preserves_business_and_baseline(tmp_path, monkeypatch, failure):
    from evaluation.formal import preservation

    output = tmp_path / "run"
    output.mkdir()
    root = output / "workspace"
    _, base = make_repo(root)
    artifacts.capture_baseline(root, base, output / "baseline")
    (root / "value.txt").write_text("final")
    patch = artifacts.extract_patch(root, base, output / "baseline")[0]
    if failure != "missing":
        (output / "submission.diff").write_text("" if failure == "wrong-state" else patch)
    if failure in ("pack", "interrupt"):

        def failed(*args, **kwargs):
            raise KeyboardInterrupt() if failure == "interrupt" else OSError("disk full")

        monkeypatch.setattr(preservation, "pack", failed)
    elif failure == "corrupt":
        original = preservation.pack

        def corrupt(paths, root, destination):
            original(paths, root, destination)
            if destination.name == "run.tar.gz":
                destination.write_bytes(b"broken archive")

        monkeypatch.setattr(preservation, "pack", corrupt)
    with pytest.raises((ValueError, OSError, KeyboardInterrupt, __import__("tarfile").ReadError)):
        preservation.archive_run(output, tmp_path / "run.tar.gz")
    assert (root / "value.txt").read_text() == "final"
    assert (output / "baseline/manifest.json").is_file()
    assert not (output / "archive.json").exists()


def test_diagnostics_are_bounded_redacted_and_preserve_http_facts():
    from novacode.evaluation.diagnostics import error_details

    error = RuntimeError("Authorization: Bearer custom-credential " + "x" * 9000)
    error.status_code = 429
    error.body = {
        "error": {"code": "rate_limit", "message": "custom-credential", "headers": "private"}
    }
    result = error_details(error, ("custom-credential",))
    assert result["chain"][0]["http_status"] == 429
    assert result["chain"][0]["provider_error"]["code"] == "rate_limit"
    assert len(result["chain"][0]["message"]) <= 2000
    assert "custom-credential" not in str(result) and "private" not in str(result)


async def test_failed_workspace_copy_keeps_owned_container_and_blocks_grade(tmp_path, monkeypatch):
    from novacode.evaluation.ledger import Ledger
    from novacode.evaluation.runner import stop_and_extract

    calls = []

    async def failed_copy(*args, **kwargs):
        calls.append(args[1])
        if args[1] == "cp":
            raise RuntimeError("copy interrupted")
        return b"false\n" if args[1] == "inspect" else b""

    monkeypatch.setattr("novacode.evaluation.runner.command", failed_copy)
    ledger = Ledger(tmp_path / "ledger.jsonl", "copy-failed")
    assert not await stop_and_extract(
        "novacode-eval-owned", tmp_path, True, {"limits": {"cleanup_seconds": 2}}, ledger
    )
    ledger.close()
    assert "rm" not in calls
    assert (tmp_path / "retained-container.json").exists()
