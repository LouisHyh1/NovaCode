"""规则解析与规则集判定。

规则以「工具名(模式)」声明，结果只有 allow 或 deny 两种。
工具名用友好名（Bash/Read/Write/Edit/Glob/Grep）。
模式段支持精确匹配与 glob 匹配（* 任意串、** 跨目录段仅对文件路径有意义）。
"""

from dataclasses import dataclass, field

from novacode.permission import Decision
from novacode.permission.matcher import GlobMatcher, Matcher, compile_matcher


@dataclass
class Rule:
    """单条规则；pattern 属性仅用于兼容旧调用方。"""

    tool: str  # 友好名：Bash/Read/Write/Edit/Glob/Grep
    matcher: Matcher | str | None
    allow: bool  # True=allow, False=deny
    raw: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.matcher, str):
            pattern = self.matcher
            self.matcher = (
                compile_matcher(pattern, is_command=self.tool == "Bash") if pattern else None
            )
            if not self.raw:
                self.raw = pattern

    @property
    def pattern(self) -> str:
        return self.raw


@dataclass
class RuleSet:
    """一组 allow 与 deny 规则。"""

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        """先 deny 再 allow；返回 (Allow|Deny, 命中?)。"""
        for r in self.deny:
            if r.tool == friendly and match_rule(r, target):
                return Decision.DENY, True
        for r in self.allow:
            if r.tool == friendly and match_rule(r, target):
                return Decision.ALLOW, True
        return Decision.ALLOW, False


def parse_rule(s: str) -> tuple[Rule, bool]:
    """解析 'Tool(pattern)' 或 'Tool' 为 Rule。

    返回 (rule, ok)。非法格式（空、括号不配对）返回 (Rule("","",False), False)。
    注：allow/deny 归属由调用方根据来源列表决定。
    """
    s = s.strip()
    if not s:
        return Rule("", None, False), False
    # 提取工具名与可选模式
    paren = s.find("(")
    if paren == -1:
        # 无括号：匹配该工具全部
        tool = s.strip()
        if not tool:
            return Rule("", None, False), False
        return Rule(tool=tool, matcher=None, allow=True, raw=""), True
    # 有括号
    if not s.endswith(")"):
        return Rule("", None, False), False
    tool = s[:paren].strip()
    pattern = s[paren + 1 : -1]  # 去掉首尾括号
    if not tool:
        return Rule("", None, False), False
    try:
        matcher = compile_matcher(pattern, is_command=tool == "Bash") if pattern else None
    except ValueError:
        return Rule("", None, False), False
    return Rule(tool=tool, matcher=matcher, allow=True, raw=pattern), True


def parse_rule_detailed(s: str) -> tuple[Rule | None, str | None]:
    """解析规则并返回可供启动期 stderr 展示的错误原因。"""
    raw = s.strip()
    if not raw:
        return None, "empty rule"
    paren = raw.find("(")
    if paren == -1:
        return Rule(raw, None, True, raw=""), None
    if not raw.endswith(")"):
        return None, "unclosed parentheses"
    tool = raw[:paren].strip()
    if not tool:
        return None, "empty tool name"
    pattern = raw[paren + 1 : -1]
    if not pattern:
        return Rule(tool, None, True, raw=""), None
    try:
        matcher = compile_matcher(pattern, is_command=tool == "Bash")
    except ValueError as exc:
        return None, str(exc)
    return Rule(tool, matcher, True, raw=pattern), None


def match_rule(rule: Rule, target: str) -> bool:
    return rule.matcher is None or rule.matcher.match(target)


def match_pattern(pattern: str, target: str) -> bool:
    """glob 匹配：pattern=="" 恒匹配。

    命令串走「命令 glob」——* 匹配任意字符含空格，其余字面，** 等价 *。
    文件路径按 / 分段：* 匹配段内任意字符，** 跨段匹配。
    """
    if not pattern:
        return True
    return GlobMatcher(pattern, is_command="/" not in target and "\\" not in target).match(target)
