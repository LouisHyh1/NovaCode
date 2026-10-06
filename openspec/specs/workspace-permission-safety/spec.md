# workspace-permission-safety

## Purpose
限定文件访问和内部状态的规范化项目边界，防止父目录与符号链接逃逸，并要求权限配置损坏时保守回退、保留内建限制及显示来源明确的告警。

## Requirements

### Requirement: Workspace 仅包含项目树
文件工具 SHALL 只允许访问规范化后位于项目根目录或其后代的路径，并 SHALL 拒绝解析后位于项目外的绝对路径、父目录跳转、符号链接和系统临时路径。

#### Scenario: 访问项目内文件
- **WHEN** 工具请求的路径解析后位于项目根目录内
- **THEN** Workspace 边界检查允许该路径继续进入正常权限流程

#### Scenario: 访问系统临时目录
- **WHEN** 工具请求 `/tmp`、`/private/tmp` 或其他项目外临时路径
- **THEN** Workspace 边界检查拒绝该请求，即使路径由 NovaCode 创建

#### Scenario: 符号链接逃逸
- **WHEN** 项目内路径通过符号链接解析到项目根目录外
- **THEN** Workspace 边界检查拒绝该请求

### Requirement: 内部状态留在项目内
NovaCode 需要由文件工具或运行时访问的项目状态和中间文件 SHALL 存放在项目根目录内的 `.novacode/`，且 SHALL 经过与其他项目文件相同的规范化边界检查。

#### Scenario: 创建内部状态文件
- **WHEN** NovaCode 为当前项目创建可恢复状态或中间文件
- **THEN** 文件位于项目内 `.novacode/` 且无需系统临时目录例外

### Requirement: 损坏权限配置保守回退并告警
权限配置解析失败时系统 SHALL 忽略无效的自定义允许规则，保留内建硬限制和默认批准流程，并 SHALL 显示包含配置来源与错误原因的用户可见警告。

#### Scenario: 项目权限配置语法无效
- **WHEN** NovaCode 加载到无法解析或校验失败的权限配置
- **THEN** 应用继续启动但不应用其中任何自定义放行，并在 TUI 显示明确警告

#### Scenario: 权限配置有效
- **WHEN** 权限配置成功解析并通过校验
- **THEN** 系统应用其规则且不显示配置损坏警告
