# Skill 系统 Checklist

> 所有条目必须可勾选、可观测。验收方式写在每项后面的括号里。操作目录在仓库根 `.`。

## 1. 实现完整性

### 1.1 解析与加载

- [ ] `mewcode/skills/parser.py:23` `SkillDef` 含字段 `name / description / prompt_body / mode / model / context / source_path / is_directory`（`grep -n "class SkillDef" mewcode/skills/parser.py` 命中）
- [ ] `mewcode/skills/parser.py:36` `parse_frontmatter(raw) -> (dict, str)` 处理 `---\n...\n---` 格式
- [ ] `mewcode/skills/parser.py:57` `_validate_meta` 校验 `name` 正则 + `mode in {inline, fork}` + `context in {full, recent, none}`
- [ ] `mewcode/skills/parser.py:99` `substitute_arguments(prompt_body, args)` 实现 `$ARGUMENTS` 替换
- [ ] `mewcode/skills/loader.py:15` `SkillLoader(work_dir)` 实现两级搜索（`grep -n "PROJECT_SKILLS_DIR\|USER_SKILLS_DIR" mewcode/skills/loader.py` 命中 ≥2 处）
- [ ] `mewcode/skills/loader.py:96` `get(name)` 每次重读源文件实现热重载，失败回退 `_cache` 并 `log.warning`
- [ ] `mewcode/skills/loader.py:117` `get_source_label(name)` 按 `_project_dir / _user_dir` 前缀返回 `project | user`

### 1.2 Executor

- [ ] `mewcode/skills/executor.py:43` 含 `class SkillExecutor` 与 `execute_inline / execute_fork` 两个方法
- [ ] inline 调用链：`substitute_arguments` → `agent.activate_skill(name, body)`（`grep -n "activate_skill" mewcode/skills/executor.py` 命中）
- [ ] fork 调用链：新 `ConversationManager` → `_build_fork_context(context)` 按 `full / recent / none` 三档装填 → 临时 Agent run → 收集 `StreamText` 到 `LoopComplete`

### 1.3 Agent 集成

- [ ] `mewcode/agent.py:317` Agent 含 `self.active_skills: dict[str, str] = {}`
- [ ] `mewcode/agent.py:357` `activate_skill(name, prompt_body)` 实现
- [ ] `mewcode/agent.py:360` `clear_active_skills()` 实现
- [ ] `mewcode/agent.py:363` `set_skill_catalog(catalog)` 实现
- [ ] `mewcode/agent.py:400` 主循环每轮调用 `build_environment_context(work_dir, active_skills, skill_catalog, agent_catalog)`
- [ ] `mewcode/prompts.py:277` `build_environment_context` 把 `active_skills` 拼成 `## Active Skills` 段；`skill_catalog` 拼到 environment block

### 1.4 LoadSkill 工具

- [ ] `mewcode/tools/load_skill.py:21` 含 `class LoadSkill(Tool)`，`name = "LoadSkill"`、`category = "read"`
- [ ] `mewcode/tools/load_skill.py:39` `set_loader / set_agent` 注入方法
- [ ] `mewcode/tools/load_skill.py:46` `execute` 调 `loader.get → agent.activate_skill` → 返回 `"Skill '' activated. SOP pinned to environment context."`
- [ ] `mewcode/skills/executor.py:14` `SYSTEM_TOOL_NAMES = frozenset({"LoadSkill"})` 常量

### 1.5 命令集成

- [ ] 每个 skill 自动注册为 `/` 命令，描述末尾含 `[skill]`（`grep -n "\\[skill\\]" mewcode/commands/handlers/skill_register.py` 命中）
- [ ] `mewcode/commands/handlers/skill_register.py:18` `register_skill_commands(registry, loader, executor)` 实现；模块级 `_REGISTERED_SKILL_NAMES` 跟踪重复注册
- [ ] inline skill 命令 handler 调 `executor.execute_inline` 后再 `ctx.ui.send_user_message(trigger)`
- [ ] fork skill 命令 handler 走 `asyncio.create_task(_run_fork)`，结果作为 `add_system_message` 插入
- [ ] `mewcode/commands/handlers/skill.py:11` `/skill list | info  | reload` 子命令分发
- [ ] `mewcode/commands/handlers/clear.py:19` `handle_clear` 调用 `ctx.agent.clear_active_skills()`

### 1.6 远程安装

- [ ] `mewcode/skills/install.py` 含 `parse_skill_url` 支持三种 URL 格式
- [ ] `install_skill(src, install_root)` 走 GitHub Contents API 递归拉取，atomic rename
- [ ] 限额常量 MAX_FILE_SIZE / MAX_TOTAL_SIZE / MAX_FILE_COUNT / MAX_RECURSION_DEPTH
- [ ] 下载完没有 SKILL.md 或 skill.yaml 时拒绝安装并清理 staging
- [ ] `InstallSkillTool` name = InstallSkill，category = write
- [ ] 执行成功后调 loader.reload() + on_installed 回调

## 2. 接入完整性（杜绝死代码）

- [ ] `grep -rn "SkillLoader" mewcode/app.py` 命中 ≥2 处（import + 实例化）
- [ ] `grep -rn "activate_skill" mewcode/` 命中 Agent 方法定义 + Executor + LoadSkillTool 三处调用
- [ ] `grep -rn "clear_active_skills" mewcode/` 命中 `/clear` handler 调用 + Agent 方法定义
- [ ] `grep -rn "LoadSkill\|\"LoadSkill\"" mewcode/` 命中 tool 定义 + app 注册 + 至少 1 个测试
- [ ] `grep -rn "SkillExecutor\|register_skill_commands" mewcode/` 命中 app.py 注册 + handler 模块
- [ ] `grep -rn "execute_inline\|execute_fork" mewcode/skills/` 命中 Executor 定义 + handler 调用
- [ ] `grep -rn "loader.get\|SkillLoader.get" mewcode/tools/load_skill.py` 命中 1 处
- [ ] `mewcode/app.py:556` 存在 `self.skill_loader` / `self.skill_executor` / `self._load_skill_tool` 字段
- [ ] `mewcode/app.py:885` `CommandContext.config` 字典塞入 `"skill_loader"` 与 `"skill_executor"` key

## 3. 编译与测试

- [ ] `cd . && ruff check mewcode/skills mewcode/tools/load_skill.py` 无 error
- [ ] `cd . && pytest tests/test_skills.py -q` 全部通过
- [ ] `cd . && pytest tests/test_agent.py -q` 全部通过
- [ ] `cd . && python -c "from mewcode.skills.loader import SkillLoader; l = SkillLoader('/tmp'); l.load_all(); print('SkillLoader OK')"` 不报错
- [ ] `cd . && python -c "from mewcode.tools.load_skill import LoadSkill; t = LoadSkill(); print(t.name, t.category)"` 输出 `LoadSkill read`

## 4. 端到端验证（手动操作 TUI）

> 启动命令：`cd . && python -m mewcode`

- [ ] 创建测试 skill 目录 `.mewcode/skills/test-skill/SKILL.md`，写一个简单 inline SOP（`name: test-skill / description: A test skill / mode: inline`，body 写 `Echo hello`）
- [ ] 启动后输 `/help`，看到 `/test-skill [skill]` / `/skill` 都列出
- [ ] 输 `/test-skill`，看到 SOP 加载到 env context
- [ ] 编辑 `.mewcode/skills/test-skill/SKILL.md` 改一行后**不重启** TUI，再 `/test-skill`，看到新行进入 prompt（热重载验证）
- [ ] 自然语言触发 `LoadSkill({name: "test-skill"})`，environment 段里出现该 skill 的 SOP
- [ ] 输 `/clear`，立即输任意消息，environment 段里**不再出现** `## Active Skills`
- [ ] 创建 `.mewcode/skills/bad.md` 故意写错 frontmatter，启动日志出现 `Skipping ... skill 'bad': ...` warning，其他 skill 仍正常加载
- [ ] LoadSkill 工具调用时**不**弹权限提示（`category=read`）

## 5. 文档

- [ ] `docs/skills/spec.md` 更新到课程全量版（不是验收版）
- [ ] `docs/skills/tasks.md` 11 个任务全部勾上
- [ ] `docs/skills/checklist.md` 全部条目勾上
- [ ] commit 信息：`feat(skills): full skill system per course design (python) [spec/tasks/checklist closed]`