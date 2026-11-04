# Remaining Engineering Issues Completion

日期：2026-10-28。范围：本地、单用户 Demo 的工程可靠性，不是商业系统或公网生产就绪评估。

结论：本轮可实施的剩余缺陷已修复并通过相应离线/本地 HTTP 验证，可以进入统一人工验收。没有改变四项指标定义、finding 算法、Tool Calling 流程或训练条件；未修改真实数据库，未 commit/push。

`ENGINEERING IMPLEMENTATION COMPLETE — READY FOR USER ACCEPTANCE`

此标记指实现完成、等待人工验收，不代表真实 Qwen 回答、物理鼠标、辅助技术或公网部署已验证。

## 1. 原始问题与最终状态

原计划有 **13 个顶层编号：P0 5、P1 5、P2 3**。用户本轮确认 P0 完成，因此不重复实现 P0；仍执行统一回归。按本轮可实施范围核销，8 项 DONE、5 项 DEFERRED，0 BLOCKED、0 FAILED。部分 DONE 项的新增功能/真实模型评估子项明确延期，不计作已经实现。

- **P0-1 DONE**：共享上传/存储/分析契约及历史兼容，既有代码，回归通过。
- **P0-2 DONE**：服务端实验身份、协议、恢复及请求快照，既有代码，回归通过。
- **P0-3 DONE**：离线 fixture、来源 hash、黄金结果、隔离测试、CI 配置，既有代码；本轮补充干净环境验证。远程 CI 未执行。
- **P0-4 DONE**：参数/结果、证据门槛、预算和安全失败，既有代码；本轮补齐客户端关闭及结果额外断言拒绝。真实模型没有运行。
- **P0-5 DONE（用户确认阶段完成）**：既有保存队列、无 Key 启动、取消、超时、恢复。上轮内置浏览器 Pointer Lock 受限的历史记录保留，不把本轮 mock 宣称为物理采集实测。
- **P1-1 DONE（可靠性及离线评估部分）**：trace/快照基础已有；补充模型名、prompt/tools 内容 hash、逐次模型耗时和 token accounting；严格工具结果集合；补充引用/观察/建议/未知的 prompt 约束和人工 rubric。真实模型重复评估尚未运行；专门前端证据展示、自动语义裁判为 DEFERRED 扩展。自然语言事实正确性没有被代码保证。
- **P1-2 DEFERRED**：结构化计划预览、接受、执行是新增业务功能，本轮明确排除。
- **P1-3 DEFERRED（剩余性能增量）**：请求内只计算一次快照、显式关闭 SQLite 连接已有；没有重复修改。Attempt 聚合复用、索引、WAL、rAF 均无当前瓶颈证据，按用户要求延期，不为展示重构。
- **P1-4 DONE**：最小 runtime/optional Agent 依赖、实测 lock、干净 Python 3.12 安装、环境配置、只读 health、隔离备份恢复、启动目录修复、本地 HTTP 重启验证和操作说明。容器/公网为 DEFERRED，不是本地启动必需品。
- **P1-5 DONE（功能部分）**：既有 raw/fallback、固定训练区/resize 中止、样本量及完成度保留；新增主按钮限制、可获取的 DPR/viewport scale、目标 label 和状态 live region。未知值为 null；不推断物理 DPI，不增加未经确认的跨设备比较规则。视觉优化延期，真实辅助技术待人工验收。
- **P2-1 DEFERRED**：新统计方法、多玩家/设备实验是功能扩展。
- **P2-2 DEFERRED**：历史列表/回放/派生报告是新功能；原有 pending 原始数据导出保留。
- **P2-3 DEFERRED**：公网、多用户鉴权、TLS、全局流控/成本体系属于新部署能力；保持本地监听。备份恢复的本地必要部分已在 P1-4 完成。

额外确认的 6 组缺陷（是原领域下的补充证据，不把原始总数改成 19）：

1. **DONE**：同一 Session 重复传给 Analysis 会重复计数；原入口仅检查单条完整性及 experimentId。
2. **DONE**：有限坐标相减/累加路径、有限指标聚合仍可能产生 inf/NaN；原代码可算出错误零效率或在 JSON 序列化阶段失败。
3. **DONE**：每请求创建 OpenAI HTTP 客户端而没有显式关闭，失败路径也未释放。
4. **DONE**：summary/compare 只核对已知字段，夹带未经 snapshot 支持的额外断言仍会传给模型。
5. **DONE**：规划输入只检查字符串长度，空白文本可触发模型链路；默认额外字段无提示忽略。
6. **DONE**：读取库锁/损坏及 Session 身份不匹配缺少稳定受控响应，可能返回通用 500 或错误原始结构；现为安全 503/409。

## 2. Change Summary 与范围边界

以下是本轮实际修改文件；仓库在开始时已有大量 P0 未提交改动，不能把 HEAD 到当前的全部 diff 算成本轮成果。

核心代码：

- `agent/experiment_planner.py`：配置、内容版本标识、模型耗时/accounting、严格完整工具结果、存储错误分类、客户端 try/finally；保留原循环与预算。
- `analysis/analysis_service.py`：拒绝重复 Session；计算和聚合输出必须有限；公式不变。
- `backend/main.py`：strict/非空白规划请求、health、共享存储异常响应；保存错误交给同一 handler。
- `backend/session_store.py`：只读 readiness；读取原始 Session 时验证对象与身份，不修改/迁移数据。
- `frontend/trainer.js`：主按钮规则；可选 `clientEnvironment`（DPR/visualViewportScale），没有新指标或协议门槛。
- `frontend/index.html`：输入 label、状态 live region、资源版本更新；无视觉重设计。
- `run.ps1`：用脚本根目录启动并在退出后恢复目录。

依赖、测试与脚本：

- 新增 `requirements-runtime.txt`、`requirements-agent.txt`、`requirements-lock.txt`；`requirements-test.txt` 引用统一 constraints。原完整 `requirements.txt` 原样保留。
- 新增 `scripts/backup_database.py`：只读源、SQLite backup API、新目标独占创建、integrity_check、失败只清理本次新文件。
- 新增 `scripts/check_local_startup.py`：无 Key、临时合成 DB 的真实 HTTP 启动/重启检查，finally 停止子进程。
- 新增 `tests/test_engineering_completion.py`：8 个回归方法，覆盖下文行为。
- `tests/test_experiment_frontend.cjs`：新增 2 项按钮/环境用例；已有生成主按钮的 fixture 现在显式传 `button: 0`。
- `tests/test_session_contract.py`、`tests/test_experiment_consistency.py`：仅补齐 API fake planner 的 `model_settings` 接口，原断言标准不变。

文档：修改 `README.md`（最小安装、现实限制）、`IMPROVEMENT_PLAN.md`（状态核销）；新增本报告、`LOCAL_OPERATIONS.md`、`AGENT_EVALUATION.md`、`USER_ACCEPTANCE_CHECKLIST.md`。最终自动测试日志另存 artifacts/engineering-offline-checks.log。

本轮没有修改前端 CSS、Session validator、Analysis Tools 实现、历史 fixture/输出、原 P0 报告或真实数据。相关文件仍显示为 Git modified/untracked 是之前已有的工作。

## 3. Before / After 与 Engineering Reasoning

### 3.1 Analysis 样本身份和数值边界

Before：逐条合法、同 experimentId 的列表直接聚合；`[session, session]` 也计为两个 Session。

```python
for session in sessions:
    validate_session_integrity(session)
# No dataset-level session identity check before aggregation.
```

After（精简）：

```python
session_ids = set()
if session["sessionId"] in session_ids:
    problems.append("duplicate sessionId")
session_ids.add(session["sessionId"])
```

`session_ids` 表示本批次已经计入的观察身份，防止调用者把同一证据重复计入样本；SQLite 主键只能保护库内写入，不能保护直接 Analysis 调用。

Before：`path_efficiency = direct_distance / path_length`，没有检查计算溢出；例如路径长度 inf 时可能得到看似合法的 0。

After：原公式后检查时间、误差、直线距离、路径和效率均为有限值；统计聚合也检查 mean/median/std，OverflowError 转 ValueError。不钳制数值、不编造合理上限、不更改合法结果。Runtime 既有入口将 ValueError 映射为无效证据冲突。历史 9/180 黄金结果不变。

### 3.2 工具结果完整性

Before：

```python
valid = all(same(result.get(key), source[key]) for key in known_keys)
```

After：

```python
expected = {key: source[key] for key in known_keys}
valid = same(result, {**expected, "evidence": snapshot.metadata()})
```

summary 必须整体相等，compare 必须键集合和内容均相等；profile 原本已整体比较。否则额外的 `best_sensitivity: 999` 即使没有改变已核对字段，也可能污染模型证据。此修复防止工具层夹带断言，不保证模型最终文本不会自行误述。

### 3.3 Agent 配置、来源与资源

Before：模型、endpoint 写死；每次 `get_model_client()` 创建资源后，无论 return 还是失败都没有显式 close。

After（精简）：

```python
model, endpoint = model_settings()  # Environment configuration, fixed for this run.
try:
    # Existing Tool Calling loop, immutable snapshot and budgets.
    ...
finally:
    close = getattr(client, "close", None)
    if callable(close):
        close()
```

实际代码隔离 cleanup 异常，避免覆盖原始安全失败或成功结果。endpoint 拒绝非法 URL、内嵌账号、query/fragment；默认仍为原 DashScope，不增加服务。新增 `model_runs` 是每次模型调用耗时/accounting 列表；`usage` 中 model、prompt/tools SHA256 说明本次配置和内容版本，不记录 Key、raw 数据或完整模型输出。Tool trace 仍携带原 snapshot_id、参数、成功状态和耗时。缺供应商 token 时保留原有保守预算估计，明确区别实报 token。

Prompt 补充观察/建议/未知、metric/sensitivity/experiment/snapshot 引用，以及 pooled 不等于跨 Session 的要求。这是行为约束，不是自动语义证明；人工 rubric 专门验证这项边界。

### 3.4 API 与存储错误

Before：goal 仅 min_length=1；空白合法。读取存储的 SQL/JSON 异常没有共享安全响应，原始 Session 的 JSON 对象身份未在读取时核对。

After：

```python
model_config = ConfigDict(strict=True, extra="forbid")
if not value.strip():
    raise ValueError("Value must not be blank.")
```

不自动修剪 experimentId 或替换身份。库锁/不可访问用 503（可重试），损坏 JSON/身份冲突用 409（保留证据待检查）；不返回 SQL、磁盘路径或供应商异常原文。Agent 快照读取的 sqlite 错误标记 storage_unavailable，不能误报为模型供应商故障。

`/health` 的 storage probe 使用 mode=ro，不创建失踪库；Agent 只报告 unavailable/configured_not_probed，绝不调用付费模型。health 不承诺写入空间、Key 有效性或供应商在线。

### 3.5 前端输入和环境来源

Before：Pointer Lock 下任何 mousedown 都记录为 shot。

After：

```javascript
// Only the primary button records shots; other buttons must not change evidence.
if (event.button !== 0) return;
```

这是必须改变的行为：右键/中键现在不算 HIT/MISS，避免污染训练条件。只影响新采集，不改历史数据。

新 Session 将浏览器可提供的 `devicePixelRatio` 与 `visualViewport.scale` 写入可选 clientEnvironment，未提供/无效用 null。服务端既有 extra 字段保存方式保留这些信息，原 Session 无字段仍合法；不冒充真实物理 DPI、不能单独拆分浏览器缩放与 OS 缩放，也不增加新的跨设备可比性规则。原训练区坐标和四指标完全不变。label/live region 是功能可访问性，不改 CSS。

### 3.6 发布依赖与恢复

Before：安装说明使用完整开发环境 freeze；run.ps1 从调用者目录导入；缺可复现备份恢复路径。

After：最小服务与可选 SDK 分层、共享实测 constraints，保留历史 freeze；Push-Location/try/finally 恢复调用目录。SQLite backup API 使用一致性快照，目标以 `xb` 创建，已存在则拒绝。恢复也是复制到新文件后显式切换配置，不覆盖旧库。必须测试恢复行内容，而不是假定文件字节布局一致。

## 4. Verification：实际运行

环境：Windows、Python **3.12.14**、Node **22.17.0**。所有写入指向 TemporaryDirectory 或独立临时 venv；默认不使用 API Key。

实际命令（PowerShell，`$cleanPython` 指本轮临时 venv 的 Scripts/python.exe）：

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B -m unittest discover -s tests -p test_engineering_completion.py -v
node --test tests/test_experiment_frontend.cjs
D:/Anaconda/envs/aimcoach/python.exe -B scripts/run_checks.py

D:/Anaconda/envs/aimcoach/python.exe -B -m venv "$env:TEMP/aimcoach-engineering-clean-20261028"
$cleanPython = "$env:TEMP/aimcoach-engineering-clean-20261028/Scripts/python.exe"
& $cleanPython -m pip install -r requirements-agent.txt --index-url https://pypi.org/simple --disable-pip-version-check --retries 0 --timeout 15
& $cleanPython -m pip install -r requirements-agent.txt --no-index --disable-pip-version-check
& $cleanPython -m pip check
& $cleanPython -B scripts/run_checks.py
& $cleanPython -B scripts/check_local_startup.py
git -c safe.directory=D:/Projects/AimCoach diff --check
Get-FileHash data/aimcoach.db -Algorithm SHA256
```

最终结果：

- **60 Python tests PASS**，最终统一运行 13.749 秒；包括历史黄金/来源保护、Session 契约、存储/API、实验身份与并发、Agent fake model、备份恢复、新边界。
- **27 Node tests PASS，0 FAIL**；25 个原 trainer/queue 模拟回归基础上加按钮及环境测试，实际为 trainer VM 23 + queue 4。不是真实浏览器鼠标 E2E。
- `run_checks.py` 内两个 frontend JS 的 `node --check` 通过。
- 干净环境安装及 `pip check` 通过；lock constraints 在已安装的干净环境用 --no-index 再验证通过。新解析依赖如 Starlette 1.10.0 与原环境不同，已用整套测试验证；旧环境也已通过一轮统一检查。
- **真实本地 HTTP smoke PASS**：临时合成库、页面 200、health 200、Session 新上传/读取/相同重试 200、冲突 409、输入 422、未知 Session 404、9 Session/27 Attempt 分析 200、缺 Key Agent 503；停止再启动，原实验 snapshot_id 相同。它不执行浏览器 JS 或物理鼠标操作。
- **备份集成回归 PASS**：两张表逐行/原始 JSON 相等；源 hash 不变；已提交 WAL 行纳入备份；拒绝覆盖、坏源失败清理。都使用临时库，不备份/恢复真实库。
- **真实训练库 SHA256 前后相同**：`1c4a46be675fba110ee79e66aa3a01fd6fc7276401ca1968b153366ebffd1dbb`。
- `git diff --check` 通过。没有 commit/push。

新 Python 8 个方法分别验证：重复 Session；路径溢出；统计溢出；备份/恢复/不覆盖；WAL/坏源；health/安全 API 错误/空白请求/损坏身份；配置/成功失败客户端关闭；工具额外结论/存储故障分类。现有 fake 的接口适配没有减少原断言。

过程中真实失败亦保留说明：首次统一回归因旧 fake 缺 model_settings 有 1 ERROR，适配后通过；首次隔离安装无可用包，获准联网安装后通过；首次 HTTP smoke 被 seed 的“未验证配置库”保护拒绝，修正测试先 seed 后配置 DB 的顺序，未削弱保护。未通过修改断言、清空数据制造成功。

**未运行/未宣称通过**：远程 GitHub CI、Linux/macOS 安装、真实 Qwen 付费请求和语义正确率、真实 Chrome/Edge Pointer Lock/鼠标/读屏、浏览器断网下载演练、公网负载/鉴权/TLS。人工步骤见 USER_ACCEPTANCE_CHECKLIST.md。

## 5. 兼容性、剩余风险与回归判断

已验证合法历史 JSON 与黄金结果不变，默认九轮实验条件、原 Tools、保存恢复和快照机制无自动测试可见回归。新 clientEnvironment 是可选附加字段，缺字段历史 Session 不拒绝。新采集右键/中键不再记射击是有意纠错。

直接 Analysis 传重复 Session 或数值溢出现在明确失败；这是数据边界收紧。PlanRequest 现在拒绝额外字段和空白字符串，外部调用者若依赖这些无效输入，需要修正请求。模型 endpoint 由部署者设置，只能指向可信服务；Key 仍属于私密配置。

仍未解决（明确保留的边界）：自然语言结论可能误述；自洽客户端事件不能证明真实命中；物理 DPI/疲劳/学习效应未知；取消等待不保证供应商立即终止；localStorage 满且页面被强制关闭可能丢失内存 pending；跨标签页冲突 pending 不自动删除，先导出再人工核对。现有项目不提供冲突数据导入/合并功能。

没有已知需要本轮继续修改的 BLOCKED/FAILED 实现项；上述真实环境验证是人工验收事项，扩展项是 DEFERRED，并非生产级安全保证。全局多请求费用限制与用户隔离需要在考虑公网前单独实施。

## 6. Review Guide（建议阅读顺序）

1. [Analysis dataset / 数值边界](D:/Projects/AimCoach/analysis/analysis_service.py:48)：先看 validate_analysis_dataset，再看 calculate_attempt_metrics:233 和 calculate_statistics:358，确认只加边界，没有改指标。
2. [工具证据与 Agent 生命周期](D:/Projects/AimCoach/agent/experiment_planner.py:311)：verify_tool_result → run_experiment_planner:340 → finally:515；model_settings:52；usage:450 附模型和版本来源。
3. [API 与 readiness](D:/Projects/AimCoach/backend/main.py:94)：共享 handler、health:111、PlanRequest:165；对应 storage_ready 在 backend/session_store.py:41，原始 Session 读取在 :353。
4. [原始采集](D:/Projects/AimCoach/frontend/trainer.js:389)：clientEnvironment 来源，再看 :852 主按钮规则；新 label/live region 在 frontend/index.html:71。
5. [非破坏恢复与回归](D:/Projects/AimCoach/scripts/backup_database.py:8)：先看新目标独占创建，再看 [隔离回归](D:/Projects/AimCoach/tests/test_engineering_completion.py:23) 和 [真实 HTTP 检查](D:/Projects/AimCoach/scripts/check_local_startup.py:16)。

## 7. Git Diff 与已有工作归属

开始时 README、planner、analysis、backend、frontend、run.ps1 等已有修改，docs/tests/scripts/CI 等也有历史未跟踪文件。本轮没有重置、暂存、提交或覆盖这些成果。原 P0 docs、validator、tools、CSS、legacy adapter、fixture、seed、CI/run_checks 等仍显示变化，**不属于本轮新实现**。

```powershell
git -c safe.directory=D:/Projects/AimCoach status --short --untracked-files=all
git -c safe.directory=D:/Projects/AimCoach diff --stat
git -c safe.directory=D:/Projects/AimCoach diff -- agent/experiment_planner.py analysis/analysis_service.py backend/main.py backend/session_store.py
git -c safe.directory=D:/Projects/AimCoach diff -- frontend/trainer.js frontend/index.html run.ps1 README.md
git -c safe.directory=D:/Projects/AimCoach diff --check
# 未跟踪新增文件不会出现在普通 git diff；例如：
git -c safe.directory=D:/Projects/AimCoach diff --no-index -- /dev/null scripts/backup_database.py
```

Windows PowerShell 原生 Git 可将最后一条的 `/dev/null` 替换为 `NUL`。`diff --no-index` 有内容时退出码 1 正常。HEAD diff 包含之前的 P0，不能作为本轮独立 patch；请结合本报告文件清单及 Before/After 审查。本轮开始时 8 个主要文件副本保留在本机临时 `aimcoach-review-53b45d54-1d0f-4027-9dbd-c226f9dea9d6` 目录，未加入仓库，不依赖它运行项目。

## 8. Completion Gate

1. **所有审计编号是否逐项核销？** 是，13 个顶层编号均有明确状态；不等于 13 项功能都实现。
2. **哪些真正修复并验证？** 本轮 Analysis 身份/数值、工具结果、客户端释放、配置、API 受控失败、前端按钮/环境信息、干净安装、本地 HTTP、备份恢复，以及既有 P0 自动回归。
3. **哪些未解决？** 本文 DEFERRED 的新功能/性能/公网能力；真实模型、物理交互和辅助技术的人工验证尚未执行，语义真实性和客户端真实性不作保证。
4. **能否进入 UI/UX 阶段？** 具备本地 Demo 的工程基础；先完成维护者统一验收，再开始 UI/UX。当前停止，不自动扩展功能或美化。
