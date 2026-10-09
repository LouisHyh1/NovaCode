"""将独立 CPython、锁定 wheel 和工具核验脚本打包，不包含用户配置或题目答案。"""

import argparse
import hashlib
import json
import tarfile
from pathlib import Path


def pack(args):
    assert args.python_home.is_dir()
    assert args.uv.is_file()
    wheels = sorted(args.wheelhouse.glob("*.whl"))
    assert wheels and any(path.name.startswith("novacode-") for path in wheels)
    with tarfile.open(args.output, "x") as archive:
        archive.add(args.python_home, arcname="opt/novacode/python")
        archive.add(args.uv, arcname="opt/novacode/uv")
        archive.add(args.wheelhouse, arcname="opt/novacode/wheels")
        archive.add(Path(__file__).with_name("tool_smoke.py"), arcname="opt/novacode/tool-smoke.py")
    manifest = {
        "archive_bytes": args.output.stat().st_size,
        "archive_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "python": str(args.python_home),
        "assets": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in wheels},
    }
    args.output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"archive_bytes": manifest["archive_bytes"], "wheels": len(wheels)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-home", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    pack(parser.parse_args())
