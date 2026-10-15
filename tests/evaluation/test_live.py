"""固定全池抽样与公开白名单的必要反例。"""

import pytest

from novacode.evaluation.live import candidate_order, public_package, screen_pool


@pytest.fixture
def registry_disk_space(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "evaluation.live.fetch_image.shutil.disk_usage",
        lambda path: SimpleNamespace(free=100 * 1024**3),
    )


def test_preparation_timeout_stops_download_descendant(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path

    import pytest

    if os.name != "posix":
        pytest.skip("官方镜像资格验收只在Linux宿主执行")
    from evaluation.live.registry_http import run_preparation

    log = tmp_path / "preparation.txt"
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(child.pid,flush=True); time.sleep(60)"
    )
    with log.open("w") as stream:
        with pytest.raises(subprocess.TimeoutExpired):
            run_preparation([sys.executable, "-c", script], stream, timeout=1)
    child = int(log.read_text().strip())
    status = Path(f"/proc/{child}/status")
    try:
        assert not status.exists() or "State:\tZ" in status.read_text()
    finally:
        if status.exists() and "State:\tZ" not in status.read_text():
            os.kill(child, signal.SIGKILL)


def test_registry_token_uses_local_docker_login_without_returning_credentials(
    tmp_path, monkeypatch
):
    import io
    import json

    from evaluation.live.fetch_image import authorization

    (tmp_path / "config.json").write_text(
        json.dumps({"auths": {"https://index.docker.io/v1/": {"auth": "fixture-credential"}}})
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))

    def token(request, timeout):
        assert request.host == "auth.docker.io"
        assert request.get_header("Authorization") == "Basic fixture-credential"
        return io.BytesIO(b'{"token": "scoped-token"}')

    monkeypatch.setattr("urllib.request.urlopen", token)
    assert authorization("org/image") == {"Authorization": "Bearer scoped-token"}


def test_windows_registry_partial_blob_is_preserved_and_auth_errors_are_redacted(
    tmp_path, monkeypatch
):
    import subprocess
    import urllib.request
    from pathlib import Path

    import pytest

    from evaluation.live.registry_http import open_registry

    monkeypatch.setenv("NOVACODE_REGISTRY_TRANSPORT", "windows-curl")
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda args, **kwargs: (
            str(tmp_path) if args[0].endswith("cmd.exe") or args[1] == "-u" else args[2]
        ),
    )

    def partial(args, **kwargs):
        assert args[args.index("--max-time") + 1] == "60"
        Path(args[args.index("--dump-header") + 1]).write_text(
            "HTTP/1.1 200 OK\nContent-Length: 8\n\n"
        )
        assert args[args.index("--output") + 1] == "-"
        kwargs["stdout"].write(b"prefix")
        return subprocess.CompletedProcess(
            [], 28, b"", b"Bearer fixture-secret https://example.invalid/signed?token=secret"
        )

    monkeypatch.setattr(subprocess, "run", partial)
    blob = urllib.request.Request(
        "https://registry-1.docker.io/v2/repo/blobs/sha256:abc",
        headers={"Authorization": "Bearer fixture-secret"},
    )
    with open_registry(blob) as response:
        assert response.status == 200 and response.read() == b"prefix"
    auth = urllib.request.Request(
        "https://auth.docker.io/token", headers={"Authorization": "Bearer fixture-secret"}
    )
    with pytest.raises(RuntimeError) as error:
        with open_registry(auth):
            pass
    assert "fixture-secret" not in str(error.value) and "token=secret" not in str(error.value)
    assert not list(tmp_path.glob("nova-registry-*"))


def test_native_registry_honors_task_download_directory(tmp_path, monkeypatch):
    import subprocess
    import urllib.request
    from pathlib import Path

    from evaluation.live.registry_http import open_registry

    monkeypatch.setenv("NOVACODE_REGISTRY_TRANSPORT", "curl")
    monkeypatch.setenv("NOVACODE_REGISTRY_PROXY", "direct")
    monkeypatch.setenv("NOVACODE_REGISTRY_TEMP", str(tmp_path))

    def downloaded(args, **kwargs):
        assert args[args.index("--max-time") + 1] == "60"
        assert args[args.index("--output") + 1] == "-"
        body = Path(kwargs["stdout"].name)
        assert body.resolve().is_relative_to(tmp_path.resolve())
        Path(args[args.index("--dump-header") + 1]).write_text("HTTP/2 200 OK\n\n")
        kwargs["stdout"].write(b"verified-content")
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", downloaded)
    with open_registry(
        urllib.request.Request("https://registry-1.docker.io/v2/repo/blobs/test")
    ) as response:
        assert response.read() == b"verified-content"
    assert not list(tmp_path.glob("nova-registry-*"))


def test_registry_limit_pauses_selection_without_excluding_candidate(tmp_path):
    import json
    from pathlib import Path

    import pytest

    from evaluation.live.curate import qualify_candidate

    output = tmp_path / "final-qualification/repo-1"
    output.mkdir(parents=True)
    (output / "qualification.json").write_text(json.dumps({"qualified": False}))
    (output / "image-pull.txt").write_text("HTTP Error 429: Too Many Requests")
    with pytest.raises(RuntimeError, match="暂停选择而非排除候选"):
        qualify_candidate({"instance_id": "repo-1"}, tmp_path, Path(), Path(), Path())
    assert not (tmp_path / "attempts").exists()


def test_transport_change_replays_first_started_qualification(tmp_path):
    import json

    from evaluation.live.curate import qualification_base

    (tmp_path / "transport-policy.json").write_text(
        json.dumps(
            {
                "reused_test_attempts": ["repo-1-image-retry-2"],
            }
        )
    )
    assert qualification_base(tmp_path, "repo-1").name == "repo-1-image-retry-2"
    assert qualification_base(tmp_path, "repo-2").name == "repo-2"


def test_preparation_disk_pressure_pauses_without_excluding_candidate(tmp_path):
    import json
    from pathlib import Path

    import pytest

    from evaluation.live.curate import qualify_candidate

    output = tmp_path / "final-qualification/repo-1"
    output.mkdir(parents=True)
    (output / "qualification.json").write_text(
        json.dumps({"qualified": False, "error": "AssertionError: 准备磁盘余量不足"})
    )
    with pytest.raises(RuntimeError, match="暂停选择而非排除候选"):
        qualify_candidate({"instance_id": "repo-1"}, tmp_path, Path(), Path(), Path())
    assert not (tmp_path / "attempts").exists()


def test_low_disk_prevents_new_qualification_attempt(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    import pytest

    from evaluation.live.curate import qualify_candidate

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "selection-rule.json").write_text(
        json.dumps({"limits": {"minimum_free_disk_bytes": 20 * 1024**3}})
    )
    monkeypatch.setattr(
        "evaluation.live.curate.shutil.disk_usage", lambda root: SimpleNamespace(free=0)
    )
    with pytest.raises(RuntimeError, match="暂停选择而非排除候选"):
        qualify_candidate({"instance_id": "repo-1"}, tmp_path, pool, Path(), Path())
    assert not (tmp_path / "final-qualification").exists()


def test_incomplete_qualification_is_not_reported_as_reference_success(tmp_path):
    import hashlib
    import json
    from pathlib import Path

    from evaluation.live.curate import qualify_candidate

    archive = tmp_path / "runtime.tar"
    archive.write_bytes(b"runtime")
    output = tmp_path / "final-qualification/repo-1"
    output.mkdir(parents=True)
    (output / "qualification.json").write_text(
        json.dumps(
            {
                "qualified": False,
                "runs": [],
                "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        )
    )
    result, event = qualify_candidate(
        {"instance_id": "repo-1", "difficulty": "easy"}, tmp_path, Path(), archive, Path()
    )
    assert not result["qualified"]
    assert event["action"] == "EXCLUDED" and event["reason"] == "qualification-incomplete"


def test_preparation_retry_never_repeats_started_acceptance(tmp_path):
    from evaluation.live.curate import image_retryable

    (tmp_path / "image-pull.txt").write_text("short read: unexpected EOF")
    assert image_retryable({"qualified": False}, tmp_path)
    (tmp_path / "image-pull.txt").write_text(
        "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1010)"
    )
    assert image_retryable({"qualified": False}, tmp_path)
    assert not image_retryable({"qualified": True}, tmp_path)
    assert image_retryable(
        {"qualified": False, "error": "PreparationInterrupted: stopped"}, tmp_path
    )
    (tmp_path / "original").mkdir()
    assert not image_retryable({"qualified": False}, tmp_path)
    assert not image_retryable(
        {"qualified": False, "error": "PreparationInterrupted: stopped"}, tmp_path
    )


def test_official_blob_hash_mismatch_is_not_imported(tmp_path, monkeypatch, registry_disk_space):
    import hashlib
    import io

    import pytest

    from evaluation.live.fetch_image import blob

    monkeypatch.setattr("evaluation.live.fetch_image.authorization", lambda repo: {})

    correct = b"official-layer"
    descriptor = {"digest": "sha256:" + hashlib.sha256(correct).hexdigest(), "size": len(correct)}

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response(b"x" * len(correct)))
    with pytest.raises(AssertionError, match="SHA-256"):
        blob("https://example.invalid/v2/repo/", descriptor, {}, tmp_path)
    assert not (tmp_path / descriptor["digest"].split(":")[1]).exists()


def test_official_blob_resumes_verified_range(tmp_path, monkeypatch, registry_disk_space):
    import hashlib
    import io

    from evaluation.live.fetch_image import blob

    monkeypatch.setattr("evaluation.live.fetch_image.authorization", lambda repo: {})

    correct = b"official-layer"
    digest = hashlib.sha256(correct).hexdigest()
    (tmp_path / (digest + ".part")).write_bytes(correct[:3])

    class Response(io.BytesIO):
        status = 206
        headers = {"Content-Range": f"bytes 3-{len(correct) - 1}/{len(correct)}"}

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response(correct[3:]))
    assert (
        blob(
            "https://example.invalid/v2/repo/",
            {"digest": "sha256:" + digest, "size": len(correct)},
            {},
            tmp_path,
        ).read_bytes()
        == correct
    )


def test_image_import_preserves_existing_transport_archive(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    import tarfile
    from contextlib import contextmanager
    from pathlib import Path

    from evaluation.live import fetch_image

    image = "starryzhang/sweb.eval.x86_64.org_1776_repo-1:latest"
    repo = image.rsplit(":", 1)[0]
    config = {
        "architecture": "amd64",
        "os": "linux",
        "rootfs": {"diff_ids": ["sha256:" + "a" * 64]},
    }
    cache = tmp_path / "blobs"
    cache.mkdir()
    descriptors = []
    for raw in (json.dumps(config).encode(), b"verified-layer"):
        digest = hashlib.sha256(raw).hexdigest()
        (cache / digest).write_bytes(raw)
        descriptors.append({"digest": "sha256:" + digest, "size": len(raw)})
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": descriptors[0],
        "layers": descriptors[1:],
    }
    raw = json.dumps(manifest).encode()
    sha = hashlib.sha256(raw).hexdigest()
    preserved = tmp_path / (sha + ".tar")
    preserved.write_bytes(b"previous-transport-evidence")

    class Response(io.BytesIO):
        headers = {"Docker-Content-Digest": "sha256:" + sha}

    @contextmanager
    def official(request, timeout):
        with Response(raw) as response:
            yield response

    imported = []

    def load(command, check):
        archive = Path(command[-1])
        assert archive != preserved
        with tarfile.open(archive) as package:
            assert package.extractfile("blobs/sha256/" + sha).read() == raw
        imported.append(archive)

    proof = {
        "Id": "sha256:" + sha,
        "RepoDigests": [repo + "@sha256:" + sha],
        "RootFS": {"Layers": config["rootfs"]["diff_ids"]},
    }
    monkeypatch.setattr(fetch_image, "authorization", lambda repo: {})
    monkeypatch.setattr(fetch_image, "open_registry", official)
    monkeypatch.setattr(fetch_image.subprocess, "run", load)
    monkeypatch.setattr(fetch_image.subprocess, "check_output", lambda *a, **k: json.dumps([proof]))
    assert fetch_image.fetch(image, tmp_path)["image_id"] == proof["Id"]
    assert preserved.read_bytes() == b"previous-transport-evidence"
    assert len(imported) == 1 and not imported[0].exists()


@pytest.mark.parametrize(("failures", "expected_calls"), [(2, 3), (3, 3), (-1, 1)])
def test_blob_tls_retry_preserves_prefix_and_is_bounded(
    tmp_path, monkeypatch, failures, expected_calls, registry_disk_space
):
    import hashlib
    import io
    from contextlib import contextmanager

    from evaluation.live.fetch_image import blob

    correct = b"official-layer"
    digest = hashlib.sha256(correct).hexdigest()
    partial = tmp_path / (digest + ".part")
    partial.write_bytes(correct[:3])
    calls = []
    monkeypatch.setattr("evaluation.live.fetch_image.authorization", lambda repo: {})

    class Response(io.BytesIO):
        status = 206
        headers = {"Content-Range": f"bytes 3-{len(correct) - 1}/{len(correct)}"}

    @contextmanager
    def flaky(request, timeout):
        calls.append(request.get_header("Range"))
        if failures == -1:
            raise RuntimeError("Registry 网络请求失败，exit=22: HTTP 404")
        if len(calls) <= failures:
            raise RuntimeError("Registry 网络请求失败，exit=35: TLS handshake failed")
        with Response(correct[3:]) as response:
            yield response

    monkeypatch.setattr("evaluation.live.fetch_image.open_registry", flaky)
    args = (
        "https://example.invalid/v2/repo/",
        {"digest": "sha256:" + digest, "size": len(correct)},
        {},
        tmp_path,
    )
    if failures == 2:
        assert blob(*args).read_bytes() == correct
    else:
        with pytest.raises(RuntimeError, match="exit="):
            blob(*args)
        assert partial.read_bytes() == correct[:3]
        assert not (tmp_path / digest).exists()
    assert calls == ["bytes=3-"] * expected_calls


def test_full_pool_order_duplicates_and_public_whitelist():
    rule = {
        "seed": 20261010,
        "repository_scope": ["org/repo"],
        "hard_title_pattern": "cycle",
        "easy_title_pattern": "empty",
    }
    rows = [
        {
            "instance_id": f"org__repo-{i}",
            "repo": "org/repo",
            "base_commit": "a" * 40,
            "problem_statement": "empty data",
            "issue_numbers": [str(i + 1)],
            "FAIL_TO_PASS": ["target"],
            "PASS_TO_PASS": ["regression"],
            "test_cmds": ["pytest"],
            "commit_urls": [f"https://example.invalid/v2/repo/{i}"],
            "patch": "answer",
            "test_patch": "hidden",
            "hints_text": "hint",
            "future_answer": "secret",
        }
        for i in range(105)
    ]
    entries = screen_pool([*rows, rows[0]], rule)
    assert len(entries) == 106
    assert entries[-1]["reason"] == "identical-duplicate"
    order = candidate_order(entries, "easy")
    assert len(order) == 105 and {e["row_index"] for e in order} == set(range(105))
    assert [e["instance_id"] for e in order] == [
        e["instance_id"] for e in candidate_order(screen_pool(list(reversed(rows)), rule), "easy")
    ]
    assert public_package(rows[0]) == {"schema_version": 1, "requests": ["empty data"]}
    conflicts = screen_pool([rows[0], {**rows[0], "patch": "different"}], rule)
    assert all(e["reason"] == "conflicting-duplicate" for e in conflicts)
    assert candidate_order(conflicts, "easy") == []
    reviewed = screen_pool(
        rows[:1],
        {
            **rule,
            "complexity_reviews": {
                rows[0]["instance_id"]: {"difficulty": "hard", "reason": "多阶段状态约束"}
            },
        },
    )
    assert reviewed[0]["difficulty"] == "hard" and reviewed[0]["difficulty_status"] == "reviewed"
    rejected = screen_pool(
        rows[:1],
        {
            **rule,
            "complexity_reviews": {
                rows[0]["instance_id"]: {
                    "difficulty": "easy",
                    "reason": "无完整目标合同",
                    "exclude_reason": "缺少回归",
                }
            },
        },
    )
    assert candidate_order(rejected, "easy") == []
