# memory-automation-lifecycle

## Purpose
规范自动记忆提取的快照与提交锁边界、显式操作优先的冲突处理、治理输入预算，以及正常退出等待和再次中断取消的完整生命周期。

## Requirements

### Requirement: LLM 等待期间不持有记忆存储锁
Memory Extraction SHALL 只在读取快照和应用结果时持有用户或项目记忆锁，并 SHALL 在等待 Provider 响应期间释放这些锁。

#### Scenario: 提取器等待慢速 Provider
- **WHEN** Memory Extraction 已读取快照并正在等待 Provider 返回
- **THEN** 用户显式的 manage_memory 写入可以获得对应存储锁并完成

### Requirement: 显式记忆操作优先于后台结果
Memory Extraction SHALL 在重新加锁后检测快照以来的冲突，只应用未冲突的后台变更，并 SHALL 跳过和记录冲突项。

#### Scenario: 用户在提取期间修改同一条记忆
- **WHEN** Provider 返回的操作基于旧值，而用户已经显式更新或删除该条目
- **THEN** 后台操作被跳过、用户结果保留且冲突被记录

#### Scenario: 提取期间修改无关条目
- **WHEN** 用户只修改与后台操作无关的记忆条目
- **THEN** 未冲突的后台操作正常应用

### Requirement: Memory Governance 输入有固定上限
Memory Governance SHALL 最多选择最近 20 个 Session，并 SHALL 将发送给 Provider 的序列化历史与记忆输入限制在 50,000 字符以内；超过上限时 SHALL 优先保留较新内容。

#### Scenario: 历史数量超过限制
- **WHEN** 可治理历史包含超过 20 个 Session 或序列化后超过 50,000 字符
- **THEN** Provider 输入满足两个上限且较旧内容先被排除

### Requirement: 正常退出等待且允许再次中断
应用关闭时 SHALL 停止接收新的 Memory Extraction 任务、显示等待状态并持续等待已接收任务完成；用户再次发出中断时 SHALL 取消当前提取和剩余队列并继续退出。

#### Scenario: 后台提取正常完成
- **WHEN** 用户退出且所有提取任务最终返回
- **THEN** 应用等待任务落盘后正常关闭

#### Scenario: Provider 卡住后再次中断
- **WHEN** 应用正在等待记忆提取且用户再次按下 Ctrl+C
- **THEN** 当前提取和待处理队列被取消，应用继续关闭共享资源并退出
