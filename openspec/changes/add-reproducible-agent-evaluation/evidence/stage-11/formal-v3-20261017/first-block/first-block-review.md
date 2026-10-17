# v3 首个真实配对块复核

2026-10-17，在提交并推送 02b2da5（0.1.37）后启动。新 campaign 为 novacode-compact-20261017-v3-r1，清单 SHA-256 0cf2d3fb6ba40d1f4f62b17d31cdb975c19eb5b2adb0ac89d0572922f703fa2f；FS17 的 full、eager-schema、no-compression 三次全部 completed、独立验收 passed、scope/cleanup passed，strict=3/3。三次均计入新 216 行，属于一个独立任务，不能据此推断全题库效果或消融收益。

实际版本、25 轮/单次 Agent.run、压缩与 Schema 策略、空记忆、无辅助 Agent、同题预算和序列身份均由原始账本重建后与原 sample 逐项比较。Provider 共 74 次，实测 Token 941,531，工具调用 59 次，unknown usage=0；no-compression 中一次工具错误已恢复，原错误保留，不是 Provider 或基础设施错误。请求/响应均保留兼容别名，实际服务版本仍按公开映射单列，远端权重不可变固定未知。

三份归档除原运行器的释放前恢复外，又从各自 run tar 和可信 baseline tar 独立重建，并逐文件验证业务内容、类型和必要模式。原结果、样本、账本和归档哈希记录在对应配置 JSON 中，未覆写原终态。首块没有 offload 或摘要触发，不能宣称此块验证了压缩收益。

少量新 WSL 只读探针曾返回 Wsl/Service/0x8007274c，期间通过 UNC 读取持久结果，既有模型调度正常完成；未重启 VM、取消模型或回填时间。显式指定用户 louishyh 的 WSL 调用已成功，后续继续用该入口。监督器从第 4 行继续新计划，普通任务失败与预算终止如实保留；Provider、基础设施、身份或保全异常则停止后续块，不重跑已开始条目。
