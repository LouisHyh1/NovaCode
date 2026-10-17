"""从交付归档重建只读报告输入，不调用模型或判题器。"""

from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path

from novacode.evaluation.contracts import require
from novacode.evaluation.frozen import digest


def restore(index: Path, output: Path) -> None:
    data = json.loads(index.read_text())
    require(data["schema_version"] == 1, "归档索引版本无效")
    output.mkdir(parents=True, exist_ok=False)
    (output / "runs").mkdir()
    (output / "identity.json").write_text(json.dumps(data["identity"]))
    for name, receipt in data["archives"].items():
        require(Path(name).name == name and name not in (".", ".."), "运行目录名无效")
        filename = receipt["filename"]
        require(Path(filename).name == filename, "归档文件名无效")
        archive = index.parent / "archives" / filename
        require(digest(archive) == receipt["sha256"], "原始归档指纹变化")
        folder = output / "runs" / name
        folder.mkdir()
        with tarfile.open(archive) as raw:
            require(set(raw.getnames()) == set(receipt["files"]), "归档成员集合变化")
            require(
                all(
                    not Path(member).is_absolute() and ".." not in Path(member).parts
                    for member in receipt["files"]
                ),
                "归档路径越界",
            )
            raw.extractall(folder, filter="data")
        for member, sha in receipt["files"].items():
            require(digest(folder / member) == sha, "归档成员指纹变化")
        (folder / "archive.json").write_text(
            json.dumps(
                {
                    "path": str(archive.resolve()),
                    "sha256": receipt["sha256"],
                    "files": receipt["files"],
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    restore(args.index.resolve(), args.output.resolve())
