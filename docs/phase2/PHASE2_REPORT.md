# Phase 2 — Training Scenario Library & Scoring

日期：2026-11-05。状态：实现完成，等待 Phase 2 人工验收。本轮无 commit / push，无付费模型请求，无 Agent Runtime / Tools 改动。

## 完成范围

六个场景均已实现并通过前端处理器、真实 HTTP / SQLite 集成验证及浏览器运行：

- Four Target Switching：四个固定位置，实心目标按顺时针轮换；空心圆标记其他位置。
- Six Target Precision：六个固定位置按行轮换；目标半径为同难度基础半径的 65%。
- Horizontal Tracking：水平匀速、边缘反射。
- Vertical Tracking：垂直匀速、边缘反射，提供方向一致率。
- Flick Target：每次命中后按记录的 seed / target index 产生新位置。
- Reactive Tracking：每两秒按 seed 改变速度和方向，积分轨迹连续，边缘反射；提供重新进入目标半径的恢复时间。

统一 registry 包含题目要求的身份、名称、分类、描述、难度、允许参数、评分版本及指标。客户端只能提交枚举配置，不支持代码、函数名或动态模块 dispatch。

新场景共用启动、暂停、提前结束、排队保存、服务端评分与反馈流程。使用同一个训练区域，不引入框架、3D、账号、排行榜或新 Agent。原 Flick 保留原 Pointer Lock、设置、分析与 Agent 流程。

## 配置与数据模型

`ScenarioConfig` 默认 normal / seed=1 / 时长20秒 / 目标20次 / 速度120px/s / positive。范围：easy-normal-hard；seed 0–2147483647；10–120秒；5–100目标；40–400px/s；positive-negative。基础半径 easy=32、normal=24、hard=16px。viewport 100–10000px，并要求最短边至少6倍半径。

新场景每个 Experiment 固定一个 Session，普通页面指针、倍率1；这是场景成绩协议，不替代原有多灵敏度实验。目标数仅用于点击任务，时长适用于两类任务，速度/方向仅影响移动任务。所有配置、seed、viewport、采样频率、评分版本及权重均进入服务器 Experiment 协议和 Session 快照，之后表单变化不能改变运行中的协议。

继续使用 Experiment → Session → Attempt → clicks 层次。点击任务每个目标一个 Attempt，MISS 留在该 Attempt 内；跟随任务一个连续 Attempt，新增 `tracking_samples[{time,x,y}]`。两类均使用 `schemaVersion=scenario-1`，保留开始/结束时间、身份与原始证据。跟随不存无限 mousemove，也不把一次点击冒充一个完整 Attempt。

数据库仅新增 `scenario_sessions`（身份、实验顺序、场景、完成状态、原始 JSON、服务器结果 JSON、保存时间），不改写原 `sessions` 原始内容。该分区避免旧 Agent 将跟随样本误当作旧瞄准指标；实验进度、结束生命周期和历史接口复用原有实现，历史列表汇总两类证据。

同一个 SQLite `BEGIN IMMEDIATE` 中读取协议/进度、校验证据、计算结果、查询同条件历史并写入。相同 ID + 相同内容幂等；同 ID 内容改变、跨分区 ID 冲突、错误快照/实验顺序/viewport 返回409。非法结构、几何或时间返回422。前端仅收到对应 Session ID 的保存 ACK 后清除队列和显示成功，失败可重试/导出，刷新保留同一报告。

新增 API：GET `/scenarios`；POST `/scenarios/experiments`；POST `/scenarios/sessions`；GET `/scenarios/sessions`。原 `/history/sessions`、`/history/sessions/{id}`、`/history/experiments` 支持新证据、场景/日期筛选及正确计数。新场景详情的 `score` 是完整评分对象，数值位于 `score.overall_score`。

迁移在下次后端启动时自动执行 `CREATE TABLE IF NOT EXISTS`，无 DROP / UPDATE 历史训练数据。本轮只迁移临时测试 DB；真实 `data/aimcoach.db` SHA256 前后均为 `BB3170EA182714A672F4896D2C7AD52426826AF04BD62C1C4F49B6AA324E1ED1`。上线使用前按现有备份脚本备份 DB。回滚时保留包含新证据的完整 DB 副本，再用匹配旧代码的阶段前备份启动；不要删除新表或混用旧代码读新实验。无需安装新依赖。

## 确定性评分 aim-score-1

权重及归一化常量保存到 `scoring_config` 快照，服务端拒绝未知版本或篡改权重。LLM 不参与任何成绩计算或修改。

令 clamp(x)=max(0,min(1,x))。

点击指标：

- Accuracy = HIT / 全部点击，0–1。点击几何与 hit 标志必须一致；距离等于半径算 HIT。
- Average Hit Time = 相邻成功命中间隔的平均值，首个从开始计时，单位ms；MISS/等待时间包含在间隔中。
- Targets per Second = HIT / Session有效秒数，单位次/秒。
- Stability = 1/(1+命中间隔总体标准差/max(平均间隔,1ms))，0–1。
- First Hit Rate = 首次点击即命中的目标数 / 命中目标数，0–1；Valid Hits 为有效命中数。
- Speed = clamp(min(800ms / 平均命中时间, TPS / 1.25))。
- 综合分 = 100 × clamp((0.45×Accuracy + 0.35×Speed + 0.20×Stability) × 完成比例 × clamp(Speed/0.25))，保留两位。

点击成绩必须完整完成配置中的目标数（至少5）、有效耗时大于0、每个命中间隔大于0。未完成、超时、无有效点击的综合分为0，仍保存可解释的指标。超慢操作同时降低速度和整体倍率，不能仅靠少点/慢点获得高分。

跟随指标：

- 每50ms时间槽最多一个真实观察样本；最多 duration×20+1 个样本，最长120秒。不补插值来伪造缺失输入。
- Coverage = 半径内样本数 / (配置秒数×20)，clamp到0–1；缺失槽仍占完整任务分母。
- Error = 每个采样点与服务器重建目标中心的欧氏距离平均值，单位px；归一化 Error Component = clamp(1−Error/(4×半径))。
- Stability = 1/(1+误差总体标准差/半径)，0–1。
- Direction Agreement = 指针位移与对应目标轴位移同向的相邻采样对比例；目标没有位移的对不计入分母。
- Reactive Recovery Time = 每个2秒规则变化后，首次观测到重新进入目标半径的时间，未恢复按2000ms，最后取平均。这是离散观察下的首次重获指标，不是持续锁定或真实人体反应时证明。
- 样本比例 = min(1,样本数/(配置秒数×20))。
- 综合分 = 100 × clamp(0.60×Coverage + (0.25×Error Component + 0.15×Stability)×样本比例)。

跟随必须运行完整时长并取得至少90%的预期样本，否则总分0，前端将不足样本运行归档为 incomplete。暂停不计入训练有效时间；尺寸改变或退出归档为未完成。非适用指标为 null，而不是伪装成0。优势/薄弱点取适用归一化指标的最大/最小项，由规则生成。

同条件历史对比只比较完整配置（包括难度、seed、时间、目标数、速度、方向、评分权重/版本）及 viewport 全部相同的已完成记录；显示此前次数与最高分。没有跨难度/跨版本排名。

## 修改文件与职责

- `analysis/scenarios.py`：受控 registry、配置、冻结快照、seed hash、确定性反射轨迹。
- `analysis/scoring.py`：证据校验、单位归一化、两类评分、确定性反馈。
- `backend/scenario_store.py`：快照/身份/生命周期、事务、幂等与历史同条件比较。
- `backend/session_store.py`：新增表、原实验协议可携带场景、合并进度与历史、跨证据分区 ID 保护，旧上传入口拒绝新场景协议。
- `backend/main.py`：registry / start / upload / history API 及输入/错误边界。
- `frontend/scenario_motion.js`：与 Python 同 seed、同轨迹实现，服务端始终独立重建。
- `frontend/scenario_ui.js`：六场景统一生命周期、受控采样、冻结 Attempt、待保存队列与结果展示。
- `frontend/trainer.js`：最小场景模式互斥与显式身份选择适配，不重写原控制器。
- `frontend/training_ui.js`：运行状态与按钮互斥，新证据禁用旧 Insights，避免混合指标。
- `frontend/app.js`、`frontend/index.html`、`frontend/style.css`：挂载场景库、配置输入和目标轮廓；无视觉重设计。
- `scripts/run_checks.py`：纳入场景前端测试；`scripts/seed_demo.py`：允许空新增表，但发现新训练数据即拒绝 reseed。
- `tests/test_phase2_scoring.py`：评分、非法输入、轨迹跨语言一致、SQLite/历史兼容。
- `tests/scenario_harness.cjs`、`tests/test_phase2_ui.cjs`：真实 ESM 处理器的可控 DOM/时钟测试。
- `scripts/check_phase2_persistence.cjs`：真实 HTTP、临时落盘 SQLite、六场景处理器、幂等及进程重启测试。
- `README.md`、本报告、同目录日志/浏览器证据：运行方式与验收证据。

## 关键 Before / After

原来仅旧 Flick + 原分析，缺少场景/评分协议：

```python
# Before: only the legacy Session path.
require_valid_session(session)
save_session(session)

# After: a separate evidence contract uses the existing Experiment lifecycle.
config = state["protocol"]["scenario"]
result = score(session, config)  # Server recomputes from clicks/samples.
connection.execute("INSERT INTO scenario_sessions (...) VALUES (...)", ...)
```

原进度只计旧 sessions；现在完成的新场景也计入，未完成记录不推进：

```sql
-- Before
SELECT session_id, experiment_order FROM sessions WHERE experiment_id=?
-- After
SELECT session_id, experiment_order FROM sessions WHERE experiment_id=?
UNION ALL
SELECT session_id, experiment_order FROM scenario_sessions
WHERE experiment_id=? AND status='complete'
ORDER BY experiment_order
```

浏览器验证发现新实验创建后不能继续刷新旧 selected ID，已改为显式接收服务端新身份：

```javascript
// Rejected intermediate implementation: refreshed the previously selected experiment.
await core.refreshTrainingState();
// Final: accept creation response, save the tab identity, then collect frozen evidence.
core.selectScenarioExperiment(experiment);
config = experiment.protocol.scenario;
```

保存完成后先清除场景 pending 状态，再同步新身份的进度，避免旧状态栏与新反馈冲突。新运行清除上一轮结果，防止旧成绩看起来属于本轮。重复点击 Start 使用 starting 状态互斥。

## Verification

实际命令（项目根目录）：

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B scripts/run_checks.py
node --experimental-vm-modules scripts/check_phase2_persistence.cjs
node --experimental-vm-modules scripts/check_phase1_persistence.cjs
git diff --check
Get-FileHash data/aimcoach.db
```

- 完整离线回归：78 Python、47 Node 通过；包括原 Agent/Tools、分析 golden、历史契约、Phase 1 设置/恢复，以及新增评分和前端行为。日志 `regression.log`。这包含单元/隔离集成/模型模拟，不是付费真实模型测试。
- 六场景真实前端 ESM 处理器 → HTTP → SQLite → history → 服务进程重启：全部通过。每类理想固定证据100分；重复提交200、同ID改内容409、缺失结束时间422，重启后六条成绩仍在。DOM 和时间输入模拟，HTTP / 校验 / 事务 / 磁盘 / 重启真实；日志 `persistence.log`。
- 原 Phase 1 真实 HTTP 持久化：通过完整/未完成隔离、唯一 Attempt、幂等、冲突、暂停、统计查询、结束/新建、重启恢复；日志 `legacy-persistence.log`。
- 公式手算：50% accuracy、满速度、满稳定性为77.5分；早退/无有效射击/跟随采样不足为0；极慢完整点击得分低于20；跨语言种子轨迹误差小于1e-7。
- 真实 IAB 浏览器、端口8018、独立临时 DB：六类均实际启动、结束并确认数据库保存。点击3类完成5次命中；跟随3类各运行10秒，未持续跟随时得到低分。四目标68.59、六目标65.41、Flick68.28、水平2.28、垂直5.83、Reactive12.86。不是训练水平标定样本。刷新验证四目标实验ID、1/1进度、配置恢复；`browser-results.json` 和截图为证据。另一次四目标未点击到时结束，归档0分。
- 未执行：付费模型、跨浏览器矩阵、真实硬件灵敏度一致性、长时间性能基准、低端设备 FPS 测量。不将这些标为通过。

开发中真实浏览器揭示了旧 selected ID 同步问题，已修复并补充真实控制器身份/返回旧模式测试。集成 fixture 曾因重复固定 UUID 产生409，已改用独立 UUID；没有放宽存储冲突规则。`initial-regression.log` 是阶段中间证据，最终结果以 `regression.log` 为准。

## 待校准与已知限制

- 800ms、1.25TPS、4倍半径、90%完整采样门槛和权重属于明确版本化的初始设计，没有人群标定；100分不是职业能力或真实 FPS 等价分。
- 浏览器提交的自洽轨迹可以被伪造。服务器验证数据一致性、重建目标和重算分数，不能证明真实鼠标动作；未宣称防作弊。
- 跟随缺失采样按完整时长扣覆盖，严重节流/掉帧会形成 incomplete；需人工在常用浏览器和设备上体验、校准门槛。
- 方向一致率及恢复时间为描述性指标，本版不另加综合权重；恢复只计首次重获，不代表持续跟随恢复。
- 新场景当前使用普通指针倍率1，Phase 1 虚拟灵敏度与 Pointer Lock 仍用于原 Flick；不声称两种输入协议同条件。新场景不新增灵敏度扫描实验。音效/命中闪烁仍沿用原场景功能，没有为新场景扩展反馈特效。
- Agent Runtime 和工具完全未变，新场景不接入旧 Insights；新评分通过场景/历史接口读取，避免旧指标误消费跟随数据。没有新增场景推荐 Agent。
- 完成成绩刷新后可通过历史 API 读取，页面不新增历史浏览器/回放；本轮结果面板刷新后不自动重绘旧分数。
- 同条件比较扫描同场景历史原始快照；未进行大数据量性能基准，暂无索引/缓存重构。没有账号或跨用户隔离，仍限本地单用户 Demo。
- 损坏的本地 pending 需要导出后人工处理，不静默丢弃；未增加导入/编辑历史的功能。

## 人工验收 Checklist

1. 备份现有 DB，重启后端，HTTP 打开 `/#/test` 并刷新；不要用 file://。旧活动实验先点“结束当前实验（保留历史）”。
2. 三类点击场景依次选中，目标数可设6，时长120秒；点击实心目标，故意 MISS 一次，确认位置/准确率/有效命中/总分和保存成功提示。
3. 三类跟随设10秒，移动普通指针追踪目标；观察边缘反射和 Reactive 速度/方向变化，到时确认指标、0–100分和1/1进度。
4. P 暂停，等待后继续；E 提前结束，确认归档未完成/0分且不推进。再试运行中 resize。
5. 同配置再训练，确认同条件次数和历史最高；改变难度或 seed，确认历史比较重新分组。
6. 断网后提前结束，确认未确认提示；刷新再点“重试保存场景”，恢复网络后只保存一份；可先导出备份。
7. 完成/显式结束场景实验后，选 Original Flick，点击训练区创建旧实验，再点击开始；验证旧设置、保存、分析与 Insights 仍可使用（原模式需要支持 Pointer Lock 的浏览器）。
8. 查询 `/history/experiments`、`/history/sessions?scene=flick_target` 和详情；重启服务后再次查询，确认历史仍在。

## 建议阅读顺序

1. `analysis/scenarios.py:20` — ScenarioConfig / registry / snapshot：先理解受控参数和冻结评分协议；`target_at` 在52行。
2. `analysis/scoring.py:11` — score：证据校验 → 指标 → 归一化 → 权重 → eligibility → 反馈。
3. `backend/scenario_store.py:7` — save：同一事务绑定实验、证据、成绩和同条件历史，理解幂等与不覆盖原则。
4. `frontend/scenario_ui.js:49` — finish / tick：时钟、采样、Attempt冻结、保存队列；`frontend/trainer.js:55` 理解新身份如何传给原控制器。
5. `tests/test_phase2_scoring.py:40` 与 `scripts/check_phase2_persistence.cjs:6` — 先读可复现证据，再看真实 HTTP 与重启验证。

本轮开始时工作区干净；以上未提交改动均属于 Phase 2，无历史未提交内容混入。可用 `git diff --stat`、`git diff -- backend frontend scripts README.md` 查看已跟踪文件差异；`git status --short` 同时列出本轮新增文件，未跟踪文件不会出现在普通 git diff 中。未自动 stage、commit、push。

隔离浏览器验收服务8018与临时页面已关闭；原有8012服务未停止。验收新API时需自行重启现有后端后刷新。
