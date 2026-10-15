# 冻结问题家族审查

本审查在冻结前完成，只检查任务合同、源码、公开业务事实与独立验收，不使用 NovaCode 在冻结题上的结果。开发题的主要问题是配置读取/错误展示、配置覆盖测试/异步取消资源关闭、clamp/chunks 修复、counter/retry-delay/配置迁移多轮、单提交 CI 查询/测试部署来源区分/数值归一化修复。下面 24 题共享通用构建器与查询服务，但被测业务合同独立。

| ID | 独立问题家族 | 认知跨度与验收重点 |
|---|---|---|
| FS01 | 事件序列化到字节传输 | 追踪 JSON/UTF-8 编码及 sink 写入，不是配置读取 |
| FS02 | 未知路由回退分派 | 查清选择器与 fallback 的实际关系，不是模式配置 |
| FS03 | TTL 缓存过期驱逐 | 同时识别过期判断、驱逐、重新载入路径 |
| FS04 | 批处理日志回滚 | 快照、异常分支和原状态恢复的调用关系 |
| FS05 | 半开区间重叠 | 相接、空区间、包含与负端点；检出闭区间和反向谓词 |
| FS06 | 稳定多值聚合 | 重复 key 不覆盖、值顺序、一次性生成器和输入不变 |
| FS07 | 拓扑顺序与图合法性 | 层内顺序、缺失节点、环、空图和输入不变 |
| FS08 | CSV 单次迭代与转义 | 引号、逗号、字段内换行和生成器消费，不是配置或取消测试 |
| FS09 | 查询参数编码 | Unicode、空格、保留符、多值与顺序；一次搜索故障是共享机制 |
| FS10 | UTF-8 BOM 文件读取 | BOM、普通 Unicode 与空文件；共享命令故障不构成同源缺陷 |
| FS11 | 归档成员路径验证 | 绝对路径、.. 组件、反斜杠与合法相对路径 |
| FS12 | NDJSON 错误行账本 | 空行忽略、物理行号和异常 cause，不能静默丢掉非法事件 |
| FS13 | 十进制流式求和 | 早期 Decimal 精度约束、空输入、一次性迭代器 |
| FS14 | HTTP 多值 header | 名称大小写、重复值顺序、显式空值和无匹配缺省 |
| FS15 | RBAC 权限矩阵 | 角色/动作事实、未知默认拒绝与原文件保持；不是改 retry 参数 |
| FS16 | CSV 事件账本恢复 | 内嵌换行与分隔符、物理记录恢复和数字顺序 |
| FS17 | DAG 传递失效 | 循环图闭包、改动节点、缓存筛除及早/中/末三阶段不变性 |
| FS18 | 版本绑定分页 | 编解码、类型/版本校验、尾页和后续游标组合保持 |
| FS19 | CI 矩阵失败汇总 | 从所有 job 状态计算排序列表与数量，不是换一个 commit 查询 |
| FS20 | CI 重试状态迁移 | 跨两个完整 job 集判断恢复、持续失败和新失败 |
| FS21 | 产物 digest 部署来源 | 以完整 SHA 关联两种来源，拒绝同 key 的错误产物 |
| FS22 | 必需 shard 发布资格 | 合并 CI 必需/可选 shard 与地区授权，两个条件共同判定 |
| FS23 | MIME 协商与处理器分派 | issue/commit/CI 关联、跨两个模块的类型规范化和四层优先级、假值返回保持 |
| FS24 | mount 路由转发 | issue/CI 关联、精确/最长段前缀、重复目标不同 mount、未解码路径与 query 保持 |

工具发现初稿中的几个单记录查询/来源选择过于接近开发题，在本审查中被纠正为 FS19–FS22 的派生事实与跨记录合同；FS23–FS24 补足跨模块行为约束。修订前源码、公开/隐藏资产与两次离线对照都保存在外部缓存的新身份中，未删改原记录。修订后的全部 24 题重新核验后才可冻结，难度与压力标签分别记录。

难度配额为 8 简单、12 中等、4 困难；困难为 FS17、FS18、FS23、FS24，依据组合不变性、图/协议身份及跨模块语义，未依据文件行数、故障次数或是否触发压缩划层。专项类别为 4/4/4/6/6；共享 notes 构建器、故障装置或业务查询协议不表示共享被测问题。

## Live问题家族与开发集隔离

完整固定 verified 题池按事前规则选出 24 道 Live，覆盖 13 个仓库，难度 8/12/4，每仓库不超过 4 道。逐题原始目标失败、原有回归通过、参考三次通过；全部 issue/fix 问题键与 12 道开发 Live 无交集。公开题面与验收资产分别保存，未读取 NovaCode 在冻结任务上的结果。

| ID | 候选 | 难度 | 问题家族 | 认知审查 |
|---|---|---|---|---|
| FL01 | wireservice__csvkit-1281 | easy | live:issue:wireservice/csvkit#1280 | 排除列入口的未知列错误恢复，保留正常列/范围以及非排除入口的原异常合同 |
| FL02 | conan-io__conan-17923 | easy | live:issue:conan-io/conan#17884 | 库文件精确匹配的局部告警规则，保留路径与候选排序 |
| FL03 | python-babel__babel-1132 | easy | live:issue:python-babel/babel#1124 | PO过期消息身份保留context与plural，复用既有键构造 |
| FL04 | pylint-dev__pylint-10240 | easy | live:issue:pylint-dev/pylint#10081 | 单个导入名字定义规则覆盖点分模块名 |
| FL05 | pvlib__pvlib-python-2347 | easy | live:issue:pvlib/pvlib-python#1342 | 单个全无有效角度输入的前置校验，保留正常数值行为 |
| FL06 | pylint-dev__pylint-9772 | easy | live:issue:pylint-dev/pylint#9765 | 指定 unittest 特殊方法命名的局部识别条件，保留已有命名规则 |
| FL07 | jazzband__tablib-613 | easy | live:issue:jazzband/tablib#612 | ODS单元格缺省value属性与递归空值返回的局部读回合同，保持其它数据类型 |
| FL08 | python-attrs__attrs-1417 | easy | live:issue:python-attrs/attrs#1416 | 迭代器形式字段转换只消费一次，保持继承字段生成 |
| FL09 | reflex-dev__reflex-4510 | medium | live:issue:reflex-dev/reflex#4471 | Field默认值与default_factory区别影响状态Var可选类型和foreach元素注解，保留既有默认构造语义 |
| FL10 | patroni__patroni-3338 | medium | live:issue:patroni/patroni#3335 | ETCD gRPC不可用响应转换为transport超时触发现有节点故障转移，避免外层重试继续锁定坏节点；其它错误分类保持 |
| FL11 | jupyterlab__jupyter-ai-1294 | medium | live:issue:jupyterlab/jupyter-ai#1291 | 用户schema与新版schema合并同时保留扩展字段和新增必需键 |
| FL12 | patroni__patroni-3112 | medium | live:issue:patroni/patroni#3111 | 主节点不能作为同步standby，大小写无关的成员过滤与节点离开同步状态的执行时序共同保持 |
| FL13 | pvlib__pvlib-python-2292 | medium | live:issue:pvlib/pvlib-python#1171 | 雪深阈值、小时降雪率、时间序列索引与累计滑落状态共同约束；无snow_depth仍保持既有合同 |
| FL14 | jupyterlab__jupyter-ai-962 | medium | live:issue:jupyterlab/jupyter-ai#372 | line/cell magic默认模型、显式参数优先、帮助分支、shell命名空间与错误处理统一；资格必须证明mock provider下离线完整通过 |
| FL15 | reflex-dev__reflex-4230 | medium | live:issue:reflex-dev/reflex#4227 | 持久化状态与当前类子状态树合并，保持父指针与新引入子状态可用 |
| FL16 | patroni__patroni-3310 | medium | live:issue:patroni/patroni#3307 | 手动候选过滤、排除候选开关与quorum投票条件联动，保留自动故障转移限制 |
| FL17 | falconry__falcon-2450 | medium | live:issue:falconry/falcon#2243 | WSGI/ASGI静态ETag/IfNoneMatch优先级、日期和文件清理联动 |
| FL18 | python-babel__babel-1163 | medium | live:issue:python-babel/babel#1162 | direction/past/future和format长度变体、locale回退联动 |
| FL19 | joke2k__faker-2099 | medium | live:issue:joke2k/faker#2097 | 日期生成的多个公开入口新增可选格式化，并保持既有date返回、时区、年龄和日期范围默认语义；嵌套调用只在请求格式时转换。 |
| FL20 | jupyterlab__jupyter-ai-943 | medium | live:issue:jupyterlab/jupyter-ai#616 | 清空聊天必须同步可见消息、共享LLM有界历史和异步流回复；包装历史按请求时间阻止清空前的在途回复重新写入，handler配置和前端pending状态联动。 |
| FL21 | pylint-dev__pylint-10275 | hard | live:issue:pylint-dev/pylint#9391 | 跨模块类定义、内层函数返回注解与类方法作用域/定义时机需要共同判定 |
| FL22 | pylint-dev__pylint-9785 | hard | live:issue:pylint-dev/pylint#9672 | 动态条件定义类的多结果推断、构造器签名不确定性、继承/方法代理与关键字调用校验跨共享推断和调用检查器联动 |
| FL23 | encode__starlette-2697 | hard | live:issue:encode/starlette#950 | HTTP单/多区间解析、后缀与越界、If-Range协商、HEAD语义、分块multipart长度及后台清理共同约束异步文件响应 |
| FL24 | reflex-dev__reflex-3842 | hard | live:issue:reflex-dev/reflex#3595 | 动态路由参数在类初始化后注册，继承层的依赖索引、缓存失效、脏子状态传播与热重载重建同时成立 |
