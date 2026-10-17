# 规范场景审计

本表逐项审阅现行六份 spec 的所有 Scenario，区分本轮机制回归、此前真实环境/模型证据和协议人工审阅。测试通过只证明相应机制；没有实际触发的反事实场景不称本轮真实事件。所有 tests 均纳入本轮 1060 项完整回归，历史实验记录保持原字节。

## agent-evaluation-corpus

| 规范场景 | 证据入口 | 审阅结论及边界 |
| --- | --- | --- |
| [候选数量达到目标但未核验](../../specs/agent-evaluation-corpus/spec.md#L10) | [test_candidates_are_not_admitted_and_admission_replays_after_restart](../../../../../tests/evaluation/test_contracts.py#L355) | 已核对；机制：候选不计入库 |
| [历史失败任务满足元数据选择条件](../../specs/agent-evaluation-corpus/spec.md#L17) | [test_compact_selection_is_fixed_metadata_only_and_all_quotas_hold](../../../../../tests/evaluation/test_compact.py#L30) | 已核对；机制：仅元数据；另审阅 selection.json |
| [客观资格失效导致配额不可满足](../../specs/agent-evaluation-corpus/spec.md#L21) | [test_compact_infeasible_metadata_stops_without_relaxing_quotas](../../../../../tests/evaluation/test_compact.py#L53) | 已核对；机制：不可行则停止 |
| [一个多轮专项任务触发多个 Agent Run](../../specs/agent-evaluation-corpus/spec.md#L28) | [test_multiturn_worker_counts_all_stages](../../../../../tests/evaluation/test_specialty.py#L230) | 已核对；机制：多请求合为一次评测 |
| [参考补丁不稳定](../../specs/agent-evaluation-corpus/spec.md#L35) | [test_invalid_live_qualification_cannot_admit](../../../../../tests/evaluation/test_contracts.py#L395) | 已核对；机制：不稳定资格拒绝 |
| [Agent 自行运行仓库已有测试](../../specs/agent-evaluation-corpus/spec.md#L42) | [live-validation.md](../stage-8/live-validation.md) | 已核对；历史真实环境：公开包与独立验收分离 |
| [两个不同标识对应同源问题](../../specs/agent-evaluation-corpus/spec.md#L49) | [test_cross_suite_issue_renamed_defect_and_duplicate_candidate](../../../../../tests/evaluation/test_contracts.py#L299) | 已核对；机制：同源隔离 |
| [修复通过目标用例但引入回归](../../specs/agent-evaluation-corpus/spec.md#L56) | [test_correct_control_wrong_answer_scope_and_missing_behavior](../../../../../tests/evaluation/test_frozen.py#L27) | 已核对；机制：正负控制及范围 |
| [回答换用另一条有效调用路径](../../specs/agent-evaluation-corpus/spec.md#L63) | [test_alternative_paths_and_wrong_relation](../../../../../tests/evaluation/test_specialty.py#L65) | 已核对；机制：有效替代路径 |
| [定位回答字段具有固定编码](../../specs/agent-evaluation-corpus/spec.md#L67) | [test_localization_publicly_defines_all_hidden_field_encodings](../../../../../tests/evaluation/test_formal.py#L77) | 已核对；机制：公开格式完整 |
| [新测试只在导入阶段失败](../../specs/agent-evaluation-corpus/spec.md#L74) | [test_invalid_added_tests_fail](../../../../../tests/evaluation/test_specialty.py#L137) | 已核对；机制：导入错误不算检错 |
| [搜索失败后使用其他工具完成](../../specs/agent-evaluation-corpus/spec.md#L81) | [test_first_search_fault_and_zero_coverage](../../../../../tests/evaluation/test_specialty.py#L162) | 已核对；机制：固定故障；真实恢复见 stage-7 报告 |
| [冻结后发现验收器漏掉一项需求](../../specs/agent-evaluation-corpus/spec.md#L88) | [design.md](../../design.md) | 已核对；协议处置及历史记录审阅：公开格式缺陷后保留旧结果并换新身份，不能人工改成功 |
| [以新 campaign 名称宣称全新测试集](../../specs/agent-evaluation-corpus/spec.md#L95) | [最终评测报告.md](../stage-11/formal-v3-20261017/publication-0.1.38/最终评测报告.md) | 已核对；报告审阅：披露已查看父池及条件结论 |

## agent-evaluation-experiments

| 规范场景 | 证据入口 | 审阅结论及边界 |
| --- | --- | --- |
| [需要修改摘要提示](../../specs/agent-evaluation-experiments/spec.md#L12) | [pilot-validation.md](../stage-9/pilot-validation.md) | 已核对；历史先导与协议审阅：调试仅在开发集，未以正式结果修改摘要提示 |
| [只记录模型名称而没有预算](../../specs/agent-evaluation-experiments/spec.md#L21) | [test_missing_or_changed_numeric_budget_and_single_agent_contract](../../../../../tests/evaluation/test_frozen.py#L132) | 已核对；机制：预算不完整拒绝 |
| [关闭压缩组更早达到 Token 上限](../../specs/agent-evaluation-experiments/spec.md#L30) | [test_category_temporary_total_and_pending_reservations_have_same_limits](../../../../../tests/evaluation/test_observation.py#L508) | 已核对；机制：同题共享上限；真实预算终止见主报告 |
| [试图计算两机制全部关闭的结果](../../specs/agent-evaluation-experiments/spec.md#L37) | [evaluation-protocol.md](../../evaluation-protocol.md) | 已核对；协议审阅：仅两个条件比较，无双关闭组或交互结论 |
| [三次中只有一次成功](../../specs/agent-evaluation-experiments/spec.md#L44) | [test_task_paired_bootstrap_preserves_three_repeats_and_marks_degeneracy](../../../../../tests/evaluation/test_formal.py#L89) | 已核对；机制：三次先聚合，不取最好 |
| [精简报告仍使用旧实验分母](../../specs/agent-evaluation-experiments/spec.md#L48) | [test_report_status_denominators_are_derived_and_cross_campaign_is_rejected](../../../../../tests/evaluation/test_compact.py#L102) | 已核对；机制：按 campaign 分母 |
| [配置之间出现不同缓存命中](../../specs/agent-evaluation-experiments/spec.md#L57) | [test_normalization_verified_cache_and_reasoning_subsets](../../../../../tests/evaluation/test_observation.py#L240) | 已核对；机制及原始 usage 审阅：缓存字段不丢弃 |
| [补跑成功但首次失败](../../specs/agent-evaluation-experiments/spec.md#L64) | [test_retry_keeps_original_and_plan_keys_unique](../../../../../tests/evaluation/test_contracts.py#L189) | 已核对；机制：补跑不覆盖 |
| [历史补丁恢复后通过独立判题](../../specs/agent-evaluation-experiments/spec.md#L71) | [offline-validation.md](../stage-11/compact-v3-20261016/offline-validation.md) | 已核对；历史审计：派生评分与原评分单列，候选不冒充完整恢复 |
| [规划文档更新后调度器尝试启动模型](../../specs/agent-evaluation-experiments/spec.md#L78) | [test_unapproved_campaign_cannot_reach_environment_or_provider](../../../../../tests/evaluation/test_compact.py#L136) | 已核对；机制：未授权入口在模型前拒绝 |
| [六道专项题各重复三次](../../specs/agent-evaluation-experiments/spec.md#L87) | [test_task_paired_bootstrap_preserves_three_repeats_and_marks_degeneracy](../../../../../tests/evaluation/test_formal.py#L89) | 已核对；机制：任务级三重复配对 |
| [按实际触发挑出表现好的样本](../../specs/agent-evaluation-experiments/spec.md#L94) | [最终评测报告.md](../stage-11/formal-v3-20261017/publication-0.1.38/最终评测报告.md) | 已核对；报告审阅：全任务保留，实际触发单列 |
| [所有观察到的配对差值为零](../../specs/agent-evaluation-experiments/spec.md#L101) | [test_task_paired_bootstrap_preserves_three_repeats_and_marks_degeneracy](../../../../../tests/evaluation/test_formal.py#L89) | 已核对；机制：退化标记及 10000 次 bootstrap |
| [按需Schema增加调用且未节省总Token](../../specs/agent-evaluation-experiments/spec.md#L108) | [最终评测报告.md](../stage-11/formal-v3-20261017/publication-0.1.38/最终评测报告.md) | 已核对；报告审阅：接受区间含零和负收益，未择优删题 |

## agent-evaluation-observability

| 规范场景 | 证据入口 | 审阅结论及边界 |
| --- | --- | --- |
| [摘要忽略其用量事件](../../specs/agent-evaluation-observability/spec.md#L10) | [test_main_and_summary_ignored_usage_are_counted_once](../../../../../tests/evaluation/test_observation.py#L96) | 已核对；机制：摘要不消费 usage 仍计一次 |
| [缓存命中已包含在输入字段中](../../specs/agent-evaluation-observability/spec.md#L17) | [test_normalization_verified_cache_and_reasoning_subsets](../../../../../tests/evaluation/test_observation.py#L240) | 已核对；机制：已含缓存不重复加 |
| [SDK默认对请求内部重试](../../specs/agent-evaluation-observability/spec.md#L24) | [test_real_sdk_500_has_one_http_attempt_with_zero_retries](../../../../../tests/evaluation/test_observation.py#L278) | 已核对；真实 SDK、模拟 HTTP；一次尝试 |
| [连接中断前未收到最终用量](../../specs/agent-evaluation-observability/spec.md#L31) | [test_stream_failure_unknown_usage_preserves_error_and_stops](../../../../../tests/evaluation/test_observation.py#L177) | 已核对；机制；本阶段真实取消同样有未知 usage |
| [两个同名工具同时执行](../../specs/agent-evaluation-observability/spec.md#L38) | [test_same_name_concurrent_tools_full_arguments_and_retry_counts](../../../../../tests/evaluation/test_observation.py#L416) | 已核对；机制：按 call_id 关联并发 |
| [容器下载慢但 Agent 执行正常](../../specs/agent-evaluation-observability/spec.md#L45) | [test_monotonic_boundaries_redaction_artifacts_and_truncated_rebuild](../../../../../tests/evaluation/test_observation.py#L547) | 已核对；机制：准备、主执行、判题、清理分时 |
| [长上下文任务未达到摘要阈值](../../specs/agent-evaluation-observability/spec.md#L52) | [最终评测报告.md](../stage-11/formal-v3-20261017/publication-0.1.38/最终评测报告.md) | 已核对；真实主集覆盖审阅：摘要请求为零，不能称摘要收益 |
| [先搜索错误后修复遗漏回归](../../specs/agent-evaluation-observability/spec.md#L59) | [failure-audit.json](../stage-11/formal-v3-20261017/memory-resume-20261017/final-report/failure-audit.json) | 已核对；逐例自动归因及原事件引用；主报告人工审阅不改评分 |
| [报告生成途中中断](../../specs/agent-evaluation-observability/spec.md#L66) | [test_interrupted_request_rebuild_keeps_unknown_and_known_lower_bound](../../../../../tests/evaluation/test_observation.py#L586) | 已核对；机制；本阶段另对 216 次报告离线重建一致 |
| [缺失补丁只能恢复部分文件](../../specs/agent-evaluation-observability/spec.md#L73) | [offline-validation.md](../stage-11/compact-v3-20261016/offline-validation.md) | 已核对；历史分级：11 份候选、缺口与第147次独立重建分列 |
| [空补丁与缺失补丁需要区分](../../specs/agent-evaluation-observability/spec.md#L80) | [test_archive_failure_preserves_business_and_baseline](../../../../../tests/evaluation/test_artifact_integrity.py#L144) | 已核对；机制：缺补丁、损坏、归档失败保现场 |
| [文件路径包含敏感词但哈希不是凭证](../../specs/agent-evaluation-observability/spec.md#L87) | [test_baseline_hashes_are_not_redacted_by_sensitive_path_names](../../../../../tests/evaluation/test_artifact_integrity.py#L128) | 已核对；机制：必要哈希保留，认证另行脱敏 |
| [旧请求只保存 APIStatusError](../../specs/agent-evaluation-observability/spec.md#L91) | [test_diagnostics_are_bounded_redacted_and_preserve_http_facts](../../../../../tests/evaluation/test_artifact_integrity.py#L178) | 已核对；机制及历史审阅：旧缺失状态不凭类名回填 |

## agent-evaluation-runner

| 规范场景 | 证据入口 | 审阅结论及边界 |
| --- | --- | --- |
| [真实模型产生工具调用](../../specs/agent-evaluation-runner/spec.md#L10) | [review.json](tmux-r2/review.json) | 已核对；本阶段真实模型及 tmux；正式工具调用与 UI 审批 |
| [上一次运行产生摘要与记忆](../../specs/agent-evaluation-runner/spec.md#L17) | [test_fresh_session_state_and_formal_tool_chain](../../../../../tests/evaluation/test_runner.py#L99) | 已核对；机制：独立 Session/工作区 |
| [任务依赖的 Python 版本不同于 Agent 运行版本](../../specs/agent-evaluation-runner/spec.md#L24) | [environment-ready.md](../stage-1/environment-ready.md) | 已核对；历史真实容器：双解释器与同工作区 |
| [当前用户无权访问容器服务](../../specs/agent-evaluation-runner/spec.md#L31) | [environment-ready.md](../stage-1/environment-ready.md) | 已核对；历史实际权限拒绝和后续环境核验，不将环境失败算解题失败 |
| [摘要请求将耗尽调用预算](../../specs/agent-evaluation-runner/spec.md#L38) | [test_budget_includes_summary_discovery_denial_and_total](../../../../../tests/evaluation/test_observation.py#L483) | 已核对；机制：摘要与发现纳入预算 |
| [请求失败且未返回用量](../../specs/agent-evaluation-runner/spec.md#L45) | [test_stream_failure_unknown_usage_preserves_error_and_stops](../../../../../tests/evaluation/test_observation.py#L177) | 已核对；机制：未知停止追加 |
| [返回最终文本但修复错误](../../specs/agent-evaluation-runner/spec.md#L52) | [test_termination_acceptance_and_strict_are_independent](../../../../../tests/evaluation/test_runner.py#L253) | 已核对；机制：正常结束与修复正确分列 |
| [超时工具仍保留子进程](../../specs/agent-evaluation-runner/spec.md#L59) | [test_slow_bash_timeout_kills_owned_process](../../../../../tests/evaluation/test_runner.py#L348) | 已核对；机制：所属子进程清理 |
| [初始忽略文件改变且初始符号链接未改变](../../specs/agent-evaluation-runner/spec.md#L66) | [test_typed_initial_baseline_roundtrips_real_state](../../../../../tests/evaluation/test_artifact_integrity.py#L53) | 已核对；机制：类型化基线、链接及忽略文件 |
| [初始忽略清单超过系统命令行容量](../../specs/agent-evaluation-runner/spec.md#L70) | [test_large_initial_paths_do_not_exceed_argv](../../../../../tests/evaluation/test_artifact_integrity.py#L25) | 已核对；机制：超过 ARG_MAX 的初始清单 |
| [提取失败而归档中其他文件均校验通过](../../specs/agent-evaluation-runner/spec.md#L77) | [test_archive_failure_preserves_business_and_baseline](../../../../../tests/evaluation/test_artifact_integrity.py#L144) | 已核对；机制：成员校验不替代业务可重建性 |
| [归档恢复后文件类型或内容不一致](../../specs/agent-evaluation-runner/spec.md#L81) | [test_archive_failure_preserves_business_and_baseline](../../../../../tests/evaluation/test_artifact_integrity.py#L144) | 已核对；机制：损坏/恢复失败保留现场；正负链见 stage-11 |
| [候选恢复补丁通过测试但缺少最终状态证据](../../specs/agent-evaluation-runner/spec.md#L88) | [offline-validation.md](../stage-11/compact-v3-20261016/offline-validation.md) | 已核对；历史恢复分级：通过测试仍只记候选，不改历史 strict |
| [自动化成功而 TUI 未传递实验开关](../../specs/agent-evaluation-runner/spec.md#L95) | [test_tui_manual_and_resume_definitions_follow_active_agent_policy](../../../../../tests/agent/test_tool_exposure.py#L270) | 已核对；机制：产品入口策略；历史真实两入口见 stage-5/6 报告 |

## context-compression-policy

| 规范场景 | 证据入口 | 审阅结论及边界 |
| --- | --- | --- |
| [老配置没有新开关](../../specs/context-compression-policy/spec.md#L10) | [test_old_configuration_defaults_and_strict_boolean](../../../../../tests/agent/test_compression_policy.py#L31) | 已核对；机制：兼容默认及严格布尔 |
| [超限触发紧急入口](../../specs/context-compression-policy/spec.md#L17) | [test_disabled_agent_overflow_does_not_retry_or_compact](../../../../../tests/agent/test_compression_policy.py#L83) | 已核对；机制；历史真实端点溢出见 stage-5 报告 |
| [禁用时请求手动压缩](../../specs/context-compression-policy/spec.md#L24) | [test_tui_default_assembly_and_disabled_manual_notice](../../../../../tests/agent/test_compression_policy.py#L107) | 已核对；机制：手动压缩明确禁用 |
| [自动摘要生成后未改善历史长度](../../specs/context-compression-policy/spec.md#L31) | [test_unaccepted_summary_still_costs_and_does_not_move_anchor](../../../../../tests/agent/test_compression_policy.py#L207) | 已核对；机制：请求计费与接受分离 |
| [禁用压缩运行尝试加载已经摘要的旧状态](../../specs/context-compression-policy/spec.md#L38) | [test_disabled_resume_preserves_raw_or_rejects_incompatible](../../../../../tests/agent/test_compression_policy.py#L120) | 已核对；机制：跨策略历史拒绝 |
| [原始初始输入已超出双方窗口](../../specs/context-compression-policy/spec.md#L45) | [frozen.py](../../../../../src/novacode/evaluation/frozen.py) | 已核对；源码审阅 input_check + 冻结初始输入门禁；三组相同窗口及输入，非放宽窗口 |

## progressive-tool-schema

| 规范场景 | 证据入口 | 审阅结论及边界 |
| --- | --- | --- |
| [同题切换为全量曝光](../../specs/progressive-tool-schema/spec.md#L10) | [test_defaults_boolean_and_equal_registered_capability](../../../../../tests/agent/test_tool_exposure.py#L67) | 已核对；机制：相同注册能力及默认全量 |
| [需要未曝光的辅助查询工具](../../specs/progressive-tool-schema/spec.md#L17) | [test_same_request_guess_is_denied_and_next_request_uses_discovery](../../../../../tests/agent/test_tool_exposure.py#L118) | 已核对；机制；真实内置/MCP 发现见 stage-6 报告 |
| [模糊请求匹配多个相似工具](../../specs/progressive-tool-schema/spec.md#L24) | [test_deterministic_bounded_discovery_and_independent_state](../../../../../tests/agent/test_tool_exposure.py#L85) | 已核对；机制：确定性、有界及无匹配 |
| [一轮中发现此前未知的工具](../../specs/progressive-tool-schema/spec.md#L31) | [test_schema_refresh_multiple_runs_compaction_and_reset](../../../../../tests/agent/test_tool_exposure.py#L199) | 已核对；机制：后续轮更新且保持 |
| [只读模式搜索到写入职责](../../specs/progressive-tool-schema/spec.md#L38) | [test_permissions_hooks_plan_and_role_cannot_be_expanded](../../../../../tests/agent/test_tool_exposure.py#L136) | 已核对；机制：权限、角色和 Hook 不扩张 |
| [猜测未曝光的工具名](../../specs/progressive-tool-schema/spec.md#L45) | [test_same_request_guess_is_denied_and_next_request_uses_discovery](../../../../../tests/agent/test_tool_exposure.py#L118) | 已核对；机制：未曝光调用不执行 |
| [上一次重复已经发现全部工具](../../specs/progressive-tool-schema/spec.md#L52) | [test_schema_refresh_multiple_runs_compaction_and_reset](../../../../../tests/agent/test_tool_exposure.py#L199) | 已核对；机制：独立运行重置 |
| [使用基础工具替代辅助工具完成](../../specs/progressive-tool-schema/spec.md#L59) | [test_record_data_and_discovery_cost_in_all_modes](../../../../../tests/evaluation/test_specialty.py#L257) | 已核对；机制：能力及成本一致；主报告保留合法替代与零触发 |

## 阶段出口与事实边界

实施 78/78、开发资产 DEV_ADMITTED、先导 PILOT_COMPLETE、原父池 FROZEN_VALID、新 v3 FORMAL_COMPLETE/REPORT_REVIEWED、本阶段 PRODUCT_TMUX_VALID 分别成立，含义不能互换。v2 仍为147/432，285条未开始；v3为24道独立题、216次原计划，受测0.1.37。0.1.39包含产品焦点修复与质量门禁，不追认为主集受测版本。

本阶段没有新增正式主集样本、隐藏判题重跑或调参；真实产品调用与 Fake/机制成本单列。记忆创建/更新/删除、后台任务完成/取消和借用者关闭由隔离测试证明；本轮没有真实模型长期记忆或多 Agent 效果实验。未重新执行 Windows 原生交互专项，不宣称全平台生产就绪。

报告中保留未知 usage、主集 Layer2 零触发、父池已查看、两个主要比较区间含零和运行条件变更。新旧样本不混算，新增源码使旧实时冻结检查按预期拒绝；历史复核使用原哈希快照，不恢复模型执行。
