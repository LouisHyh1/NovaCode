"""冻结专项：独立业务事实和缺陷家族，共享构建器不共享开发题答案。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evaluation.specialty.definitions import Case, facts, test_file


@dataclass
class FrozenCase(Case):
    family: str = ""
    test_target: str = ""


def assertions(module: str, body: str) -> str:
    return test_file(f"    def test_contract(self):\n        import {module} as m\n" + body)


def localization() -> list[FrozenCase]:
    return [
        FrozenCase(
            "FS01",
            "easy",
            "localization",
            {
                "codec.py": (
                    "import json\ndef pack(value):\n    return json.dumps(value).encode('utf-8')\n"
                ),
                "transport.py": (
                    "from codec import pack\ndef send(val"
                    "ue, sink):\n    return sink.write(pa"
                    "ck(value))\n"
                ),
                "entry.py": (
                    "from transport import send\ndef publ"
                    "ish(event, sink):\n    return send(e"
                    "vent, sink)\n"
                ),
            },
            [
                (
                    "只读追踪事件 publish 到 UTF-8 字节写入的调用路径。最终"
                    " JSON 含 files、symbols（路径:符号）、relati"
                    "ons（[源,目标]数组）。"
                )
            ],
            [],
            answers=[
                facts(
                    ["entry.py", "transport.py", "codec.py"],
                    ["entry.py:publish", "transport.py:send", "codec.py:pack"],
                    [
                        ["entry.py:publish", "transport.py:send"],
                        ["transport.py:send", "codec.py:pack"],
                    ],
                )
            ],
            family="event-wire-encoding",
        ),
        FrozenCase(
            "FS02",
            "easy",
            "localization",
            {
                "routes.py": (
                    "def fallback(event):\n    return 'unknown'\ndef"
                    " choose(key, handlers):\n    return handlers.g"
                    "et(key, fallback)\n"
                ),
                "dispatch.py": (
                    "from routes import choose\ndef dispatch(key, e"
                    "vent, handlers):\n    return choose(key, handl"
                    "ers)(event)\n"
                ),
            },
            [
                (
                    "只读定位未知路由如何选取 fallback 并执行。最终 JSON 用"
                    " files、symbols、relations 给出 dispatc"
                    "her、选择器和回退函数的关系。"
                )
            ],
            [],
            answers=[
                facts(
                    ["routes.py", "dispatch.py"],
                    ["dispatch.py:dispatch", "routes.py:choose", "routes.py:fallback"],
                    [
                        ["dispatch.py:dispatch", "routes.py:choose"],
                        ["routes.py:choose", "routes.py:fallback"],
                    ],
                )
            ],
            family="dispatch-fallback-selection",
        ),
        FrozenCase(
            "FS03",
            "medium",
            "localization",
            {
                "cache.py": (
                    "def stale(entry, now):\n    return entry['expi"
                    "res'] <= now\ndef evict(cache, key):\n    retur"
                    "n cache.pop(key, None)\n"
                ),
                "reader.py": (
                    "from cache import stale, evict\ndef read(cache"
                    ", key, now, loader):\n    entry = cache.get(ke"
                    "y)\n    if entry is not None and stale(entry, "
                    "now):\n        evict(cache, key)\n        entry"
                    " = None\n    return loader(key) if entry is No"
                    "ne else entry['value']\n"
                ),
            },
            [
                (
                    "只读追踪过期缓存 read→判断→驱逐后重新装载的路径；loader "
                    "为外部参数，不列作文件符号。最终 JSON 含 files、symbo"
                    "ls、relations。"
                )
            ],
            [],
            answers=[
                facts(
                    ["cache.py", "reader.py"],
                    ["reader.py:read", "cache.py:stale", "cache.py:evict"],
                    [["reader.py:read", "cache.py:stale"], ["reader.py:read", "cache.py:evict"]],
                )
            ],
            family="ttl-cache-expiry-flow",
        ),
        FrozenCase(
            "FS04",
            "medium",
            "localization",
            {
                "journal.py": (
                    "def snapshot(store):\n    return dict(store)\nd"
                    "ef restore(store, before):\n    store.clear()\n"
                    "    store.update(before)\n"
                ),
                "batch.py": (
                    "from journal import snapshot, restore\ndef app"
                    "ly(store, actions):\n    before = snapshot(sto"
                    "re)\n    try:\n        for action in actions:\n "
                    "           action(store)\n    except Exception"
                    ":\n        restore(store, before)\n        rais"
                    "e\n"
                ),
            },
            [
                (
                    "只读追踪批处理 action 抛错时快照和回滚调用"
                    "关系。最终 JSON 含 files、symbol"
                    "s、relations；不把正常完成当作回滚。"
                )
            ],
            [],
            answers=[
                facts(
                    ["journal.py", "batch.py"],
                    ["batch.py:apply", "journal.py:snapshot", "journal.py:restore"],
                    [
                        ["batch.py:apply", "journal.py:snapshot"],
                        ["batch.py:apply", "journal.py:restore"],
                    ],
                )
            ],
            family="batch-journal-rollback-flow",
        ),
    ]


def test_addition() -> list[FrozenCase]:
    interval = "def overlaps(a, b):\n    return max(a[0], b[0]) < min(a[1], b[1])\n"
    grouping = (
        "def group(pairs):\n    result = {}\n    for key"
        ", value in pairs:\n        result.setdefault(k"
        "ey, []).append(value)\n    return result\n"
    )
    topo = """def order(edges):
    pending = {k: set(v) for k, v in edges.items()}
    if any(dep not in pending for deps in pending.values() for dep in deps):
        raise KeyError('missing dependency')
    result = []
    while pending:
        ready = sorted(k for k, deps in pending.items() if not deps)
        if not ready:
            raise ValueError('cycle')
        for key in ready:
            result.append(key)
            del pending[key]
        for deps in pending.values():
            deps.difference_update(ready)
    return result
"""
    csv = (
        "import csv, io\ndef encode(rows):\n    stream ="
        " io.StringIO(newline='')\n    writer = csv.wri"
        "ter(stream)\n    writer.writerows(rows)\n    re"
        "turn stream.getvalue()\n"
    )
    specs = [
        (
            "FS05",
            "easy",
            "intervals.py",
            interval,
            "overlaps",
            "half-open-overlap",
            "给半开区间 overlaps 补测：相交、相接不相交、空区间不相交、包含和负数端点。",
            (
                "        self.assertTrue(m.overlaps((0, 4), (2"
                ", 7)))\n        self.assertFalse(m.overlaps((0"
                ", 2), (2, 4)))\n        self.assertFalse(m.ove"
                "rlaps((1, 1), (0, 3)))\n        self.assertTru"
                "e(m.overlaps((-5, 5), (-2, 0)))\n"
            ),
            {
                "closed-end": interval.replace(" < ", " <= "),
                "inverted": interval.replace(" < ", " > "),
            },
        ),
        (
            "FS06",
            "easy",
            "grouping.py",
            grouping,
            "group",
            "stable-multimap-aggregation",
            "给 group 补测：重复 key 保留所有值及顺序、不同 key、空生成器和输入不改写。",
            (
                "        pairs = [('a', 1), ('b', 0), ('a', 2)"
                "]\n        self.assertEqual(m.group(iter(pairs"
                ")), {'a': [1, 2], 'b': [0]})\n        self.ass"
                "ertEqual(pairs, [('a', 1), ('b', 0), ('a', 2)"
                "])\n        self.assertEqual(m.group(iter([]))"
                ", {})\n"
            ),
            {
                "overwrite": grouping.replace(
                    "result.setdefault(key, []).append(value)", "result[key] = [value]"
                ),
                "reverse": grouping.replace(".append(value)", ".insert(0, value)"),
            },
        ),
        (
            "FS07",
            "medium",
            "graph.py",
            topo,
            "order",
            "topological-order-and-cycle",
            (
                "给 order 补测：层内按字母排序、前置依赖、缺"
                "失依赖 KeyError、环 ValueError"
                "、空图和输入不改写。"
            ),
            (
                "        data = {'c': ['a', 'b'], 'b': [], 'a'"
                ": []}\n        before = {k: list(v) for k, v i"
                "n data.items()}\n        self.assertEqual(m.or"
                "der(data), ['a', 'b', 'c'])\n        self.asse"
                "rtEqual(data, before)\n        self.assertEqua"
                "l(m.order({}), [])\n        with self.assertRa"
                "ises(Exception) as caught: m.order({'a': ['x'"
                "]})\n        self.assertIsInstance(caught.exce"
                "ption, KeyError)\n        with self.assertRais"
                "es(Exception) as caught: m.order({'a': ['b'],"
                " 'b': ['a']})\n        self.assertIsInstance(c"
                "aught.exception, ValueError)\n"
            ),
            {
                "wrong-order": topo.replace(
                    "ready = sorted(k for k, deps in pending.items() if not deps)",
                    "ready = sorted(k for k, deps in pending.items() if not deps)[::-1]",
                ),
                "missing-validation": topo.replace(
                    "raise KeyError('missing dependency')", "raise ValueError('missing dependency')"
                ),
                "cycle-type": topo.replace("raise ValueError('cycle')", "raise KeyError('cycle')"),
            },
        ),
        (
            "FS08",
            "medium",
            "csvwire.py",
            csv,
            "encode",
            "csv-one-shot-escaping",
            (
                "给 encode 补测：仅消费一次生成器、逗号/引"
                "号/字段内换行、空输入，通过 csv.reader"
                " 读回原字段。"
            ),
            (
                "        import csv, io\n        rows = [['a,b'"
                ", 'say \"hi\"', 'two\\nlines'], ['x', '', 'z']]\n"
                "        encoded = m.encode(iter(rows))\n      "
                "  self.assertEqual(list(csv.reader(io.StringI"
                "O(encoded))), rows)\n        self.assertEqual("
                "m.encode(iter([])), '')\n"
            ),
            {
                "naive": (
                    "def encode(rows):\n    return ''.join(','.join(row) + '\\n' for row in rows)\n"
                ),
                "consumed": csv.replace(
                    "writer.writerows(rows)", "list(rows)\n    writer.writerows(rows)"
                ),
            },
        ),
    ]
    result = []
    for identity, difficulty, filename, code, target, family, prompt, body, defects in specs:
        module = filename[:-3]
        existing = test_file(
            f"    def test_import(self):\n        import {module}\n"
            f"        self.assertTrue(callable({module}.{target}))\n"
        )
        result.append(
            FrozenCase(
                identity,
                difficulty,
                "test-addition",
                {filename: code, "tests/test_existing.py": existing},
                [
                    prompt
                    + (
                        "仅新增 tests/test_added.py，使"
                        "用标准库 unittest，生产文件和已有测试保持"
                        "不变。"
                    )
                ],
                ["tests/test_added.py"],
                {"tests/test_added.py": assertions(module, body)},
                mutants={key: {filename: value} for key, value in defects.items()},
                family=family,
                test_target=filename + ":" + target,
            )
        )
    return result


def tool_failures() -> list[FrozenCase]:
    query = (
        "from urllib.parse import urlencode\ndef encode"
        "(pairs):\n    return urlencode(pairs, doseq=Tr"
        "ue)\n"
    )
    text = (
        "def read(path):\n    with open(path, encoding="
        "'utf-8-sig') as stream:\n        return stream"
        ".read()\n"
    )
    archive = """from pathlib import PurePosixPath
def safe(name):
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and '..' not in p.parts and '\\\\' not in name
"""
    ndjson = """import json
def read(lines):
    result = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            result.append(json.loads(line))
        except ValueError as exc:
            raise ValueError('line %s' % number) from exc
    return result
"""
    specs = [
        (
            "FS09",
            "easy",
            "query.py",
            query,
            "def encode(pairs):\n    return '&'.join(str(k)+'='+str(v) for k,v in pairs)\n",
            "query-percent-and-multivalue",
            "修复 encode 的查询参数编码，空格、&、Unicode 和多值都按 urllib 标准，保留输入顺序。",
            (
                "        from urllib.parse import parse_qs\n   "
                "     self.assertEqual(parse_qs(m.encode([('x'"
                ", ['a b', 'a&b']), ('q', '雪')])), {'x': ['a b"
                "', 'a&b'], 'q': ['雪']})\n        self.assertEq"
                "ual(m.encode([]), '')\n"
            ),
        ),
        (
            "FS10",
            "easy",
            "textfile.py",
            text,
            text.replace("utf-8-sig", "utf-8"),
            "utf8-bom-file-reading",
            "修复 read 读取 UTF-8 BOM 文件时保留 BOM 的错误；普通 UTF-8 和空文件保持原内容。",
            (
                "        import tempfile\n        from pathlib "
                "import Path\n        with tempfile.TemporaryDi"
                "rectory() as d:\n            p = Path(d) / 'in"
                ".txt'\n            for raw, expected in [(b'\\x"
                "ef\\xbb\\xbfhello', 'hello'), ('雪'.encode(), '雪"
                "'), (b'', '')]:\n                p.write_bytes"
                "(raw)\n                self.assertEqual(m.read"
                "(p), expected)\n"
            ),
        ),
        (
            "FS11",
            "medium",
            "archivepath.py",
            archive,
            "def safe(name):\n    return not name.startswith('/')\n",
            "archive-member-path-validation",
            (
                "修复 safe：只允许非空相对 POSIX 成员路"
                "径；拒绝绝对路径、任何 .. 组件和反斜杠，合法点"
                "号可用。"
            ),
            (
                "        for value in ('file', 'dir/file', './"
                "a', 'a..b'): self.assertTrue(m.safe(value))\n "
                "       for value in ('', '/x', '../x', 'a/../"
                "x', 'a\\\\b'): self.assertFalse(m.safe(value))\n"
            ),
        ),
        (
            "FS12",
            "medium",
            "jsonlines.py",
            ndjson,
            ndjson.replace("raise ValueError('line %s' % number) from exc", "continue"),
            "ndjson-error-line-accounting",
            (
                "修复 read 静默吞掉非法 NDJSON 的问题"
                "；空行忽略，非法 JSON 抛包含原物理行号的 V"
                "alueError 并保留 cause。"
            ),
            (
                "        self.assertEqual(m.read(iter(['', '{\""
                "x\": 1}', '   '])), [{'x': 1}])\n        with s"
                "elf.assertRaisesRegex(ValueError, 'line 3') a"
                "s caught: m.read(['', '{}', '{bad'])\n        "
                "self.assertIsNotNone(caught.exception.__cause"
                "__)\n"
            ),
        ),
    ]
    result = []
    for identity, level, path, good, bad, family, prompt, body in specs:
        check = assertions(path[:-3], body)
        injection = (
            "grep 首次有效调用会固定报错一次，可重试或使用合法替代。"
            if identity in ("FS09", "FS11")
            else (
                "标准命令 python -m unittest d"
                "iscover -s tests -v 首次有效调"
                "用会超时并清理子进程，可恢复或合法替代。"
            )
        )
        result.append(
            FrozenCase(
                identity,
                level,
                "tool-failure",
                {path: bad, "tests/test_existing.py": check},
                [prompt + injection + f"只修改 {path}。"],
                [path],
                {path: good},
                check,
                pressure=[
                    "first-grep-error" if identity in ("FS09", "FS11") else "first-test-timeout"
                ],
                family=family,
            )
        )
    return result


def long_context() -> list[FrozenCase]:
    decimal = "from decimal import Decimal\ndef parse(value):\n    return Decimal(value)\n"
    headers = (
        "def values(pairs, name):\n    return [value fo"
        "r key, value in pairs if key.casefold() == na"
        "me.casefold()]\n"
    )
    rbac = "def allowed(role, action):\n    return False\n"
    lines = "def events(text):\n    return []\n"
    graph = "def descendants(edges, key):\n    return set(edges.get(key, []))\n"
    cursor = (
        "import base64, json\ndef encode(offset, revisi"
        "on):\n    return base64.urlsafe_b64encode(json"
        ".dumps([offset, revision]).encode()).decode()"
        "\n"
    )
    rows = [
        FrozenCase(
            "FS13",
            "easy",
            "long-context",
            {"money.py": decimal},
            [
                (
                    "早期约束：parse 必须保持 Decimal 精"
                    "度，禁止经 float 转换，后续运算不改输入字符"
                    "串。阅读 money.py。"
                )
            ],
            ["money.py"],
            {
                "money.py": decimal
                + (
                    "\ndef total(values):\n    return sum((parse(val"
                    "ue) for value in values), Decimal('0'))\n"
                )
            },
            assertions(
                "money",
                (
                    "        from decimal import Decimal\n        s"
                    "elf.assertEqual(m.total(iter(['0.1', '0.2']))"
                    ", Decimal('0.3'))\n        self.assertEqual(m."
                    "total(['123456789012345678.01', '0.01']), Dec"
                    "imal('123456789012345678.02'))\n        self.a"
                    "ssertEqual(m.total([]), Decimal('0'))\n"
                ),
            ),
            pressure=["early-constraints", "scripted-history"],
            family="decimal-stream-total",
        ),
        FrozenCase(
            "FS14",
            "medium",
            "long-context",
            {"headers.py": headers},
            [
                (
                    "早期约束：HTTP header 名称不区分大小写"
                    "，重复字段保留原值和顺序；不改输入 pairs。阅"
                    "读 headers.py。"
                )
            ],
            ["headers.py"],
            {
                "headers.py": headers
                + (
                    "\ndef first(pairs, name, default=None):\n    fo"
                    "und = values(pairs, name)\n    return found[0]"
                    " if found else default\n"
                )
            },
            assertions(
                "headers",
                (
                    "        data = [('X-ID', ''), ('x-id', '2'), "
                    "('Other', '3')]\n        before = list(data)\n "
                    "       self.assertEqual(m.values(data, 'X-Id'"
                    "), ['', '2'])\n        self.assertEqual(m.firs"
                    "t(data, 'x-id', 'fallback'), '')\n        self"
                    ".assertEqual(m.first(data, 'none', 'fallback'"
                    "), 'fallback')\n        self.assertEqual(data,"
                    " before)\n"
                ),
            ),
            pressure=["early-constraints", "scripted-history"],
            family="http-header-multiplicity",
        ),
        FrozenCase(
            "FS15",
            "medium",
            "long-context",
            {"access.py": rbac},
            [
                (
                    "阅读 data/access-matrix.jso"
                    "n 并记录角色权限事实，文件不能修改。未知角色或动"
                    "作默认拒绝。"
                )
            ],
            ["access.py"],
            {
                "access.py": (
                    "import json\nfrom pathlib import Path\ndef allo"
                    "wed(role, action):\n    matrix = json.loads(Pa"
                    "th('data/access-matrix.json').read_text())\n  "
                    "  return action in matrix.get(role, [])\n"
                )
            },
            assertions(
                "access",
                (
                    "        self.assertTrue(m.allowed('auditor', "
                    "'read'))\n        self.assertFalse(m.allowed('"
                    "auditor', 'delete'))\n        self.assertTrue("
                    "m.allowed('operator', 'retry'))\n        self."
                    "assertFalse(m.allowed('operator', 'grant'))\n "
                    "       self.assertTrue(m.allowed('owner', 'gr"
                    "ant'))\n        self.assertFalse(m.allowed('st"
                    "ranger', 'read'))\n        self.assertFalse(m."
                    "allowed('owner', 'unknown'))\n"
                ),
            ),
            pressure=["large-file", "scripted-history"],
            family="role-action-matrix",
        ),
        FrozenCase(
            "FS16",
            "medium",
            "long-context",
            {"ledger.py": lines},
            [
                (
                    "阅读 data/events.csv，后续按 cs"
                    "v 规则还原事件；字段可有逗号和内嵌换行，按 se"
                    "q 数字排序，禁止改写原始数据。"
                )
            ],
            ["ledger.py"],
            {
                "ledger.py": (
                    "import csv, io\ndef events(text):\n    return s"
                    "orted(csv.DictReader(io.StringIO(text)), key="
                    "lambda row: int(row['seq']))\n"
                )
            },
            assertions(
                "ledger",
                (
                    "        from pathlib import Path\n        data"
                    " = m.events(Path('data/events.csv').read_text"
                    "())\n        self.assertEqual([r['seq'] for r "
                    "in data], ['1', '2', '10'])\n        self.asse"
                    "rtEqual(data[1]['note'], 'left,right')\n      "
                    "  self.assertEqual(data[2]['note'], 'first\\ns"
                    "econd')\n"
                ),
            ),
            pressure=["large-file", "scripted-history"],
            family="csv-event-ledger-order",
        ),
        FrozenCase(
            "FS17",
            "hard",
            "long-context",
            {"dag.py": graph},
            [
                (
                    "在 dag.py 实现 descendants(e"
                    "dges,key) 的传递后继闭包：排除起点、循环"
                    "图必须终止、未知 key 返回空集、不改输入。"
                )
            ],
            ["dag.py"],
            {
                "dag.py": """def descendants(edges, key):
    found, pending = set(), list(edges.get(key, []))
    while pending:
        item = pending.pop()
        if item == key or item in found:
            continue
        found.add(item)
        pending.extend(edges.get(item, []))
    return found

def invalidate(edges, changed):
    result = set(changed)
    for key in changed:
        result.update(descendants(edges, key))
    return result

def retained(edges, changed, cached):
    invalid = invalidate(edges, changed)
    return {key: value for key, value in cached.items() if key not in invalid}
"""
            },
            assertions(
                "dag",
                (
                    "        graph = {'a': ['b'], 'b': ['c'], 'c':"
                    " ['a'], 'd': []}\n        before = {k: list(v)"
                    " for k, v in graph.items()}\n        self.asse"
                    "rtEqual(m.descendants(graph, 'a'), {'b', 'c'}"
                    ")\n        self.assertEqual(m.descendants(grap"
                    "h, 'unknown'), set())\n        self.assertEqua"
                    "l(m.invalidate(graph, ['a']), {'a', 'b', 'c'}"
                    ")\n        cached = {'a': 1, 'c': 3, 'd': 4}\n "
                    "       self.assertEqual(m.retained(graph, ['a"
                    "'], cached), {'d': 4})\n        self.assertEqu"
                    "al(cached, {'a': 1, 'c': 3, 'd': 4})\n        "
                    "self.assertEqual(graph, before)\n"
                ),
            ),
            pressure=["multi-stage", "scripted-history"],
            family="dag-transitive-invalidation",
        ),
        FrozenCase(
            "FS18",
            "hard",
            "long-context",
            {"paging.py": cursor},
            [
                (
                    "保留 paging.encode 的兼容格式；新增 decode(cursor,revis"
                    "ion) 返回 offset：offset 必须非负 int 且非 bool、revisi"
                    "on 必须相等；任何格式或身份错误都抛 ValueError。"
                )
            ],
            ["paging.py"],
            {
                "paging.py": cursor
                + """
def decode(cursor, revision):
    try:
        offset, saved = json.loads(base64.urlsafe_b64decode(cursor))
        if type(offset) is not int or offset < 0 or saved != revision:
            raise ValueError('cursor')
        return offset
    except Exception as exc:
        raise ValueError('cursor') from exc

def page(values, size, revision, token=None):
    if type(size) is not int or size <= 0:
        raise ValueError('size')
    offset = 0 if token is None else decode(token, revision)
    selected = values[offset:offset+size]
    end = offset + len(selected)
    return selected, encode(end, revision) if end < len(values) else None
"""
            },
            assertions(
                "paging",
                (
                    "        data = ['a', 'b', 'c']\n        first,"
                    " token = m.page(data, 2, 'r1')\n        self.a"
                    "ssertEqual(first, ['a', 'b'])\n        self.as"
                    "sertEqual(m.page(data, 2, 'r1', token), (['c'"
                    "], None))\n        self.assertEqual(m.page([],"
                    " 1, 'r1'), ([], None))\n        for offset in "
                    "(-1, True):\n            with self.assertRaise"
                    "s(ValueError): m.decode(m.encode(offset, 'r1'"
                    "), 'r1')\n        with self.assertRaises(Value"
                    "Error): m.decode(m.encode(0, 'r1'), 'r2')\n   "
                    "     with self.assertRaises(ValueError): m.de"
                    "code('invalid', 'r1')\n        with self.asser"
                    "tRaises(ValueError): m.page(data, 0, 'r1')\n  "
                    "      self.assertEqual(data, ['a', 'b', 'c'])"
                    "\n"
                ),
            ),
            pressure=["multi-stage", "scripted-history"],
            family="revision-bound-pagination",
        ),
    ]
    finals = {
        "FS13": (
            "在 money.py 新增 total(value"
            "s)，用 parse 处理可迭代十进制字符串并求和"
            "；空输入返回 Decimal 零，保留早期精度约束"
            "。"
        ),
        "FS14": (
            "新增 first(pairs,name,defau"
            "lt=None)：返回首个匹配值，只在无匹配时返回"
            " default，沿用之前约束。"
        ),
        "FS15": (
            "在 access.py 完成 allowed(role,action)，按先前权限事实返回 bool，可重新阅读原文件。"
        ),
        "FS16": (
            "在 ledger.py 实现 events(tex"
            "t) 还原之前数据的字典列表，沿用之前读取与排序约"
            "束，可重读原数据。"
        ),
        "FS17": (
            "新增 retained(edges,changed"
            ",cached)，返回未失效缓存的新字典，复用中间"
            "的 invalidate，保持先前闭包和输入不变性"
            "。"
        ),
        "FS18": (
            "新增 page(values,size,revision,token=None) 返回 ("
            "当前列表,next_token)，尾页 next_token=None；size 必须正 "
            "int 且非 bool，使用之前 decode 的身份校验。"
        ),
    }
    for case in rows:
        for i in range(1, 5):
            case.files[f"notes/audit-{i}.txt"] = "".join(
                f"审计 {i}/{n:04d}：本条是已归档背景事件。\n" for n in range(360)
            )
            case.requests.append(f"阅读 notes/audit-{i}.txt 并简述内容；这一轮不修改代码。")
            if i == 2 and case.task_id == "FS17":
                case.requests.append(
                    "新增 invalidate(edges,chang"
                    "ed) 返回改动节点本身及所有传递后继的集合，沿用"
                    "之前闭包实现。"
                )
        case.requests.append(finals[case.task_id] + "仅修改合同允许的生产文件。")
    rows[2].files["data/access-matrix.json"] = (
        json.dumps(
            {
                "auditor": ["read"],
                "operator": ["read", "retry"],
                "owner": ["read", "retry", "delete", "grant"],
                "archived": [f"fact-{n}" for n in range(2400)],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    rows[3].files["data/events.csv"] = (
        'seq,note\n10,"first\nsecond"\n2,"left,right"\n1,start\n' + "\n" * 1800
    )
    return rows


def discovery() -> list[FrozenCase]:
    cases = []
    records: list[dict[str, Any]] = [
        {
            "ci_commit": {
                "d19": {
                    "commit": "d19",
                    "jobs": [
                        {"job": "lint-linux", "state": "failed"},
                        {"job": "unit-linux", "state": "passed"},
                        {"job": "type-linux", "state": "failed"},
                    ],
                    "source": "ci",
                }
            }
        },
        {
            "ci_commit": {
                "d20": {
                    "commit": "d20",
                    "attempts": [
                        {
                            "attempt": 1,
                            "jobs": {"unit": "failed", "schema": "failed", "doc": "passed"},
                        },
                        {
                            "attempt": 2,
                            "jobs": {"unit": "passed", "schema": "failed", "doc": "failed"},
                        },
                    ],
                    "source": "ci",
                },
            }
        },
        {
            "ci_test": {
                "test-21": {
                    "case_id": "test-21",
                    "artifact_sha256": "ab" * 32,
                    "source": "ci-tests",
                }
            },
            "ci_deploy": {
                "test-21": {
                    "case_id": "test-21",
                    "artifact_sha256": "cd" * 32,
                    "region": "hk",
                    "source": "deployment",
                },
                "release-21": {
                    "deployment_id": "release-21",
                    "artifact_sha256": "ab" * 32,
                    "region": "sg",
                    "source": "deployment",
                },
            },
        },
        {
            "ci_test": {
                "test-22": {
                    "case_id": "test-22",
                    "required_shards": ["linux", "windows"],
                    "shards": {"linux": "passed", "windows": "failed", "docs": "failed"},
                    "source": "ci-tests",
                }
            },
            "ci_deploy": {
                "test-22": {
                    "case_id": "test-22",
                    "approved_regions": ["sg"],
                    "requested_region": "sg",
                    "source": "deployment",
                }
            },
        },
        {
            "issue_lookup": {"MIME-23": {"issue": "MIME-23", "commit": "d23", "source": "issues"}},
            "ci_commit": {
                "d23": {
                    "commit": "d23",
                    "job": "mime-contract",
                    "reason": "PARAMETER_CASE",
                    "source": "ci",
                }
            },
        },
        {
            "issue_lookup": {
                "ROUTE-24": {"issue": "ROUTE-24", "commit": "d24", "source": "issues"}
            },
            "ci_commit": {
                "d24": {
                    "commit": "d24",
                    "job": "route-contract",
                    "reason": "FALLBACK_SHADOWED",
                    "source": "ci",
                }
            },
            "ci_deploy": {
                "d24": {
                    "commit": "d24",
                    "job": "canary",
                    "reason": "UNRELATED",
                    "source": "deployment",
                }
            },
        },
    ]
    prompts = [
        (
            "只读汇总 d19 的 CI 矩阵：JSON 含 c"
            "ommit、failed_jobs（按名称排序）、"
            "failed_count、source。通过所有 "
            "job 状态计算，不能只返回一个失败条目。"
        ),
        (
            "只读比较 d20 两次 CI：JSON 含 com"
            "mit、recovered（失败变通过）、pers"
            "istent（两次都失败）、new_failure"
            "s（通过变失败）、source；三个列表按名称排序"
            "。"
        ),
        (
            "只读关联 test-21 的 CI 构建产物与部署"
            "：必须以 artifact_sha256 全值匹配"
            "，不能按相同记录 key 猜测。JSON 含 ca"
            "se_id、artifact_sha256、ver"
            "ified_deployment、deployed"
            "_region、source（ci-tests+d"
            "eployment）。"
        ),
        (
            "只读判断 test-22 能否发布：所有 requ"
            "ired_shards 都通过且请求地区获批准才 "
            "eligible。可选 shard 不参与阻塞。J"
            "SON 含 case_id、eligible、bl"
            "ocking_shards（排序）、region_"
            "allowed、source（ci-tests+d"
            "eployment）。"
        ),
        (
            "关联 MIME-23 和对应提交 CI，然后修复 "
            "mime.py media_type：去除参数和两"
            "侧空白并忽略大小写。另在 handlers.py "
            "实现 select(header,handlers"
            ",fallback)，按精确类型、主类型/*、*/"
            "*、fallback 顺序选处理器；decode("
            "value,header,handlers,fal"
            "lback) 调用所选处理器且保留其 None/空"
            "字符串返回。JSON 含 issue、commit"
            "、job、reason、source（CI来源）。"
            "仅改 mime.py 和 handlers.py。"
        ),
        (
            "关联 ROUTE-24 对应提交的 CI 失败而非"
            " canary 部署，修复 routes.py c"
            "hoose：精确路由优先，其次最长路径段前缀，最后"
            " fallback；不把 /api 匹配 /api"
            "x。另实现 gateway.forward(url"
            ",routes,fallback)，返回 targ"
            "et、path、query：仅用URL的未解码路径"
            "匹配，去掉已匹配mount（空尾部为/），未匹配保"
            "留原路径，query原样保留。同目标可对应不同mo"
            "unt，不得倒查目标猜mount。JSON 含 i"
            "ssue、commit、job、reason、so"
            "urce（CI来源）。仅改 routes.py 和"
            " gateway.py，保持输入字典。"
        ),
    ]
    families = [
        "ci-matrix-failure-aggregation",
        "ci-attempt-state-transitions",
        "artifact-digest-deployment-provenance",
        "required-shard-release-eligibility",
        "mime-negotiation-handler-dispatch",
        "mounted-route-forwarding",
    ]
    derived_answers = [
        {
            "commit": "d19",
            "failed_jobs": ["lint-linux", "type-linux"],
            "failed_count": 2,
            "source": "ci",
        },
        {
            "commit": "d20",
            "recovered": ["unit"],
            "persistent": ["schema"],
            "new_failures": ["doc"],
            "source": "ci",
        },
        {
            "case_id": "test-21",
            "artifact_sha256": "ab" * 32,
            "verified_deployment": "release-21",
            "deployed_region": "sg",
            "source": "ci-tests+deployment",
        },
        {
            "case_id": "test-22",
            "eligible": False,
            "blocking_shards": ["windows"],
            "region_allowed": True,
            "source": "ci-tests+deployment",
        },
    ]
    for i, data in enumerate(records, 19):
        identity = f"FS{i:02}"
        facts_record = next(iter(data["ci_test" if i in (21, 22) else "ci_commit"].values()))
        if i < 23:
            facts_record = derived_answers[i - 19]
        else:
            facts_record = {"issue": next(iter(data["issue_lookup"])), **facts_record}
        case = FrozenCase(
            identity,
            "easy" if i == 19 else "hard" if i >= 23 else "medium",
            "tool-discovery",
            {"records.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n"},
            [prompts[i - 19] + "可发现内置/MCP 查询能力，也接受读取公开记录的合法替代。"],
            [],
            answers=[facts_record],
            records=data,
            pressure=[
                "builtin-discovery" if i < 21 else "mcp-discovery",
                "cross-record" if i >= 23 else "similar-responsibilities",
            ],
            family=families[i - 19],
        )
        cases.append(case)
    cases[4].files["mime.py"] = "def media_type(value):\n    return value.lower()\n"
    cases[4].files["handlers.py"] = (
        "def select(header, handlers, fallback):\n    return fallback\n"
        "def decode(value, header, handlers, fallback):\n    return fallback(value)\n"
    )
    cases[4].allowed = ["mime.py", "handlers.py"]
    cases[4].reference = {
        "mime.py": "def media_type(value):\n    return value.split(';', 1)[0].strip().lower()\n",
        "handlers.py": "from mime import media_type\n"
        "def select(header, handlers, fallback):\n"
        "    kind = media_type(header)\n"
        "    major = kind.split('/', 1)[0] + '/*'\n"
        "    return handlers.get(kind, handlers.get(major, handlers.get('*/*', fallback)))\n"
        "def decode(value, header, handlers, fallback):\n"
        "    return select(header, handlers, fallback)(value)\n",
    }
    cases[4].checks = assertions(
        "mime",
        (
            "        self.assertEqual(m.media_type(' Text/"
            "Plain; charset=UTF-8 '), 'text/plain')\n      "
            "  self.assertEqual(m.media_type('application/"
            "json'), 'application/json')\n        self.asse"
            "rtEqual(m.media_type(''), '')\n"
        ),
    )
    cases[4].checks += (
        "    def test_dispatch(self):\n"
        "        from handlers import select, decode\n"
        "        exact = lambda value: None\n"
        "        major = lambda value: ''\n"
        "        any_type = lambda value: 'any:' + value\n"
        "        fallback = lambda value: 'fallback:' + value\n"
        "        rules = {'text/plain': exact, 'text/*': major, '*/*': any_type}\n"
        "        self.assertIs(select(' Text/Plain; charset=UTF-8', rules, fallback), exact)\n"
        "        self.assertIsNone(decode('x', 'text/plain', rules, fallback))\n"
        "        self.assertEqual(decode('x', 'TEXT/HTML', rules, fallback), '')\n"
        "        self.assertEqual(decode('x', 'application/json', rules, fallback), 'any:x')\n"
        "        self.assertEqual(decode('x', 'application/json', {}, fallback), 'fallback:x')\n"
        "        self.assertEqual(set(rules), {'text/plain', 'text/*', '*/*'})\n"
    )
    good = """def choose(path, routes, fallback):
    if path in routes:
        return routes[path]
    matches = [key for key in routes if path.startswith(key.rstrip('/') + '/')]
    return routes[max(matches, key=len)] if matches else fallback
"""
    cases[5].files["routes.py"] = "def choose(path, routes, fallback):\n    return fallback\n"
    cases[5].files["gateway.py"] = (
        "def forward(url, routes, fallback):\n"
        "    return {'target': fallback, 'path': url, 'query': ''}\n"
    )
    cases[5].allowed = ["routes.py", "gateway.py"]
    good += (
        "\ndef mount(path, routes):\n"
        "    if path in routes: return path\n"
        "    found = [key for key in routes if path.startswith(key.rstrip('/') + '/')]\n"
        "    return max(found, key=len) if found else None\n"
    )
    cases[5].reference = {
        "routes.py": good,
        "gateway.py": (
            "from urllib.parse import urlsplit\nfrom routes import mount, choose\n"
            "def forward(url, routes, fallback):\n"
            "    parsed = urlsplit(url)\n    prefix = mount(parsed.path, routes)\n"
            "    target = choose(parsed.path, routes, fallback)\n"
            "    path = parsed.path if prefix is None else parsed.path[len(prefix.rstrip('/')):]\n"
            "    return {'target': target, 'path': path or '/', 'query': parsed.query}\n"
        ),
    }
    cases[5].checks = assertions(
        "routes",
        (
            "        data = {'/api': 'base', '/api/v1': 'v"
            "1', '/api/v1/users': 'users'}\n        self.as"
            "sertEqual(m.choose('/api/v1/users', data, 'no"
            "ne'), 'users')\n        self.assertEqual(m.cho"
            "ose('/api/v1/items', data, 'none'), 'v1')\n   "
            "     self.assertEqual(m.choose('/api/other', "
            "data, 'none'), 'base')\n        self.assertEqu"
            "al(m.choose('/apix', data, 'none'), 'none')\n "
            "       self.assertEqual(m.choose('/x', {}, 'n"
            "one'), 'none')\n"
        ),
    )
    cases[5].checks += (
        "    def test_forwarding(self):\n"
        "        from gateway import forward\n"
        "        rules = {'/api': 'shared', '/api/v1': 'shared'}\n"
        "        before = dict(rules)\n"
        "        self.assertEqual(forward('/api/v1/items/1?x=%2F&x=2', rules, 'none'),\n"
        "                         {'target': 'shared', 'path': '/items/1', 'query': 'x=%2F&x=2'})\n"
        "        self.assertEqual(forward('/api', rules, 'none'),\n"
        "                         {'target': 'shared', 'path': '/', 'query': ''})\n"
        "        self.assertEqual(forward('/apix?q=1', rules, 'none'),\n"
        "                         {'target': 'none', 'path': '/apix', 'query': 'q=1'})\n"
        "        self.assertEqual(forward('/api%2Fhidden', rules, 'none')['target'], 'none')\n"
        "        self.assertEqual(rules, before)\n"
    )
    return cases


def cases() -> list[FrozenCase]:
    return localization() + test_addition() + tool_failures() + long_context() + discovery()
