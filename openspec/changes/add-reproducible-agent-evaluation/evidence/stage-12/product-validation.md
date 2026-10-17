# 第十二阶段：产品专项与质量门禁

2026-10-17，产品版本 **0.1.39**。12.1–12.4 的交付范围为真实模型 tmux 产品专项、隔离记忆和后台生命周期机制验收、完整质量门禁及全部规范场景审计。主集仍为受测 **0.1.37** 的 24 道独立题、216/216 次、147 次严格成功；本轮没有新增正式样本，没有更改模型策略、预算、判题合同或历史评分。

## 真实产品入口

[验收脚本](../../../../../evaluation/examples/stage12_tmux.py) 使用正式 NovaCodeApp、Agent、SessionService、权限引擎、工具和真实 Provider，通过独立 tmux socket 输入请求与按键；子类只旁路记录事件。工作区和 HOME 均隔离，不装配操作者的记忆提取器、治理器或自定义 Hook。兼容产品默认仍为压缩开启、Schema 全量暴露；SDK 自动重试为零、超时90秒、单请求输出上限4096、thinking发送规则与原适配器相同。

每次尝试有 create、restore 两个产品进程，各上限240秒、200000 Token、12次 Provider、12次工具、单请求预留60000、清理15秒；每次尝试总预留480秒、400000 Token、24次 Provider和24次工具，使用独立追加预算账本。r1 与 r2 账本独立，全部已发生调用均计入下表，不退还失败成本，不占用主集计划键。

| 验收项 | r2 实际证据 | 结论 |
| --- | --- | --- |
| 普通请求 | [create账本](tmux-r2/create/ledger.jsonl) 与 [终端](tmux-r2/create/final-pane.txt)：真实 read_file 返回 LYRA-482 和37 | 工具、最终回复和Session均一致 |
| 权限拒绝 | [审批界面](tmux-r2/create/approval-pane.txt)，通过真实按键 n 拒绝 write_file | authorization=denied、execution=not-executed；blocked.txt不存在，无替代写入 |
| 创建与退出 | create写入持久Session后Ctrl+C，退出码0；写队列完成，消费者为零 | Provider所有者实际关闭一次 |
| 重启与恢复 | 新产品进程通过 /resume 菜单恢复原Session；[菜单](tmux-r2/restore/resume-menu-pane.txt) 与 [继续对话](tmux-r2/restore/final-pane.txt) | 恢复原session_id，不读文件或调用工具即可回答原标识和值 |
| 流式终止 | 收到真实文本后按Escape；[终端](tmux-r2/restore/final-pane.txt) 保留尾部和 interrupted 提示 | 回到idle，流以cancelled结束；退出码0，无未完成工具或阶段 |

原账本、内容寻址请求/回答/Session工件、实际业务目录和退出状态均随本报告保留。[review.json](tmux-r2/review.json) 由最终脚本从这些文件重建；所有工件SHA-256、账本序列、两轮回复、审批、恢复ID、取消尾部、Provider关闭次数及收尾条件均断言通过。复核只读模型和Session输入，不再次调用模型；复核输出写入review.json。

```bash
.venv/bin/python evaluation/examples/stage12_tmux.py --root "$PWD/openspec/changes/add-reproducible-agent-evaluation/evidence/stage-12/tmux-r2" --review
```

## 实际发现和修复

首轮 [r1](tmux-r1/driver.log) 完成普通请求、审批拒绝和Session恢复，但菜单关闭后焦点落到聊天记录区，后续输入没有进入ChatInput。保留原请求、成本、Session和退出证据；该轮不是通过样本。新增真实产品消息循环回归，Enter选择和Escape取消两条路径都先失败，见 [修复前](resume-focus-before.txt)；共用的 _close_resume_list 现在显式归还聊天输入焦点，两例均通过，见 [修复后](resume-focus-after.txt)。没有在tmux脚本中用额外Tab绕开产品故障。

完整pytest还发现历史发布快照的 evaluation/specialty/test_driver.py 被当成测试收集，和当前同名文件冲突，见 [原日志](quality-gates-collection-failure.txt)。在pyproject.toml明确 testpaths=["tests"]，完整收集全部1060项项目测试，历史源码和故意缺陷夹具不属于当前可执行测试套件。新增脚本首次复杂度11超过上限10，见 [首次门禁](quality-gates-first.txt)；将独立流记录器提取为函数后通过，未放宽门禁或增加历史豁免。

真实r2之后只调整了脚本的记录函数组织和pytest收集配置，产品TUI源码保持r2受测字节；相关哈希和版本来源见 [version-identity.json](version-identity.json)。主集受测源码的231个原绑定快照继续保留。

## 费用和证据类型

| 尝试 | Provider调用 | Token已知下界 | 未知usage请求 | 结论 |
| --- | ---: | ---: | ---: | --- |
| r1 create＋restore | 4 | 9942 | 0 | 恢复后焦点故障，保留失败 |
| r2 create＋restore | 6 | 12643 | 1 | PRODUCT_TMUX_VALID |
| 全部产品调用 | 10 | 22585 | 1 | 金额未知，完整Token未知 |

r2的最后一次真实流被主动取消，没有可信最终usage，因此其成本保持未知，预算停止追加调用，不记作零消耗。没有在此后继续发起模型请求。汇总及每进程指标见 [summary.json](summary.json)，本表不合并进216次主集或历史v2成本。

[170项机制测试](product-mechanisms.txt) 及 [逐例XML](product-mechanisms.xml) 覆盖显式记忆创建/更新/删除、非法请求和事务失败、提取队列与取消、后台Agent提交/完成/停止、共享Provider所有权、TUI退出等待和第二次Ctrl+C取消，以及相关流关闭与原错误保留。它们使用临时MemoryStore、Fake Provider或模拟HTTP，标为隔离机制证据；本轮没有真实模型长期记忆或多Agent效果实验。操作者项目的4个记忆文件及用户记忆目录集合前后相同，见 [保全收据](preservation.json)；未写入Codex跨会话记忆。

## 质量、场景与历史核验

`PATH=/home/louishyh/.local/bin:$PWD/.venv/bin:$PATH UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple .venv/bin/python scripts/validate_change.py` 退出码0，完整命令和结果见 [quality-gates.txt](quality-gates.txt)：1060项pytest通过，uv lock检查、Ruff/格式、23个评测入口严格mypy、复杂度、运行时增量门禁、6项架构测试及版本/启动全部通过。运行时mypy为历史180、当前179、新增0；复杂度历史21、当前20、新增0。新增专项脚本纳入同一校验入口，没有扩充历史债务基线。

[69个规范场景审计](scenario-audit.md) 覆盖全部六份spec，逐项关联测试符号、历史真实证据或协议审阅。实施、题库、先导、冻结、正式与本轮专项的完成边界各自列明；未触发的反事实和未重做的Windows原生交互不冒充本轮真实事件。

[主报告重建](report-reconstruction.json) 从原216次账本及归档生成独立输出，runs、summary、failure-audit、paired四份报告逐字节一致，所有原报告校验和及231份绑定源码快照通过。当前源码变化使旧实时freeze检查按预期拒绝，见 [拒绝记录](current-freeze-rejected.txt)；历史复核显式验证原快照哈希，不修改manifest，不跳过执行身份门禁启动队列。另有2340个已跟踪历史证据/原冻结文件前后哈希一致；旧147次和285条未开始保持历史边界。

控制台失败输出的Git展示副本仅规范换行和行尾空格，原输出留在.tmp/stage12-raw-console/，原始与展示哈希见 [收据](console-log-presentation.json)；账本与内容寻址工件保持原字节。

版本0.1.39在pyproject、运行时、uv.lock与已安装元数据中一致。验证完成后仅按本阶段范围提交和推送；停止实施，不自动同步主spec、归档change、重新冻结或启动任何模型队列。
