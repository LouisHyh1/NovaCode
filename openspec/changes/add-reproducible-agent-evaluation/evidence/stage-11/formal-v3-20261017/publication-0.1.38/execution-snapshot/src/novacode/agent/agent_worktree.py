"""SubAgent 的 Git Worktree 隔离执行路径。"""

from pathlib import Path

from novacode.tool import cwd_from_ctx, with_cwd
from novacode.worktree import Manager, random_agent_name


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    return f"""<worktree-context>
你当前在一个独立的 Git Worktree 副本中工作，与父 Agent 的文件系统隔离。
- 父目录: {parent_cwd}
- 你的工作目录: {wt_path}
- 父 Agent 提到的绝对路径基于父目录，请替换前缀为你的工作目录后再读写
- 编辑文件前，必须先在本地 Worktree 重新调用 read_file，避免使用过时内容
</worktree-context>"""


async def execute_with_worktree(
    manager: Manager,
    sub_agent,
    conversation,
    prompt: str,
    events,
) -> str:
    name = random_agent_name()
    worktree = await manager.create(name, "HEAD", manual=False)
    parent_cwd = cwd_from_ctx() or str(Path.cwd())
    final_text = ""
    try:
        task = f"{build_worktree_notice(parent_cwd, worktree.path)}\n\n{prompt}"
        with with_cwd(worktree.path, parent_cwd=parent_cwd):
            final_text = await sub_agent.run_to_completion(conversation, task, events)
    finally:
        report = await manager.auto_cleanup(name)
    if report.kept:
        final_text += f"\n[Worktree 保留在 {report.path}，分支 {report.branch}]"
    return final_text


_execute_with_worktree = execute_with_worktree
