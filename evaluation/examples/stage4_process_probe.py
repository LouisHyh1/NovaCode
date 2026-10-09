"""残留子进程机制核验；不创建 Provider、不发起模型调用。"""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from novacode.evaluation.ledger import Ledger
from novacode.evaluation.runner import command, stop_and_extract


async def main():
    root = Path(sys.argv[1])
    output = root / "process-probe"
    output.mkdir(exist_ok=False)
    env = json.loads((root / "assets/public/environment.json").read_text())
    name = "novacode-process-" + uuid4().hex
    ledger = Ledger(output / "ledger.jsonl", "stage4-process-probe")
    await command(
        "docker",
        "create",
        "--name",
        name,
        "--network",
        "none",
        "--pids-limit",
        "32",
        env["image"],
        "tail",
        "-f",
        "/dev/null",
    )
    try:
        await command("docker", "start", name)
        await command(
            "docker",
            "exec",
            name,
            "sh",
            "-c",
            "sleep 120 >/dev/null 2>&1 & echo $! > /testbed/residual.pid",
        )
        before = await command("docker", "top", name, "-eo", "pid,comm")
        assert b"sleep" in before, "未生成已脱离调用 shell 的残留进程"
        ledger.append("residual_before", processes=before.decode())
        stopped = await stop_and_extract(
            name, output, False, {"limits": {"cleanup_seconds": 20}}, ledger
        )
        assert stopped
        after = await command(
            "docker", "ps", "-a", "--filter", "name=" + name, "--format", "{{.Names}}"
        )
        assert not after.strip()
        ledger.append(
            "verification", owned_container_removed=True, residual_before=True, real_model_calls=0
        )
    finally:
        ledger.close()
        await command("docker", "rm", "-f", name)


if __name__ == "__main__":
    asyncio.run(main())
