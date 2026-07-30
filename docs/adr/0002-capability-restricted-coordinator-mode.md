# Coordinator Mode 采用能力收缩

Coordinator Lead 不获得文件写工具、通用 shell 或任意验证命令，只能使用只读、团队调度和受控 TeamIntegrate/TeamFinalize；验证命令必须来自用户配置的 Verification Profile。虽然保留 Bash 会让 Git 操作更灵活，但它也能通过重定向、脚本或任意程序绕过“Lead 不写代码”的承诺，因此 coordinator 的约束必须由工具能力边界强制执行，而不能只依赖提示词。
