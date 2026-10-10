"""固定全池抽样与公开白名单的必要反例。"""

from novacode.evaluation.live import candidate_order, public_package, screen_pool


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


def test_official_blob_hash_mismatch_is_not_imported(tmp_path, monkeypatch):
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


def test_official_blob_resumes_verified_range(tmp_path, monkeypatch):
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
