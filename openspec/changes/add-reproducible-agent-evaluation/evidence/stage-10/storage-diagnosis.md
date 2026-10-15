# C盘增长排查与F盘下载配置

2026-10-13 用户报告任务期间 C 盘接近满，清理后剩余约 12.4 GiB。当前评测使用 Ubuntu-22.04 中的 Snap Docker，`DockerRootDir=/var/snap/docker/common/var-lib-docker`；该发行版实际位于 `F:\WSL\Ubuntu-22.04\ext4.vhdx`，主镜像和容器数据已经落在 F 盘。C 盘另一个停止的 Ubuntu 虚拟磁盘约 1.25 GiB，最后写入时间为 2026-09-10，不是本轮评测发行版。

此前 Windows curl 准备通道将大 blob 暂存到 `C:\Users\LouisHyh\AppData\Local\Temp`，是本轮已确认会写 C 盘的下载环节；中断可能留下尚未清理的临时目录。当前检查没有 `nova-registry-*` 残留，不能据此复原用户清理前的占用峰值。此前切换到 WSL 原生 curl 时使用的 `/tmp` 在 F 盘发行版内，也不是 C 盘镜像主存储。

当前快照显示 `C:\pagefile.sys` 已分配 13039 MiB，Temp 目录合计 2290518147 字节，三个 WSL swap.vhdx 合计约 0.36 GiB；`.codex` 合计约 4.12 GiB，以已安装程序版本等文件为主。Windows 进程快照中 MsMpEng 私有内存约 5103 MiB、cmd 约 2413 MiB，存在明显内存压力来源。缺少任务开始前的分页文件和磁盘快照，不能把 C 盘全部历史增长断言为 Docker 下载或分页文件增长；此次未调整 Windows 系统分页文件或删除用户缓存。

两种 curl 通道现在都支持 `NOVACODE_REGISTRY_TEMP`。恢复脚本显式设置 `/mnt/f/NovaCode-evaluation-cache/registry-temp`，Windows 对应路径为 `F:\NovaCode-evaluation-cache\registry-temp`；Docker 主存储继续使用 F 盘上的原 ext4 镜像，原 OCI 缓存读取路径和镜像身份保持可用。新增回归先证明原生 curl 忽略指定目录，再修复并验证相关 10 项测试通过。

真实 Registry 1 MiB 下载已在 F 盘完成，内容与已有官方完整 blob 的对应前缀一致，临时目录正确清理；见 [下载核验](f-drive-download-probe.json) 和 [存储配置](f-drive-storage-policy.json)。个人 `.wslconfig` 已备份到 F 盘，并新增 `swapFile=F:\\NovaCode-evaluation-cache\\wsl-swap.vhdx`；内存 6 GiB、CPU 4、swap 2 GiB 等原值保持不变，该交换文件位置配置在下次 WSL 虚拟机重启时生效。

恢复前确认没有资格控制器或所属判题容器活动。`stage10-frozen-v9` 复用 21 条已开始验收的首个记录；中断的 Reflex-4510 第三次镜像准备没有开始原始测试，单独留证后按新身份恢复。会话为 `nova-stage10-qual-v9`，资源、600 秒上限、配额和种子不变，不重跑已有原始/参考验收结果。冻结模型调用仍为零，第十阶段未达完整出口前不提交推送。

配置依据：[Microsoft WSL设置](https://learn.microsoft.com/windows/wsl/wsl-config)、[Docker存储目录设置](https://docs.docker.com/engine/daemon/)。以上路径和大小由本机读取，文档用于核对配置含义。

后续同会话复查发现 C 盘剩余降至约 2.92 GiB，而分页文件分配由 13039 MiB 增至 22450 MiB，增加 9411 MiB，足以解释本次复查期间约 9 GiB 的下降。此时 Defender `MsMpEng` 私有提交约 12195 MiB，随后达到 12426 MiB；这些是实测内存压力证据，但尚未定位 Defender 的具体扫描对象，不能断言它正在扫描某个镜像文件或发生内存泄漏。系统启用自动管理分页文件；[Microsoft 分页文件说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows)解释了系统提交压力下的自动增长机制。用户明确选择只改下载目录、保留 Windows 分页文件设置，因此未修改分页文件、未停用 Defender、未自动重启 Windows。

新增完整 blob 迁移已完成：160 个文件、4731851322 字节，逐个复制并确认完整 SHA-256 和大小后，以原路径符号链接保持访问。保存位置为 `F:\NovaCode-evaluation-cache\verified-blobs-20261013`，清单见 `f-drive-blob-preservation.json`；迁移后 WSL 内部可用约 30.36 GiB，F 宿主卷可用约 75.53 GiB。C 盘低空间期间暂缓新增大镜像下载，继续事前认知审查，已有资格结果不重跑。

用户恢复空间后，`stage10-frozen-v9` 继续取得 Reflex-4230、Patroni-3310 和 Falcon-2450 的首次有效资格；后续新身份取得 Patroni-3338，中等有效候选增至 6。期间再次观察到自动分页文件增长及 Defender 内存增长，因此原生下载临时文件改放 F 盘 WSL 内部，并将 14 个常用共享层、727881157 字节按完整 SHA-256 复制到同一文件系统，原宿主缓存仍保留；见 [热层核验](native-hot-cache-proof.json)。这减少 Windows 文件层的大文件 I/O，但未证明 Defender 内存问题已经解决。

为维持既定 20 GiB 准备余量，对 PDM-3420、CFN-3866 和 Checkov-6922 三个已失败候选核验完整官方 manifest、config 和全部 blob 的大小/完整 SHA-256，以及已导入 RootFS 和无容器引用后，释放 Docker 中的展开副本；全部 OCI 字节及首次验收记录保留，见 [保留与释放证据](invalid-image-release-proof.json)。本机 containerd 的实测 Image ID 等于 manifest digest，config 具有另一独立指纹。PDM-3420 已实际通过离线恢复入口重建到原 digest/Image ID/RootFS，随后再次释放；未重跑其失败验收。

WSL Windows 侧占用曾达约 6.1 GiB，而 Linux 文件缓存约 3.6 GiB。在资格准备期间同步脏页后一次写入 `drop_caches=1`，实际回收 3818369024 字节干净页缓存；随后 Windows 侧 WSL 占用降至约 2.4 GiB，见 [回收快照](wsl-page-cache-reclaim.json)。这是此次调试的临时回收，没有停止应用进程，也没有修改 Windows 分页文件。[Linux 内核说明](https://docs.kernel.org/admin-guide/sysctl/vm.html#drop-caches)指出该操作回收可重建缓存并会增加后续 I/O，不能视为固定防止缓存增长的策略或正式性能测量规则。

直连 TLS 再次持续 EOF 后，Windows 现有代理通道改为二进制 stdout 交由 WSL 写文件，响应头借助同目录 UNC 路径传递，大 blob 不在 Windows Temp 落地。真实 1 MiB 下载与官方完整缓存前缀完全一致，见 [Windows 网络与 WSL 存储核验](windows-stream-download-probe.json)。两种 curl 通道的临时目录、断流保留、范围/完整摘要校验与凭证脱敏回归共 11 项通过；最新完整质量门禁仍为 1019 项通过。当前会话 `nova-stage10-qual-v12`，复用 33 条首次开始的记录，883 个文件字节一致；见 [恢复身份](preparation-recovery-policy-v12.json) 与 [逐文件核验](reused-first-attempts-verification-v12.json)。

后续 Jupyter-ai-1294 和 Patroni-3112 完成首次有效资格，中等数量增至 8。请求超时恢复为传入的 60 秒，并为元数据重试设置总重试时间，避免单次准备中的 HTTP 等待吞没整体 600 秒上限；原始/参考测试上限和资源未改变。全局准备磁盘不足现在暂停选择，不将资源问题算作候选排除；13 项相关回归与完整 1021 项质量门禁通过。12 份字节相同的旧资格运行时改为同一 F 盘文件系统上的硬链接，SHA-256 与原路径不变，恢复约 2 GiB，见 [去重核验](runtime-dedup-proof.json)。新增失败的 Checkov-6657 与 Reflex-4958 也在完整 OCI 核验后释放展开副本，见 [第二轮](invalid-image-release-proof-v2.json) 和 [第三轮](invalid-image-release-proof-v3.json)。

C 盘此后再次剩余约 1.85 GiB，Defender 私有提交超过 12 GiB，仍需要用户处理 Windows 系统资源。第二次临时回收 WSL 干净页缓存释放 4061552640 字节，见 [第二次回收](wsl-page-cache-reclaim-v2.json)，没有修改 Windows 分页文件。WinDefend 的实测 `CanStop=false` 且当前账户不是管理员；本轮未尝试绕过服务保护、禁用防护或自动重启 Windows。选择器已在未开始原始测试的 CFN-3856 镜像准备阶段保存并停止，没有活动验收容器；见 [停稳证据](storage-pause-v13.json)。诊断目前证明了下载位置与内存/分页压力的区别，但没有定位 Defender 持续分配内存的具体代码或扫描对象，因此不能宣称内存泄漏已被修复。

2026-10-15 用户恢复后，初始 C/F 盘分别可用 12.40/70.27 GiB，后台任务恢复。新增 57 个完整 blob、3386669628 字节复制到 F 盘目录并核对完整 SHA-256，原路径原子切换为符号链接，见 [保留清单](f-drive-blob-preservation-20261015.json)。另外五个新失败候选仅在全部 OCI 字节、RootFS 和无容器引用核验后释放 Docker 展开副本，见第四/五轮保留证明；未删改原始资格或未完成下载。后续质量检查期间 C 盘约余 9.82 GiB，自动分页文件分配为 15317 MiB，WSL 私有提交约 6.01 GiB；当次进程快照未出现 MsMpEng，不能把这次压力继续归因于 Defender。下载主体仍在 F 盘 WSL 文件系统，没有修改 Windows 分页文件或自动重启。

本轮后续再保留完整 blob 21 个/1596986338 字节和 12 个/1560779973 字节，见第二、三批保留清单；12 个已结束先导任务的 Git pack 按完整 SHA-256 迁至 F 盘缓存，原路径仍可读，逐工作区 Git HEAD 对象读取通过。14 个原生热缓存副本在双方完整 SHA 一致后恢复为指向原保留文件的链接，保留全部字节；此前 16 个传输归档重新核验，大小与完整 SHA 全部未变，见 [旧归档复核](historical-transport-recheck-20261015.json)。Docker 仅释放已失败、完整 OCI 可恢复且无容器引用的展开副本；本轮新增的第六至八轮证明均保留。

最终质量核验期间 Windows 分页文件分配从 15317 MiB 增至 18430 MiB，增长 3113 MiB，C 盘降至约 7.1 GiB。完整测试通过后临时回收 4067459072 字节干净 WSL 页缓存，见 [第三次回收](wsl-page-cache-reclaim-v3.json)；没有终止应用进程或修改 Windows 分页文件。F 盘 WSL 交换文件现已存在，Linux 检测到 2 GiB swap；最终宿主快照见 [storage-final-20261015.json](storage-final-20261015.json)。第十阶段已达到 FROZEN_VALID，准备与验收后台工作结束，不自动启动正式模型实验；本次缓存回收不作为正式性能测量规则。
