"""正式编排的产物保留与账本重建机制检查。"""

import json
import shutil
import tarfile

import pytest

from evaluation.formal.replay import restore
from evaluation.formal.report import compare, distribution, paired_bootstrap
from evaluation.formal.run import archive_run, save, snapshot
from evaluation.frozen.definitions import localization
from novacode.evaluation.ledger import Ledger


def test_archive_verified_before_releasing_reconstructable_workspace(tmp_path):
    from novacode.evaluation.artifacts import capture_baseline, extract_patch
    from tests.evaluation.test_runner import make_repo

    run = tmp_path / "run"
    run.mkdir()
    workspace = run / "workspace"
    git, base = make_repo(workspace)
    (workspace / "initial.py").write_text("from frozen image")
    git("add", "initial.py")
    git("commit", "-qm", "initial image")
    base = git("rev-parse", "HEAD").strip()
    capture_baseline(workspace, base, run / "baseline")
    (workspace / "value.txt").write_text("updated")
    state = run / "workspace/.novacode"
    state.mkdir(parents=True)
    (state / "session.jsonl").write_text("retained")
    save(run / "result.json", {"termination": "timeout"})
    patch = extract_patch(workspace, base, run / "baseline")[0]
    (run / "submission.diff").write_text(patch)
    archive = tmp_path / "raw.tar.gz"
    archive_run(run, archive)
    assert not (run / "workspace").exists()
    with tarfile.open(archive) as raw:
        assert "workspace/.novacode/session.jsonl" in raw.getnames()
        assert "workspace/initial.py" not in raw.getnames()
        assert raw.extractfile("submission.diff").read() == patch.encode()
    assert json.loads((run / "archive.json").read_text())["sha256"]
    delivery = tmp_path / "delivery"
    (delivery / "archives").mkdir(parents=True)
    shutil.copy2(archive, delivery / "archives/raw.tar.gz")
    receipt = json.loads((run / "archive.json").read_text())
    save(
        delivery / "index.json",
        {
            "schema_version": 1,
            "identity": {"test": "identity"},
            "archives": {"run": {**receipt, "filename": "raw.tar.gz"}},
        },
    )
    restored = tmp_path / "restored"
    restore(delivery / "index.json", restored)
    assert (restored / "runs/run/workspace/.novacode/session.jsonl").read_text() == "retained"
    assert (restored / "runs/run/submission.diff").read_text() == patch
    with pytest.raises(FileExistsError):
        restore(delivery / "index.json", restored)
    with pytest.raises(ValueError, match="覆盖"):
        archive_run(run, archive)


def test_preflight_missing_worker_is_unknown_and_raw_result_never_overwritten(tmp_path):
    save(tmp_path / "result.json", {"termination": None})
    ledger = Ledger(tmp_path / "orchestration.jsonl", "blocked")
    ledger.append("environment_blocked", reason="not started")
    ledger.close()
    data = snapshot(tmp_path)
    assert data["metrics"] is None and not data["cost_complete"]
    with pytest.raises(FileExistsError):
        save(tmp_path / "result.json", {"termination": "completed"})


def test_localization_publicly_defines_all_hidden_field_encodings():
    for case in localization():
        request = case.requests[0]
        assert "files 为仓库根相对路径的字符串数组" in request
        assert "路径:符号字符串数组" in request
        assert "[源符号,目标符号] 二元字符串数组" in request
        assert "不列 import 边、库方法或外部参数" in request
        # 格式说明不包含期望路径或符号，原业务素材保持不变。
        for symbol in case.answers[0]["symbols"]:
            assert symbol not in request


def test_task_paired_bootstrap_preserves_three_repeats_and_marks_degeneracy():
    samples = []
    for task in range(6):
        for config in ("full", "no-compression"):
            for repeat in (1, 2, 3):
                passed = repeat <= (2 if config == "full" else 1)
                samples.append(
                    {
                        "result": {
                            "run_id": f"run-{task}-{config}-{repeat}",
                            "schema_version": 1,
                            "campaign_id": "fixture",
                            "task_id": str(task),
                            "task_sha256": "a" * 64,
                            "initial_sha256": "b" * 64,
                            "config_id": config,
                            "repeat_id": repeat,
                            "state": "RECORDED",
                            "termination": "completed",
                            "agent_run_ids": ["agent"],
                            "completed_requests": 1,
                            "acceptance": "passed" if passed else "failed",
                            "scope": "passed",
                            "cleanup": "passed",
                        }
                    }
                )
    result = compare(
        samples, list(map(str, range(6))), "no-compression", "strict_percentage_points", 20261012
    )
    assert result["independent_tasks"] == 6
    assert result["point"] == pytest.approx(100 / 3)
    assert result["degenerate"] and result["ci"] == pytest.approx([100 / 3, 100 / 3])
    assert result == compare(
        list(reversed(samples)),
        list(map(str, range(6))),
        "no-compression",
        "strict_percentage_points",
        20261012,
    )
    assert not paired_bootstrap({"one": 100}, seed=1)["reliable"]
    assert paired_bootstrap({}, seed=1)["point"] is None
    varied = paired_bootstrap({"one": -100, "two": 100}, seed=1)
    assert not varied["degenerate"] and varied["point"] == 0
    assert varied["ci"] == [-100, 100]


def test_distribution_p90_is_nearest_rank_including_failures():
    assert distribution(list(range(1, 11))) == {"n": 10, "mean": 5.5, "median": 5.5, "p90": 9}
    assert distribution([])["p90"] is None
