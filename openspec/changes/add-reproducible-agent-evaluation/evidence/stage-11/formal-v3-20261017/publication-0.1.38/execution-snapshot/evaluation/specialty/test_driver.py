"""在独立干净环境执行 unittest 并输出实际收集、跳过及行为失败证据。"""

import json
import sys
import unittest
from pathlib import Path
from types import FrameType
from typing import Any


def main() -> None:
    called: set[str] = set()
    executed: set[str] = set()

    def trace(frame: FrameType, event: str, arg: Any) -> None:
        if event == "call" and Path(frame.f_code.co_filename).resolve().parent == Path.cwd():
            called.add(Path(frame.f_code.co_filename).name)
            executed.add(Path(frame.f_code.co_filename).name + ":" + frame.f_code.co_name)

    suite = unittest.defaultTestLoader.discover("tests")
    sys.setprofile(trace)
    try:
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        sys.setprofile(None)
    data = {
        "run": result.testsRun,
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "failures": len(result.failures),
        "successful": result.wasSuccessful(),
        "failure_ids": [test.id() for test, _ in result.failures],
        "error_ids": [test.id() for test, _ in result.errors],
        "called": sorted(called),
        "executed": sorted(executed),
    }
    Path("/tmp/specialty-result.json" if "--container" in sys.argv else "result.json").write_text(
        json.dumps(data) + "\n"
    )


if __name__ == "__main__":
    main()
