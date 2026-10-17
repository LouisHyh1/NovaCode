"""最小化补丁提取参数超限复现；只创建临时fixture，不调用模型。"""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from evaluation.specialty.build import initialize
from novacode.evaluation.artifacts import extract_patch

with tempfile.TemporaryDirectory(prefix="novacode-argmax-repro-") as directory:
    root = Path(directory)
    base = initialize(root, {".gitignore": "keep.txt\n", "main.py": "value = 1\n"})
    (root / "keep.txt").write_text("unchanged ignored data")
    (root / "main.py").write_text("value = 2\n")
    sha = hashlib.sha256((root / "keep.txt").read_bytes()).hexdigest()
    control = extract_patch(root, base, {"keep.txt": sha})
    # Path会规范化./，只用一个物理文件即可复现同样的聚合参数长度边界。
    ignored = {"keep.txt": sha, **{"./" * n + "keep.txt": sha for n in range(500, 1900)}}
    try:
        extract_patch(root, base, ignored)
    except OSError as error:
        assert error.errno == 7
    else:
        raise AssertionError("原提取器未触发预期E2BIG")
    check_output = subprocess.check_output

    def streamed_pathspec(args, **kwargs):
        if args[0] == "git" and "add" in args and "--" in args:
            split = args.index("--")
            paths = b"\0".join(p.encode() for p in args[split + 1:]) + b"\0"
            args = [*args[:split], "--pathspec-from-file=-", "--pathspec-file-nul"]
            kwargs["input"] = paths
        return check_output(args, **kwargs)

    subprocess.check_output = streamed_pathspec
    try:
        corrected = extract_patch(root, base, ignored)
    finally:
        subprocess.check_output = check_output
    assert corrected == control
    print(json.dumps({"original_errno": 7, "streamed_pathspec_matches_control": True,
                      "ignored_entries": len(ignored), "fixture_physical_files": 3}))
