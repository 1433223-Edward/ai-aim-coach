# 工程决策与证据账本

基线、事实分级、执行验证与数据指纹见 [BASELINE.md](BASELINE.md)。本文件区分“源码能证明的选择”和“文档记录的历史经过”。Git 仅一次初始提交，无法独立证明各决策的发生顺序或修复前后变化。

## D1：先保存 Raw Data，再做确定性分析

**已验证**：frontend/trainer.js 保存 movements、clicks、target、时间及 Session 信息；session_store.py 保存 raw_data；analysis_service.py 独立派生四个指标，没有模型计算统计的依赖。价值是可重算、可解释、可以比较算法版本。

**边界**：客户端记录是采集事实，不等于可信/完整事实；validator 可接受伪造 HIT 和非法时间。严格上传校验、采集模式和协议版本应补齐，原始记录不能就地“纠正”。

## D2：Pointer Lock + Virtual Crosshair + sensitivity 乘数

**已验证**：trainer.js 中有 unadjustedMovement 请求、普通 Pointer Lock fallback、raw delta 到虚拟坐标的乘法与 clamp。

**历史记录**：ENGINEERING_EVIDENCE.md §1 描述异常加速/跳变、输入层假设及修改后消失。没有原始录屏、事件对照日志或逐步提交；本轮不能独立确认修复因果关系。当前 fallback 未写入 Session，历史数据无法据此证明全部使用 raw input。浏览器 compatibility、DPI、缩放和 resize 影响仍待验证。

## D3：保留 Attempt / Session 边界，使用最终 HIT 位置定义路径效率

**已验证**：analysis_service.py calculate_path_length / calculate_attempt_metrics 使用最终 click 坐标，不是 target center；聚合保留每个 Session 的四指标和 miss rate，同时提供 pooled 统计。该定义与“进入目标圆即可命中”的任务一致，也保留了检查跨 Session 一致性的入口。

**边界**：path efficiency 只描述路径直接程度，不能自动改称整体稳定性；零移动返回 1 是定义选择，不是运动能力满分。随机目标距离、窗口变化可能影响可比性。当前只做描述性分析，不应包装成最佳灵敏度预测器。

## D4：SQLite 元数据列 + 完整 JSON

**已验证**：session_store.py 的参数化 INSERT/SELECT、主键防覆盖、experiment 查询以及 raw_data 文本；实际数据库 DDL 与代码一致。对于本地单用户 Demo，该设计减少不必要的细粒度表结构，同时保留未来查询入口；没有证据要求立刻迁移 PostgreSQL。

**边界**：.gitignore 排除了 DB，干净 clone 不含当前运行数据。没有 seed/import/backup 流程；现有 DB 的 exp_001 混有 3/5 次 Attempt 和不同尺寸，说明 experimentId 还不能保证协议一致。不能将本地 DB 当可重现测试 fixture。

## D5：纯计算 Service + Storage/Runtime 适配层

**已验证**：analysis_service.py 不读取 SQLite、文件系统或 LLM；analysis_runtime.py 负责载入；旧 build_analysis_result.py 保留“文件读取→计算→输出”的上一代实现。旧输出 schema=0.1；当前 Service=0.2，多了 experiment 身份要求。

**价值**：能在完全离线的条件下重算指标，便于后续黄金样例回归。

**漂移**：tools.py 注释仍说读取 analysis_result.json，实际调用 analyze_latest_experiment。工具并非只查询预先计算结果，会重复全量解析 raw JSON、计算指标。解释架构时应讲实际调用链，保留旧注释作为历史材料，未来通过正常代码变更修正，不能本轮伪造历史。

## D6：三个窄工具 + 显式 Agent Loop

**已验证**：experiment_planner.py 中 schema、execute_tool 的三分支白名单、assistant/tool 消息回填及六轮上限。模型只能请求现有工具，不直接执行 Python。可读性适合展示 Agent runtime 工程能力，不需要先引入框架。

**历史 Failure Case A**：证据文档 §8 记录仅开放 summary 时模型误认为缺少 Flick speed 数据，后开放 profile/comparison。当前源码确实有这两个工具，但没有完整原始 transcript 或修复前代码，因此“改动存在”与“历史因果已独立复现”必须分开。

**历史 Failure Case B**：文档记录模型将 pooled trend 说成更强的跨 Session 结论。当前 system prompt 包含“不要重解释指标”“尊重限制”“区分观察与建议”；这些是提示约束，不是输出验证。离线 mock 已证明 runtime 可以无工具返回建议。应保留失败样例并转为测试和评估用例，而非只增加 prompt。

## D7：随机重复条件，区分 Experiment 与 Session

**已验证**：当前前端生成三档×三重复的随机 schedule，Session UUID 独立，附 experimentOrder 与 sensitivityRepeat；本地计数器生成 exp_NNN。

**历史证据缺口**：正式 experiment_002 的 9/180 文件及三个 sensitivity 的重复数可查，旧输出可精确重算；但 raw JSON 没有 experimentId/order/repeat。文件名的 undefined 是当时元数据缺失的直接材料，不能重命名或倒填成确定的真实顺序。现有随机算法无法反向证明历史实验确实按随机顺序执行。

**合理推断**：刷新/多标签页/清理 localStorage/切换浏览器可能混合实验；应由后端创建身份、固定协议并保存进度。历史数据若导入，只能记录来源文件、原始 hash、导入规则和“未知”字段，不得编造原始元数据。

## D8：同源 FastAPI 静态托管与 JSON fallback

**已验证**：main.py 最后挂载 frontend；前端使用相对 URL；run.ps1 启动单进程本地服务。上传失败会尝试下载 JSON；这是保存完成数据的可用 fallback，不等于已有可靠离线同步。

**风险**：进度在上传确认前推进；等待中的保存与新规划可交错，网络挂起时 fallback 不会触发。Esc 会主动丢弃未完成采集。建议以可见保存状态和持久 pending queue 补足，保留已完成原始数据。

## 应长期保护的证据

- `data/experiment_000/001/002`：保留文件名、内容与目录，不就地补字段；记录来源和 hash 后再生成派生副本。
- `analysis/old_files/build_analysis_result.py` 与 `analysis/output/analysis_result.json`：保留可匹配的旧算法/旧输出，不运行旧 main 覆盖输出。允许在测试中调用纯函数重算。
- README.md、README.pdf、ENGINEERING_EVIDENCE.md：是原始陈述与历史记录；不能将其中所有 PASS 标记当作本轮验收。
- 本地 DB：与版本化 JSON 分开备份和审计；本轮 SHA-256 见 BASELINE。后续迁移前需保留只读副本和迁移验收，不能为让测试通过而清库。
- Git：保留唯一初始提交；未来改造用小而真实的提交/PR 建立可审查历史，不补写虚假的历史提交。
- 后续真实模型运行：保存脱敏的 goal、模型/提示/工具版本、参数、结果摘要、证据快照 ID、耗时、错误与最终文本；Key 不写日志。旧的缺失 transcript 应明确标“未保留”，不能用新运行冒充旧运行。

## 面试与 Demo 的诚实表达

可展示：亲自实现的浏览器输入采集、SQLite 持久化、纯确定性分析、三个证据工具、显式 tool-calling loop，以及真实数据和失败复现。重点解释工具边界为何影响模型判断、怎样识别证据漂移、怎样保证数据可重算。

不应宣称：生产用户规模、上线稳定性、显著改善训练效果、跨设备泛化、已完成自动推荐执行、所有测试通过或模型绝不幻觉。最有说服力的下一阶段材料是可离线复现的回归集、可查证的 runtime trace，以及一次明确标注条件的真实 Demo。
