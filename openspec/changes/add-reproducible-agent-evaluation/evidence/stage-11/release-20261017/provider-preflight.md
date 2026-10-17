# 启动前的远端公开身份与价格核对

2026-10-17 仅查阅官方公开文档，没有在推送前调用模型。依据 [官方价格说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，旧请求别名 deepseek-v4-flash 仍可调用，服务为 DeepSeek-V4.1-Flash；不更换已冻结请求名或模型策略。远端权重仍不能不可变固定，后续逐请求保留实际返回模型名。

官方列示 Flash 每百万 Token 的空闲价格分别为缓存输入 0.02 元、未缓存输入 1 元、输出 4 元，高峰价格为其两倍；实际费用按执行时段和已知分项 usage 计算，未知部分不记零。这次核对未发现需要修改原 Token/Provider/工具/秒数预算的公开变化，不把 Token 总预留声称为人民币账单上限。

[Anthropic 兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/) 继续支持既定端点及 thinking；budget_tokens 被忽略的限制保持记录。当前使用的 DeepSeek 别名兼容性以价格页明确的旧别名说明为准，不因兼容页示例换用新推荐名称而擅自替换实验请求名。
