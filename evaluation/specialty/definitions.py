"""开发专项的建设合同；本文件及正确/缺陷资产只由外部 curator 读取。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Case:
    task_id: str
    difficulty: str
    category: str
    files: dict[str, str]
    requests: list[str]
    allowed: list[str]
    reference: dict[str, str] = field(default_factory=dict)
    checks: str = ""
    answers: list[dict[str, Any]] = field(default_factory=list)
    mutants: dict[str, dict[str, str]] = field(default_factory=dict)
    records: dict[str, Any] = field(default_factory=dict)
    pressure: list[str] = field(default_factory=list)


CONFIG = """import json

def load(path):
    with open(path) as stream:
        return json.load(stream)

def resolve(file_values, overrides):
    result = {"mode": "safe", "label": "default"}
    result.update(file_values)
    result.update(overrides)
    return result
"""
CONFIG_TESTS = """import unittest
from settings import resolve

class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(resolve({}, {}), {"mode": "safe", "label": "default"})
    def test_priority(self):
        self.assertEqual(resolve({"mode": "file"}, {"mode": "cli"})["mode"], "cli")
    def test_empty(self):
        self.assertEqual(resolve({"label": "file"}, {"label": ""})["label"], "")
        self.assertIsNone(resolve({"label": "file"}, {"label": None})["label"])
"""
CANCEL = """import asyncio

class Operation:
    def __init__(self, resource):
        self.resource = resource
        self.state = "idle"
        self.started = asyncio.Event()
        self.release = asyncio.Event()
    async def run(self):
        self.state = "running"
        self.started.set()
        try:
            await self.release.wait()
        finally:
            await self.resource.close()
            self.state = "idle"
"""
CANCEL_TESTS = """import asyncio
import unittest
from operation import Operation

class CancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel(self):
        class Resource:
            count = 0
            async def close(self):
                self.count += 1
        resource = Resource()
        operation = Operation(resource)
        task = asyncio.create_task(operation.run())
        await operation.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(resource.count, 1)
        self.assertEqual(operation.state, "idle")
    async def test_normal(self):
        class Resource:
            count = 0
            async def close(self):
                self.count += 1
        resource = Resource()
        operation = Operation(resource)
        task = asyncio.create_task(operation.run())
        await operation.started.wait()
        operation.release.set()
        await task
        self.assertEqual(resource.count, 1)
        self.assertEqual(operation.state, "idle")
"""


def facts(files: list[str], symbols: list[str], relations: list[list[str]]) -> dict[str, Any]:
    return {"files": files, "symbols": symbols, "relations": relations}


def test_file(body: str) -> str:
    return "import unittest\n\nclass Contract(unittest.TestCase):\n" + body


def cases() -> list[Case]:
    s01 = Case(
        "S01",
        "easy",
        "localization",
        {
            "settings.py": CONFIG,
            "cli.py": (
                "from settings import load, resolve\nfrom runtime import "
                "start\n\ndef main(path, overrides):\n    return start(reso"
                "lve(load(path), overrides))\n"
            ),
            "runtime.py": 'def start(values):\n    return {"active_mode": values["mode"]}\n',
        },
        [
            (
                "只读定位：说明配置文件中的 mode 从读取到运行"
                "生效的路径。最终 JSON 包含 files（相对"
                "路径）、s"
                "ymbols（文件路径:符号）、relations（[源符号,目标符号] 数组）；可加自由解释。"
            )
        ],
        [],
        answers=[
            facts(
                ["settings.py", "cli.py", "runtime.py"],
                ["settings.py:load", "settings.py:resolve", "cli.py:main", "runtime.py:start"],
                [
                    ["settings.py:load", "settings.py:resolve"],
                    ["settings.py:resolve", "runtime.py:start"],
                ],
            ),
            facts(
                ["settings.py", "cli.py", "runtime.py"],
                ["settings.py:load", "settings.py:resolve", "cli.py:main", "runtime.py:start"],
                [
                    ["cli.py:main", "settings.py:load"],
                    ["cli.py:main", "settings.py:resolve"],
                    ["cli.py:main", "runtime.py:start"],
                ],
            ),
        ],
    )
    s02 = Case(
        "S02",
        "medium",
        "localization",
        {
            "storage.py": (
                "class MissingRecord(Exception):\n    pass\n\nasync def fet"
                "ch(key):\n    raise MissingRecord(key)\n"
            ),
            "service.py": (
                "from storage import fetch, MissingRecord\n\nclass Request"
                "Error(Exception):\n    pass\n\nasync def get(key):\n    try"
                ":\n        return await fetch(key)\n    except MissingRec"
                'ord as exc:\n        raise RequestError("record unavaila'
                'ble") from exc\n'
            ),
            "cli.py": (
                "from service import get, RequestError\n\nasync def main(k"
                "ey):\n    try:\n        return await get(key)\n    except "
                "RequestError as exc:\n        return display(str(exc))\n\n"
                'def display(message):\n    return "ERROR: " + message\n\nd'
                'ef network_error(exc):\n    return "NETWORK: " + str(exc'
                ")\n"
            ),
        },
        [
            (
                "只读定位：key 缺失错误如何产生、转换并在异步 "
                "CLI 展示？最终 JSON 用 files、sy"
                "mbols"
                "（路径:符号）、relations（[源,目标]）表示实际路径，不修改文件。"
            )
        ],
        [],
        answers=[
            facts(
                ["storage.py", "service.py", "cli.py"],
                ["storage.py:fetch", "service.py:get", "cli.py:main", "cli.py:display"],
                [
                    ["storage.py:fetch", "service.py:get"],
                    ["service.py:get", "cli.py:main"],
                    ["cli.py:main", "cli.py:display"],
                ],
            ),
            facts(
                ["storage.py", "service.py", "cli.py"],
                [
                    "storage.py:fetch",
                    "storage.py:MissingRecord",
                    "service.py:get",
                    "service.py:RequestError",
                    "cli.py:main",
                    "cli.py:display",
                ],
                [
                    ["storage.py:fetch", "storage.py:MissingRecord"],
                    ["storage.py:MissingRecord", "service.py:RequestError"],
                    ["service.py:RequestError", "cli.py:display"],
                ],
            ),
        ],
    )
    s03 = Case(
        "S03",
        "easy",
        "test-addition",
        {
            "settings.py": CONFIG,
            "tests/test_existing.py": test_file(
                "    def test_import(self):\n        import settings\n    "
                "    self.assertTrue(callable(settings.resolve))\n"
            ),
        },
        [
            (
                "仅新增 tests/test_added.py，使"
                "用标准库 unittest 给 settings."
                "resol"
                "ve 补充测试：配置覆盖优先级、缺省值、显式空字符"
                "串和 None。生产文件和已有测试保持不变；测试需"
                "在正确实现"
                "通过并检出缺陷。"
            )
        ],
        ["tests/test_added.py"],
        {"tests/test_added.py": CONFIG_TESTS},
        mutants={
            "reversed-priority": {
                "settings.py": CONFIG.replace(
                    "result.update(file_values)\n    result.update(overrides)",
                    "result.update(overrides)\n    result.update(file_values)",
                )
            },
            "lost-empty": {
                "settings.py": CONFIG.replace(
                    "result.update(overrides)",
                    "result.update({k: v for k, v in overrides.items() if v})",
                )
            },
        },
    )
    s04 = Case(
        "S04",
        "medium",
        "test-addition",
        {
            "operation.py": CANCEL,
            "tests/test_existing.py": test_file(
                "    def test_import(self):\n        import operation\n   "
                "     self.assertTrue(callable(operation.Operation))\n"
            ),
        },
        [
            (
                "仅新增 tests/test_added.py，用"
                "标准库 unittest 给 Operation "
                "补充正常完"
                "成和取消后的资源恰好关闭一次、状态恢复 idle "
                "测试。通过 started/release 事件建"
                "立确定性同"
                "步，不依赖 sleep 碰巧时序；生产文件与原测试保持不变。"
            )
        ],
        ["tests/test_added.py"],
        {"tests/test_added.py": CANCEL_TESTS},
        mutants={
            "missing-close": {
                "operation.py": CANCEL.replace("await self.resource.close()", "pass")
            },
            "wrong-state": {
                "operation.py": CANCEL.replace('self.state = "idle"\n', 'self.state = "running"\n')
            },
        },
        pressure=["deterministic-cancellation"],
    )
    clamp = (
        "def clamp(value, lower, upper):\n    if lower > upper:\n "
        '       raise ValueError("reversed range")\n    return mi'
        "n(max(value, lower), upper)\n"
    )
    clamp_checks = test_file(
        "    def test_bounds(self):\n        from bounds import c"
        "lamp\n        for value, expected in [(-1, 0), (0, 0), ("
        "5, 5), (10, 10), (11, 10)]:\n            self.assertEqua"
        "l(clamp(value, 0, 10), expected)\n        self.assertEqu"
        "al(clamp(4, 3, 3), 3)\n    def test_reversed(self):\n    "
        "    from bounds import clamp\n        with self.assertRa"
        "ises(ValueError):\n            clamp(1, 5, 0)\n"
    )
    s05 = Case(
        "S05",
        "medium",
        "tool-failure",
        {
            "bounds.py": clamp.replace(
                "min(max(value, lower), upper)", "max(min(value, lower), upper)"
            )
        },
        [
            (
                "修复 bounds.clamp 的区间夹取边界错误"
                "；保留 lower>upper 时 ValueEr"
                "ror。内"
                "容搜索 grep 的首次有效执行会收到一次规定错误"
                "，可重试或使用其他合法工具。只修改 bounds."
                "py。"
            )
        ],
        ["bounds.py"],
        {"bounds.py": clamp},
        clamp_checks,
        pressure=["first-grep-error"],
    )
    chunks = (
        "def chunks(values, size):\n    if size <= 0:\n        rai"
        'se ValueError("size must be positive")\n    return [valu'
        "es[i:i+size] for i in range(0, len(values), size)]\n"
    )
    chunks_checks = test_file(
        "    def test_chunks(self):\n        from batches import "
        "chunks\n        self.assertEqual(chunks([1, 2, 3, 4, 5],"
        " 2), [[1, 2], [3, 4], [5]])\n        self.assertEqual(ch"
        "unks([], 2), [])\n        self.assertEqual(chunks([1], 1"
        "), [[1]])\n    def test_invalid(self):\n        from batc"
        "hes import chunks\n        for size in (0, -1):\n        "
        "    with self.assertRaises(ValueError):\n               "
        " chunks([1], size)\n"
    )
    s06 = Case(
        "S06",
        "medium",
        "tool-failure",
        {
            "batches.py": chunks.replace(
                "range(0, len(values), size)", "range(0, len(values) - size + 1, size)"
            ),
            "tests/test_existing.py": chunks_checks,
        },
        [
            (
                "修复 batches.chunks 丢弃尾部短组的"
                "问题，保留空输入与非法 size 行为。项目标准命"
                "令是 py"
                "thon -m unittest discover"
                " -s tests -v，其首次有效调用会真实超时"
                "并清理所属"
                "子进程；可恢复或走合法替代方案。只修改 batches.py。"
            )
        ],
        ["batches.py"],
        {"batches.py": chunks},
        chunks_checks,
        pressure=["first-test-timeout"],
    )
    formatter = 'def render(value):\n    return {"value": value, "kind": "counter"}\n'
    s07 = Case(
        "S07",
        "easy",
        "long-context",
        {"counter.py": formatter},
        [
            (
                "后续操作必须保持 render 的公共返回字段 v"
                "alue/kind 及 kind=counter；"
                "不改函数名"
                "。现在阅读 counter.py 并记录兼容约束。"
            ),
            *[
                f"阅读 notes/block-{i}.txt，简要报告该块条目数；这一轮不修改代码。"
                for i in range(1, 5)
            ],
            "在 counter.py 新增 increment(value, step=1)，通过原有格式返回 value+step。",
        ],
        ["counter.py"],
        {
            "counter.py": formatter
            + "\ndef increment(value, step=1):\n    return render(value + step)\n"
        },
        test_file(
            "    def test_counter(self):\n        from counter import"
            " render, increment\n        self.assertEqual(render(2), "
            '{"value": 2, "kind": "counter"})\n        self.assertEqu'
            'al(increment(2), {"value": 3, "kind": "counter"})\n     '
            '   self.assertEqual(increment(2, -2), {"value": 0, "kin'
            'd": "counter"})\n'
        ),
        pressure=["early-constraints", "scripted-history"],
    )
    s08 = Case(
        "S08",
        "medium",
        "long-context",
        {"policy.py": "def retry_delay(service):\n    return 1\n"},
        [
            "阅读 data/service-policy.txt，记录其中服务的重试退避事实；可分段阅读，不修改该文件。",
            *[
                f"阅读 notes/block-{i}.txt，简要概括，再把块编号写入 review-{i}.txt。"
                for i in range(1, 5)
            ],
            "根据之前读到的原始策略，在 policy.py 实"
            "现 retry_delay(service)，未知"
            "服务抛 KeyError。可重新读取原文件。",
        ],
        ["policy.py", *[f"review-{i}.txt" for i in range(1, 5)]],
        {
            "policy.py": (
                'def retry_delay(service):\n    return {"uploads": 17, "b'
                'illing": 29, "catalog": 43}[service]\n'
            )
        },
        test_file(
            "    def test_facts(self):\n        from policy import re"
            'try_delay\n        for key, delay in {"uploads": 17, "bi'
            'lling": 29, "catalog": 43}.items():\n            self.as'
            "sertEqual(retry_delay(key), delay)\n        with self.as"
            'sertRaises(KeyError):\n            retry_delay("missing"'
            ")\n"
        ),
        pressure=["large-file", "scripted-history"],
    )
    migration = (
        'def migrate(data):\n    return {"endpoint": data["host"]'
        ', "retries": data.get("retries", 3), "timeout": data.ge'
        't("timeout", 5)}\n'
    )
    client = "from config import migrate\n\ndef request_options(data):\n    return migrate(data)\n"
    s09 = Case(
        "S09",
        "hard",
        "long-context",
        {"config.py": migration, "client.py": client},
        [
            (
                "约束：迁移不得修改输入，保留未知扩展字段，显式 r"
                "etries=0/timeout=0 不得丢失；旧"
                " host"
                " 仍可用。实现 config.migrate 的 endpoint 优先于 host 迁移。"
            ),
            *[f"阅读 notes/block-{i}.txt并简要概括。" for i in range(1, 3)],
            (
                "在 client.request_options "
                "增加校验：endpoint 非空字符串；retri"
                "es 是非"
                "负整数且不是 bool；timeout 是非负数且"
                "不是 bool。非法抛 ValueError，保持"
                "先前迁移成"
                "果。"
            ),
            *[f"阅读 notes/block-{i}.txt并简要概括。" for i in range(3, 5)],
            (
                "新增 client.connect(data)，返"
                "回 {'url': endpoint, 'atte"
                "mpts'"
                ": retries+1, 'timeout': timeout}，沿用现有迁移和校验。"
            ),
        ],
        ["config.py", "client.py"],
        {
            "config.py": (
                'def migrate(data):\n    result = dict(data)\n    result["'
                'endpoint"] = result.get("endpoint", result.get("host"))'
                '\n    result.setdefault("retries", 3)\n    result.setdefa'
                'ult("timeout", 5)\n    return result\n'
            ),
            "client.py": client.replace(
                "    return migrate(data)",
                (
                    "    result = migrate(data)\n    if not isinstance(result"
                    '["endpoint"], str) or not result["endpoint"]:\n        r'
                    'aise ValueError("endpoint")\n    if type(result["retries'
                    '"]) is not int or result["retries"] < 0:\n        raise '
                    'ValueError("retries")\n    if type(result["timeout"]) no'
                    't in (int, float) or result["timeout"] < 0:\n        rai'
                    'se ValueError("timeout")\n    return result'
                ),
            )
            + (
                "\ndef connect(data):\n    options = request_options(data)"
                '\n    return {"url": options["endpoint"], "attempts": op'
                'tions["retries"] + 1, "timeout": options["timeout"]}\n'
            ),
        },
        test_file(
            "    def test_early(self):\n        from config import mi"
            'grate\n        data = {"host": "old", "endpoint": "new",'
            ' "retries": 0, "timeout": 0, "extension": 7}\n        be'
            "fore = dict(data)\n        result = migrate(data)\n      "
            "  self.assertEqual(result, before)\n        self.assertE"
            'qual(data, before)\n        self.assertEqual(migrate({"h'
            'ost": "old"})["endpoint"], "old")\n    def test_middle(s'
            "elf):\n        from client import request_options\n      "
            '  for data in ({"host": ""}, {"host": "ok", "retries": '
            'True}, {"host": "ok", "retries": -1}, {"host": "ok", "t'
            'imeout": -1}, {"host": "ok", "timeout": False}):\n      '
            "      with self.assertRaises(ValueError):\n             "
            "   request_options(data)\n    def test_final(self):\n    "
            "    from client import connect\n        self.assertEqual"
            '(connect({"host": "old", "retries": 0, "timeout": 0}), '
            '{"url": "old", "attempts": 1, "timeout": 0})\n        se'
            'lf.assertEqual(connect({"endpoint": "new"}), {"url": "n'
            'ew", "attempts": 4, "timeout": 5})\n'
        ),
        pressure=["multi-stage", "scripted-history"],
    )
    records = {
        "ci_commit": {
            "c731": {
                "commit": "c731",
                "job": "unit-linux",
                "reason": "ASSERT_EMPTY",
                "test": "tests/test_normalize.py::empty",
                "source": "ci",
            },
            "c732": {"commit": "c732", "job": "type-check", "reason": "TYPE_ERROR", "source": "ci"},
        },
        "ci_test": {
            "case-884": {
                "case_id": "case-884",
                "commit": "c884",
                "job": "unit-windows",
                "reason": "PATH_SEPARATOR",
                "source": "ci-tests",
            }
        },
        "ci_deploy": {
            "case-884": {
                "case_id": "case-884",
                "commit": "c885",
                "job": "deploy",
                "reason": "NETWORK",
                "source": "deployment",
            }
        },
        "issue_lookup": {"ISSUE-42": {"issue": "ISSUE-42", "commit": "c731", "source": "issues"}},
    }
    s10 = Case(
        "S10",
        "easy",
        "tool-discovery",
        {"records.json": ""},
        [
            (
                "只读查询提交 c731 对应的 CI 失败任务和原"
                "因；可发现并使用本地查询工具或采用其他合法路径。最"
                "终 JSO"
                "N 含 commit、job、reason、source。"
            )
        ],
        [],
        answers=[{"commit": "c731", "job": "unit-linux", "reason": "ASSERT_EMPTY", "source": "ci"}],
        records={"ci_commit": records["ci_commit"]},
        pressure=["builtin-discovery"],
    )
    s11 = Case(
        "S11",
        "medium",
        "tool-discovery",
        {"records.json": ""},
        [
            (
                "只读定位 CI 测试记录 case-884 的失败"
                "，注意部署记录不是测试记录。最终 JSON 含 c"
                "ase_i"
                "d、commit、job、reason、source，可使用本地工具或合法替代路径。"
            )
        ],
        [],
        answers=[records["ci_test"]["case-884"]],
        records={"ci_test": records["ci_test"], "ci_deploy": records["ci_deploy"]},
        pressure=["mcp-discovery", "similar-responsibilities"],
    )
    normalize = (
        "def normalize(values):\n    if not values:\n        retur"
        "n []\n    total = sum(values)\n    if total == 0:\n       "
        " return [0 for _ in values]\n    return [value / total f"
        "or value in values]\n"
    )
    s12 = Case(
        "S12",
        "hard",
        "tool-discovery",
        {"normalize.py": normalize.replace("return []", "return [0]"), "records.json": ""},
        [
            (
                "关联 ISSUE-42、对应提交和 CI 失败测试"
                "，并修复 normalize.py 对空列表的错误"
                "，保持零和"
                "及普通归一化行为。仅修改 normalize.py"
                "。最终 JSON 含 issue、commit、j"
                "ob、re"
                "ason、test、source（CI 来源）。可用本地查询能力或合法替代。"
            )
        ],
        ["normalize.py"],
        {"normalize.py": normalize},
        test_file(
            "    def test_normalize(self):\n        from normalize im"
            "port normalize\n        self.assertEqual(normalize([]), "
            "[])\n        self.assertEqual(normalize([0, 0]), [0, 0])"
            "\n        self.assertEqual(normalize([1, 3]), [0.25, 0.7"
            "5])\n"
        ),
        answers=[{"issue": "ISSUE-42", **records["ci_commit"]["c731"]}],
        records={"issue_lookup": records["issue_lookup"], "ci_commit": records["ci_commit"]},
        pressure=["builtin-discovery", "mcp-discovery", "cross-record"],
    )
    result = [s01, s02, s03, s04, s05, s06, s07, s08, s09, s10, s11, s12]
    s03.mutants["wrong-default"] = {
        "settings.py": CONFIG.replace('"mode": "safe"', '"mode": "fast"')
    }
    for i in range(1, 5):
        s08.reference[f"review-{i}.txt"] = f"{i}\n"
    s08.checks += (
        "    def test_middle_artifacts(self):\n"
        "        from pathlib import Path\n"
        "        for i in range(1, 5):\n"
        "            self.assertIn(str(i), Path('review-%s.txt' % i).read_text())\n"
    )
    for case in (s07, s08, s09):
        for i in range(1, 5):
            case.files[f"notes/block-{i}.txt"] = "".join(
                f"记录 {i}-{n:04d}：服务审计条目已归档。\n" for n in range(360)
            )
    s08.files["data/service-policy.txt"] = "".join(
        f"审计条目 {n:04d}：常规记录无需修改。\n"
        if n not in (173, 681, 1177)
        else {
            173: "service=uploads retry_delay=17\n",
            681: "service=billing retry_delay=29\n",
            1177: "service=catalog retry_delay=43\n",
        }[n]
        for n in range(1440)
    )
    for case in (s10, s11, s12):
        import json

        case.files["records.json"] = json.dumps(case.records, ensure_ascii=False, indent=2) + "\n"
    return result
