# P0-2 Code Review：Experiment Identity & Evidence Consistency

日期：2026-10-28。范围依据 IMPROVEMENT_PLAN.md 的 P0-2 章节；本轮没有重新通读历史审计或重做 P0-1。Before 指本轮开始时的实际工作区，不是 HEAD（HEAD 尚不包含 P0-1）。

## 1. 解决的问题和方案

原实现存在两个独立的可变状态来源：浏览器只保存 exp_NNN 编号，schedule/进度在刷新后重建；每个 Tool 又自行查询 latest 并重新分析。一次建议因此没有稳定的实验身份或证据版本。

本轮采用现有 SQLite、原生 JavaScript 和显式 Tool Calling，不引入依赖或新框架：

1. 后端新增 experiments 表，为新实验生成 UUID，持久化协议和随机 schedule。
2. Session 提交在同一写事务内核对实验、协议和下一条件；进度从已提交 Session 推导。
3. 浏览器恢复服务器状态，sessionStorage 只保存本标签页选中的 experiment ID；不保存进度或证据缓存。
4. plan 请求必须指定 experiment_id。在一次 SQLite 读事务内获取协议、进度和固定 Session 集合，然后只计算一次 analysis snapshot。
5. 三个原 Tool 接收同一不可变 snapshot；不再调用 latest。每个 Tool 和最终响应都包含同一 evidence metadata。
6. 浏览器重新请求建议时重新取证；返回前重新核对 Session 集合。已变化的证据、过期请求或错误实验的答案不显示。

确定性指标、聚合、finding 算法不变；Tool schema、白名单分派、assistant/tool 消息回填、Qwen 配置及六轮循环机制保留。加入的 system 消息仅说明当前请求证据范围，不把 raw events 发给模型。

## 2. 实际修改的文件及归属

### 本轮修改

- `backend/session_store.py`：新增 experiments 表与实验状态读写；写入前协议检查；并发创建/保存串行化；一致性读事务。继续使用既有 sessions 表和 raw JSON。
- `backend/main.py`：新增实验创建/恢复/读取接口；PlanRequest 增加 experiment_id，PlanResponse 增加 evidence；对不存在/无证据/历史协议缺失/条件冲突返回明确的 404/409。
- `analysis/analysis_runtime.py`：新增不可变 AnalysisSnapshot 与 create_analysis_snapshot；保留旧的显式分析辅助入口。
- `analysis/tools.py`：三个原工具显式接收调用者 snapshot，返回 evidence metadata；不新增 Tool，不更改原指标。
- `agent/experiment_planner.py`：在模型循环前创建一次 snapshot，并通过 Dispatcher 传给每个 Tool；结果附 evidence；CLI 必须显式提供 experiment_id。
- `frontend/trainer.js`：恢复所选实验、按确认进度训练、提交协议字段、保存成功后同步、跨标签页失效通知、推荐绑定和过期结果检查。使用现有 DOM/状态区域，未修改 HTML/CSS 布局。
- `tests/test_session_contract.py`：只为已有合成 fixture 补协议/条件字段，并在临时库注册匹配的测试协议；保持原 15 个 P0-1 测试与断言，未使用真实数据库。

### 本轮新增

- `tests/test_experiment_consistency.py`：12 个新 Python 测试方法，覆盖实验、事务和真实 Agent loop 的离线一致性验证。
- `tests/test_experiment_frontend.cjs`：11 个 Node 原生测试，执行实际 trainer.js，模拟 DOM、存储及 HTTP 服务。
- `docs/engineering-review/P0_2_CODE_REVIEW.md`：本报告。

### 之前已有，不能算作本轮成果

`analysis/session_validator.py`、`analysis/analysis_service.py` 的当前 Git diff 来自 P0-1，本轮没有继续修改。backend/main.py 和 session_store.py 的 diff 同时包含 P0-1/P0-2；tests/test_session_contract.py 也是 P0-1 新文件，本轮仅做上述 fixture 适配。

BASELINE.md、DECISIONS.md、IMPROVEMENT_PLAN.md、SESSION_CONTRACT.md、P0_1_CODE_REVIEW.md 在本轮开始前已经存在且未提交，本轮未修改。由于没有阶段 commit，不能把整个 `git diff HEAD` 或全部 untracked 文件当作 P0-2 的差异。

## 3. 关键 Before / After

### 3.1 实验身份与 schedule

Before：编号在 localStorage，schedule 和进度在内存；刷新可以继续沿用相同编号，却重新随机排列并从第一条件开始。

```javascript
const experimentSchedule = createExperimentSchedule();
let currentExperimentIndex = 0;
// experimentId came from the browser's exp_NNN counter.
```

After：后端创建一次并保存；并发标签页复用同一个 active experiment。

```python
connection.execute("BEGIN IMMEDIATE")
row = connection.execute(
    "SELECT * FROM experiments WHERE active_slot=1"
).fetchone()
if row is None:
    experiment_id = str(uuid4())
    # Persist protocol and shuffled schedule once.
```

experiments 表含 experiment_id 主键、protocol JSON、schedule JSON、active_slot UNIQUE 和 created_at。active_slot=1 表示唯一进行中的本地单用户实验；完成后置 NULL，保留历史实验及 Session。

### 3.2 恢复和确认进度

Before：先递增本地进度，再异步上传；保存失败也已经切换条件。

```javascript
currentExperimentIndex++;
await persistCompletedSession(completedSession);
```

After：浏览器等待确认，再从服务器恢复进度；失败只导出 raw JSON，不虚增完成数。

```javascript
await persistCompletedSession(completedSession);
await refreshExperiment();
```

恢复时 applyExperimentState 使用 completed_count 和已保存 schedule；sessionStorage 只记所选 ID。若别的标签页已经开始下一实验，本标签页刷新仍恢复自己选中的实验。已选 ID 不存在时明确报错，不默默换 ID。

### 3.3 防止两个标签页重复保存同一条件

Before：sessions 主键只防相同 sessionId，两个不同 UUID 的 Session 可以写入相同实验条件。

After：检查和 INSERT 共用 BEGIN IMMEDIATE 写事务。

```python
state = _experiment_state(connection, row)
require_matching_experiment(session, state)
connection.execute("INSERT INTO sessions ...", ...)
```

require_matching_experiment 核对下一 experimentOrder、sensitivity、repeat、schema/protocol version、targetCount、radius、训练尺寸和 inputMode。第二个过期提交返回 409，不写第二份相同条件；原始失败 payload 仍尝试 JSON 导出。

### 3.4 Tool 不再各自读取 latest

Before：每个 Tool 都可能读到不同实验或新提交的数据。

```python
def _get_current_analysis_result():
    return analyze_latest_experiment()
```

After：一次推荐只生成一次快照，Dispatcher 显式传递。

```python
analysis_snapshot = create_analysis_snapshot(experiment_id)
tool_result = execute_tool(
    tool_name=tool_name,
    arguments=arguments,
    snapshot=analysis_snapshot,
)
```

```python
def get_analysis_summary(*, snapshot: AnalysisSnapshot) -> dict:
    result = snapshot.result()
    return {"evidence": snapshot.metadata(), ...}
```

SQLite 读事务先固定协议、已保存进度和 Session 集合，读取完成即释放连接，不持有读锁等待模型。后续上传不能修改已创建的快照；下一请求重新读取并计算，不复用进程级缓存。

### 3.5 快照不可变及版本标识

Before：没有 snapshot ID，也没有对一次推荐固定 Session ID 集合。

After：frozen dataclass 只持有 JSON 字符串，方法每次解析出新对象，避免调用者修改一份 Tool result 污染其他工具。

```python
@dataclass(frozen=True)
class AnalysisSnapshot:
    result_json: str
    metadata_json: str

    def result(self) -> dict:
        return json.loads(self.result_json)
```

snapshot_id 是 protocol、schedule、raw sessions 和确定性 analysis 的规范化 JSON 的 SHA-256；相同证据重复请求可得到相同 ID，但对象和读取过程仍是新的。数据集合、内容或分析结果改变会改变 ID。哈希不是客户端真实性证明，也不是持久化的完整审计日志。

### 3.6 浏览器拒绝过期结果

Before：请求返回后直接显示 answer，没有证据版本核对。

After：请求包含 experiment_id；请求返回后再次读取该实验的已保存 Session ID 集合，并核对请求 revision、当前选择和响应 identity。

```javascript
if (requestVersion !== planRequestVersion ||
    data.evidence.experiment_id !== requestedExperimentId ||
    activeExperiment.experiment_id !== requestedExperimentId ||
    JSON.stringify(latestState.completed_session_ids) !==
        JSON.stringify(data.evidence.session_ids)) {
    // Hide stale evidence; never restore a previously selected experiment.
    ...
    return;
}
```

失效时提示重新生成，不把旧结果自动替换成新的结论；成功时显示来源实验、样本数、snapshot ID 前缀和 incomplete 状态。storage 事件和 focus 时同步可使其他标签页的旧结果失效。

## 4. 关键变量、条件与数据流

- `activeExperiment`：本标签页从服务器取得的身份、协议、schedule 和确认进度。sessionStorage 中只有 selection hint，数据库才是状态来源。
- `active_slot`：数据库内唯一的 active 实验标记，配合写事务防止双标签页各自创建新的进行中实验。
- `completed_count`：由已提交 Session 行计算，不作为独立可变计数存储；还检查 experimentOrder 连续性，不能将手工破坏的顺序伪装成正常进度。
- `sessionSaving` / `experimentLoading` / `planBusy`：阻止在保存未确认、状态恢复或推荐期间开始新的本地采集，避免并行状态交错。
- `analysis_snapshot`：从指定 experiment_id 读取一次、计算一次，仅属于当前 planner 请求，通过 Python 参数传给 Dispatcher/Tools，没有全局共享上下文。
- `evidence`：experiment_id、snapshot_id、session_ids、session_count、attempt_count、expected_session_count、incomplete、protocol_version、protocol 和实际上报 input_modes。模型和客户端均可识别证据范围。
- `planRequestVersion`：当前页面的异步结果 revision。数据集合或选择改变即递增；旧请求不能覆盖新选择。
- `aimcoachEvidenceRevision`：localStorage 中的跨标签页通知 token，不含 Session/分析缓存。通知失败不否定数据库已经确认的保存；它不是真实状态来源。

主要条件的设计意图：

1. 实验不存在 → 404；实验存在但没有 Session → 409，明确 incomplete，不调用模型。
2. 缺少持久协议的 legacy 实验 → 409，报告协议未验证；不把旧数据自动注册或重分组。
3. 已保存部分 Session → 允许推荐，但 evidence.incomplete=true；采样不足仍需谨慎解释，不伪装成完整实验。
4. 提交条件或协议与服务器不符 → 409；调用者须重新同步，不能靠自己的内存 index 推进。
5. 同一实验窗口尺寸不符 → 前端拒绝开始、服务端拒绝写入；采集中 resize → 中止当前未完成 Session，保持既有中止语义，不把不同坐标系统混在一份数据里。
6. 已选身份不存在 → 报错，不静默选择另一个实验。sessionStorage 不可用时退回服务器 current，但不从浏览器恢复旧证据。

新增/实质修改的关键逻辑均附简单英文 What/Why 注释；未为了注释而重写无关的采集、指标或 Agent 循环代码。

## 5. 必须说明的行为变化

- 新上传必须引用后端注册的 managed experiment，并匹配协议；任意客户端 exp_NNN 不再作为新实验的写入入口。历史记录仍可通过既有 Session 读取函数访问。
- 新实验仍为三档×三重复，每 Session 3 个目标、radius=25，保留本轮开始时的 Demo 行为；不会把历史 20 目标实验当作新协议数据。
- 新协议固定本实验创建时的训练区尺寸。这项约束用于防止同一实验混合坐标系统，允许改变尺寸后继续采集的旧行为不再适用。实际输入模式保留“优先请求 unadjusted、失败 fallback”策略；`unadjusted_requested` 只表示请求过该模式，不声称平台已证明原始输入真实性。
- 刷新不再自动创建下一实验；完成实验保持选中以便规划。用户点击训练区明确开始下一轮时才创建/恢复 active 实验，然后再次点击进入 Pointer Lock，避免异步请求消耗浏览器 user activation。
- Python Tool 调用现在必须传 snapshot，planner/CLI 必须传 experiment_id；模型看到的 Tool 参数 schema 未变，模型不能切换实验。
- POST /agent/plan 旧的仅 goal 请求现在返回 422；新契约为 `{goal, experiment_id}`，响应原字段之外增加 evidence。

接口：POST /experiments（创建/复用 active）、GET /experiments/current（首次选择）、GET /experiments/{id}（恢复明确实验），原 /sessions 与 /agent/plan 保留路径。

## 6. Verification

实际执行：

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B -m unittest discover -s tests -v
node --test tests/test_experiment_frontend.cjs
node --check frontend/trainer.js
git -c safe.directory=D:/Projects/AimCoach diff --check
Get-FileHash data/aimcoach.db -Algorithm SHA256
```

结果：Python **27/27 方法通过**（12 个 P0-2 + 15 个 P0-1，包含 subTest）；Node **11/11 通过**；JS syntax 和 diff whitespace 检查通过。Python 最终运行约 9.960 秒，Node 最终运行约 258.76ms，不作为性能 benchmark。

### 新 Python 测试逐项覆盖

1. identity_schedule_and_confirmed_progress_survive_reload：UUID、九个唯一条件、持久 schedule、确认进度、重初始化不重置状态。
2. concurrent_tabs_create_only_one_active_experiment：两个真实线程/独立 SQLite 连接并发创建，只返回一个实验。
3. concurrent_tabs_cannot_commit_same_condition_twice：两个不同 Session UUID 竞争同一条件，仅一份成功、一份 conflict。
4. protocol_mismatch_and_unregistered_identity_are_rejected：尺寸、版本、条件、schema、inputMode、radius/count 和未注册身份不匹配均不写入。
5. completion_is_restored_until_explicit_new_experiment：九轮完成后可恢复；新实验拥有新 ID，旧实验仍可独立读取，不能继续追加条件。
6. legacy_rows_are_not_migrated_or_used_as_verified_evidence：临时库中的旧记录未改写/注册；快照拒绝，仍可创建新的 managed 实验。
7. snapshot_is_immutable_and_new_requests_see_new_data：相同证据 hash 稳定、返回副本修改不污染快照、后续保存产生新的 hash/样本数、禁止依赖 latest。
8. read_transaction_keeps_progress_and_session_rows_together：在两次读取之间启动并发写入，读事务的进度和 raw rows 都保持原集合；结束后新请求看到新增 Session。
9. corrupt_identity_or_protocol_is_not_relabelled_as_valid_evidence：临时库手工改坏内部身份/协议后，不能贴上原实验的快照标签。
10. single_recommendation_keeps_snapshot_during_upload：真实 planner loop + fake SDK，在 summary 后插入新 Session，再调用 compare/profile，三个 Tool 的 evidence 全部一致；snapshot 仅创建一次，后续请求看到新证据。
11. parallel_recommendations_do_not_share_evidence：两个并行真实 planner 请求分析不同实验，各自 sample count、identity 和 incomplete 独立。
12. api_explicit_scope_status_and_resume：真实 FastAPI ASGI 路由的创建、恢复、保存、显式实验参数、缺参 422、缺实验 404、空/legacy/stale 409、部分样本推荐与 evidence。

### Node 测试逐项覆盖

1. 刷新恢复同 ID、schedule、已保存进度。
2. 两个页面复用身份，在开始训练前重新读取另一个页面的提交。
3. 挂起/失败保存均不推进本地条件；刷新也不虚增。
4. 已完成实验刷新保持选中，明确开始才创建新实验。
5. 推荐发送明确 ID；模型请求期间 Session 集合变化时拒绝旧答案；重发取得新证据。
6. 跨标签页通知令结果失效；focus 后读取新进度。
7. 窗口不匹配不能开始，采集中 resize 中止且不推进。
8. 旧异步回答不能恢复已离开的实验身份。
9. localStorage 通知失败不把已确认保存误报为失败。
10. 另一标签页创建新实验后，本标签页刷新仍恢复自己的旧选择。
11. 已保存的 selection ID 不存在时明确报错，不静默创建替代身份。

测试边界：Python 所有写入都在 TemporaryDirectory 的数据库；保留的 19 份 JSON/黄金输出仅只读检查。Node 执行实际 trainer.js，但 DOM、storage 和服务端是模拟对象。真实 SQLite 并发由 Python 测试覆盖，真实 Tool Calling 消息循环由 fake SDK 驱动的 planner 覆盖。

未运行：真实 Qwen 请求、真实浏览器 Pointer Lock / fallback / resize 的人工端到端演练、真实网络故障/多进程负载、全仓 pytest、依赖作者本地 DB 的旧 smoke 脚本、部署与备份恢复。旧 tools smoke 仍需显式 snapshot 适配并去除真实 DB 假设，不能据此宣称全仓测试已绿。

真实 DB 未用作测试，前后 SHA-256 均为 `1c4a46be675fba110ee79e66aa3a01fd6fc7276401ca1968b153366ebffd1dbb`。本轮未启动真实服务，因此没有对真实数据库执行新表初始化。首次实际启动会新增 experiments 表，不修改旧 sessions；在将代码切换到真实库前应先对库做备份和恢复校验，本轮不声称已完成现场迁移或恢复验收。

## 7. 未解决的问题与风险

- 双标签页可以同时实际训练同一条件，但数据库只接受第一份合法提交；本轮没有引入训练租约/占用系统。第二份原始数据会尝试导出，不会成为重复条件的分析证据。
- 未完成的 Session 仍沿用 Esc 中止语义，不恢复到某个 Attempt。没有持久 pending 上传队列；保存请求挂起时等待、超时/重试/取消仍属 P0-5。
- 窗口固定策略可能让改变显示环境后的未完成实验难以继续。本轮没有猜测放弃/克隆实验的产品规则；需恢复尺寸，或后续明确设计结束/放弃功能，不能覆盖已有 schedule。
- sessionStorage 不可用时同标签页选择恢复降级到服务器 current；旧选中 ID 被删除/更换数据库时会停在明确错误，需要维护者或后续恢复 UI 处理，不自动换身份。
- 跨标签页通知不是强一致的推送机制；通过其他程序写入时，已显示建议会保持为带 snapshot 标签的历史建议，直到 focus/同步/重新请求。返回前的 Session ID 核对依赖正常应用的 append-only 行为，不能发现有人绕过应用原地改 raw_data 且保持 ID 的所有情况。
- protocol 明确允许 unadjusted 请求及 fallback，因此两个模式可在同一协议内出现；evidence 提供 input_modes，但 DPI、平台输入真实性和所有环境因素仍不能完全控制。
- 快照不跨请求缓存、不长期存储完整 payload/trace；相同 hash 不代表同一次运行，也不证明模型充分使用了证据。无 Tool 的直接模型答案仍可能出现，强制取证与输出验证属于 P0-4。
- API 和实验 active_slot 保持本地单用户边界，没有多用户隔离或授权。snapshot 读取结束后即释放事务，不持有锁等待网络模型；大数据集的 JSON/hash 开销未 benchmark。
- 没有自动迁移历史协议、不更改历史 ID，不会让现有 exp_001 突然被当作合格新实验。Legacy 数据若要重新纳入推荐，需要单独设计有来源记录的导入/适配流程。

## 8. Review Guide（建议阅读顺序）

1. [session_store.py:135](D:/Projects/AimCoach/backend/session_store.py:135) — get_or_create_experiment；接着读 :191 require_matching_experiment 和 :241 save_session。重点看唯一 active 身份、随机协议一次生成，以及条件检查和 INSERT 是否位于同一写事务。
2. [trainer.js:49](D:/Projects/AimCoach/frontend/trainer.js:49) — applyExperimentState / refreshExperiment（:80）；再读 :564 completeSession。理解 selection hint 与服务器进度的区别，以及失败为何不推进。
3. [analysis_runtime.py:30](D:/Projects/AimCoach/analysis/analysis_runtime.py:30) — create_analysis_snapshot；对照 session_store.py:215 load_experiment_evidence。重点看 SQLite 读事务、数据集合、协议验证、不可变副本与 hash 输入。
4. [experiment_planner.py:259](D:/Projects/AimCoach/agent/experiment_planner.py:259) — run_experiment_planner；对照 :229 execute_tool 和 tools.py:35。确认只创建一次 snapshot，模型不能自己切换实验，三个工具不再访问 latest。
5. [trainer.js:946](D:/Projects/AimCoach/frontend/trainer.js:946) — generateExperimentPlan；特别看 :1025 的过期判断。配合 tests/test_experiment_consistency.py:247 的上传交错测试和 tests/test_experiment_frontend.cjs 的旧响应测试，审查前后端的一致性承诺。

## 9. 查看差异

```powershell
Set-Location D:/Projects/AimCoach
git -c safe.directory=D:/Projects/AimCoach status --short --untracked-files=all
git -c safe.directory=D:/Projects/AimCoach diff -- backend/session_store.py backend/main.py analysis/analysis_runtime.py analysis/tools.py agent/experiment_planner.py frontend/trainer.js
Get-Content tests/test_experiment_consistency.py
Get-Content tests/test_experiment_frontend.cjs
Get-Content tests/test_session_contract.py
git -c safe.directory=D:/Projects/AimCoach diff --check
```

注意上述 backend 两个文件的 diff 还含 P0-1，不能直接作 P0-2 独立补丁；新增测试/报告为 untracked，不会出现在普通 diff 中。任务开始前已存在的未提交内容见第 2 节。未自动 add、commit、push 或重写历史。
