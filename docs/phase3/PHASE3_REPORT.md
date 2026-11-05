# Phase 3 — Agent Personalized Training

日期：2026-11-05。实现完成，等待人工验收。无 commit / push，无真实模型或付费请求；测试只使用临时 SQLite 和明确标识的 Fake Client。Phase 2 基础评分代码没有修改。

## 实现结果与控制流

沿用 `run_experiment_planner` 的同一个 Tool Calling 循环，通过可选 `training_context` 注入训练工具、提示及结构化结果验收。未迁移框架，未增加 Multi-Agent，旧 `/agent/plan`、三个 sensitivity tools 和证据 gate 保持兼容。

训练路径：请求的目标/约束/日期场景范围 → SQLite 一次读事务生成历史快照 → get_training_history → analyze_performance_trend → recommend_training_plan → LLM 返回原样计划及解释 → 服务端严格核对 → 保存 proposed TrainingPlan → 前端展示 → 用户接受/拒绝/重新推荐 → 接受事务创建 Experiment 并关联 plan_id → 用户手动开始现有场景 → Session 保存 ACK → 自动或手动效果评估。

模型不拥有统计、评分或训练启动权限。结构化参数由 Python 根据用户边界与历史选择；LLM 负责工具调用和解释。最终模型 JSON 必须包含 `plan` 与 `explanation`，plan 必须与确定性工具返回对象逐字段、逐类型相同。额外指标、伪造 Session ID、非法场景或篡改参数不能进入执行层。模型解释单独命名 `model_explanation_unverified`，展示时明确其事实未作语义验证，不把解释中的数字当作历史证据。

## 三个工具的真实能力

### get_training_history

- 支持 limit 1–50、YYYY-MM-DD 日期起止、六个 registry scenario_id 或旧 `flick_2d`。
- 请求可用 `history_filter` 预设日期/场景范围，SQL 在数量截断之前应用这些过滤，再按训练开始时间读最近100条（额外第101条只用于标记 truncated）。因此旧日期范围可查询，不需要扫描全部记录到 Python。
- 所有工具共用这100条以内的固定请求快照。模型后续过滤只能缩小该范围，不能在一次推荐中换到另一份 latest；返回 snapshot_id、Session IDs、过滤范围与截断标识。
- 返回实际参数、开始时间、评分版本、服务器评分、适用指标、完成状态、Session/Experiment ID，以及去重的 Experiment 协议/进度信息。
- complete / incomplete / abnormal 明确区分。旧 Flick 有原指标、没有 aim-score-1 分数，不进入场景成绩比较。损坏 JSON、身份/协议不一致、未知评分版本或与重算分数不符，按 abnormal 返回安全原因，排除数值分析，不改写原记录。
- 模型不接收 raw clicks 或 tracking_samples。Python 在固定快照读取过程中验证并重算当前版本成绩，原始证据留在数据库。

### analyze_performance_trend

- 可选择快照内至多50个唯一 Session IDs；不存在/越界身份返回受控工具错误。
- 同条件 key 包含完整场景快照、difficulty、seed、评分版本/权重及 viewport。速度、目标数、时长、方向等控制参数不会被忽略。
- 每组按训练时间排序，返回样本数量、证据IDs、适用指标前半/后半均值与 delta；accuracy、平均命中耗时、TPS、tracking coverage/error、stability、score 等只在适用的同条件组内计算。
- 以归一化子指标均值识别相对薄弱项；全部相同时返回无独立薄弱项。至少3条样本才标记描述性证据 sufficient；不是统计显著性检验。
- 未完成、异常、旧未评分数据不会悄悄加入趋势分母，也不建立跨难度/版本成绩排名。

### recommend_training_plan

- 此工具无模型自由参数；目标、边界和 viewport 来自已校验用户请求，不能被模型偷偷替换。
- 优先复用符合全部约束的同条件历史，从相对薄弱项制定小规模练习；明确的切换/精准/水平/垂直/Flick/动态反应词可限定到对应 registry 场景。
- 暂为简单、透明的规则：目标关键词 + 同条件归一化薄弱项，不是机器学习，也不宣称开放自然语言意图理解已完备。
- 无历史或无匹配条件时给出首次/新条件基线练习，证据IDs为空、sufficient=false，默认 Flick 或跟随类别的基线场景；不将不匹配数据伪装成推荐依据。
- 输出场景ID、Sessions、task_parameters、training_goal、目标指标/方向、rationale 与真实 evidence_session_ids。准确目标看 accuracy、速度目标看平均命中时间、跟随看 coverage，其余看 overall_score；所有指标由 Python 计算。

## TrainingPlan、约束与持久化

`training-plan-1` 包括：plan_id、UTC created_at、scenario_id、sessions、task_parameters、training_goal、evidence_session_ids、plan_status、plan_version；另保存用户 bounds、viewport、frozen_scenario、请求级 snapshot metadata、推荐前 baseline_records、模型来源/解释/trace，以及执行关联 experiment_id 和最后一次 evaluation。

状态为 proposed / accepted / rejected，执行完成率单独由已确认有效 Sessions 计算。重新推荐会创建新计划，不覆盖原计划。刷新只保存/恢复选中 plan_id，服务器保留完整内容与评估，不自动开始训练。

用户边界默认：总有效训练时长120秒、最多3 Sessions、最多20 Attempts、每轮最多60秒、最高normal。合法范围分别是10–3600秒、1–10 Sessions、5–100 Attempts、10–120秒、easy/normal/hard。每轮场景仍遵守 Phase 2 参数上限；`sessions × duration_seconds <= time_budget_seconds`。暂停等待/人工休息不计入有效训练时长，不承诺墙上时间绝不超过预算。

接受时有以下保护：

1. 只接受 proposed，重试 accepted 返回同一 Experiment，不重复创建。
2. 必须显式完成或结束其他活动 Experiment，不自动终止现有训练。
3. viewport 必须匹配推荐时的尺寸，场景/评分快照必须与当前版本一致；升级后旧计划需重新推荐。
4. 接受在同一个 `BEGIN IMMEDIATE` 内创建 Experiment、冻结协议、写入 `training_plan_id`、关联 TrainingPlan。中间失败整体回滚，测试验证不会留下未关联的新 Experiment。
5. 用户视觉偏好通过原 ExperimentSettings 校验并冻结；执行 Sessions/Attempts/时长/难度来自计划，不能由接受请求改写。原训练记录和分数不更新。

数据库只增 `training_plans` 元数据表，无重建/清空旧表。现有 Experiment protocol JSON 新增 training_plan_id，Session 通过经过服务端校验的 experimentId 间接关联。旧场景上传、旧历史及旧 Agent 路由仍可用。

迁移在下次启动自动建表；本轮真实8012服务未重启，真实数据库未用于测试。实现期间记录的 DB SHA256 均为 `9CB8348A1524AFB2CCC3FA311ECE71F42D86100837585EFE2A390D6B66011893`。迁移前按原说明备份；回滚保留包含新计划/执行证据的完整副本，使用匹配旧代码的阶段前备份，不删除计划表或改写关联历史。Demo reseed 发现 retained plans 会拒绝覆盖。

## 效果评估

- 完成率 = 关联 Experiment 中已确认 complete 且验证有效的 Session 数 / 计划 Sessions。未完成或异常不算完成。
- 推荐前基线来自计划保存时的原始快照；推荐后只查该计划关联的 Experiment，有界读取，保留 before/after IDs。
- 只有完整配置/viewport/版本一致，且至少3条基线、2条执行后有效记录，才报告目标指标均值差及是否按预定方向改善；否则 delta、target_reached 为 null，明确 insufficient_evidence。
- 根据执行后的归一化子指标判断原相对薄弱项是否仍存在；所有子指标相同不硬造新的薄弱项。
- 返回继续、调整或收集更多同条件数据的规则建议。没有足够证据时，不武断推荐更换任务；用户可以重新请求计划。
- 评估快照包含 execution_session_ids、comparison_session_ids、目标、完成率、薄弱项、后续动作及时间。最后一次结果持久化；原始 Sessions 与冻结基线保留，可复核。该比较是描述性的，不将上升归因于 AI。

## API 与前端

- POST `/agent/training-plans`：生成并保存建议；与旧 POST `/agent/plan` 并存。
- GET `/training-plans/{plan_id}`：恢复计划、执行关联和最后评估。
- POST `/training-plans/{plan_id}/accept`：需 viewport，可提交原用户 preferences；只建实验，不开练。
- POST `/training-plans/{plan_id}/reject`：保留拒绝记录；accepted 不允许追溯拒绝。
- POST `/training-plans/{plan_id}/evaluate`：确定性评估，无模型调用。

缺失计划404、状态/活动实验/快照冲突409、越界/非法数值422、数据库失败503。模型不可用503、超时504、异常响应/预算失败使用原 Runtime 安全错误码及 trace，不伪造成功回复。

Test 页新增功能面板，没有视觉重设计。可设目标、数量/时长/难度、日期范围；展示参数、实际基线 IDs、来源、证据充分度和反馈。拒绝/重新推荐、接受、评估均有明确按钮，Session 保存后跟踪实验进度自动评估，避免仅 POST 未确认就推进。

## Before / After 与主要文件

之前只有 sensitivity 推荐；本轮保留循环，通过可选上下文选择工具和验证方式：

```python
# Before
analysis_snapshot = create_analysis_snapshot(experiment_id)
# After
analysis_snapshot = training_context or create_analysis_snapshot(experiment_id)
runtime_tools = training_context.tools if training_context else TOOLS
```

之前 Experiment 工厂总是独立提交；现在接受计划可传入调用者事务，保证关联原子性：

```python
# Default callers keep their existing transaction semantics.
scope = closing(_connect()) if _connection is None else nullcontext(_connection)
# Acceptance creates and associates inside its own BEGIN IMMEDIATE.
experiment = store.get_or_create_experiment(..., _connection=connection)
```

文件职责：

- `analysis/training_tools.py`：过滤模型、固定历史快照、三个确定性工具、训练 profile 的 schema/输出 gate。
- `agent/experiment_planner.py`：小范围接入 training_context；同一轮次/时间/工具预算、关闭模型连接逻辑继续复用。
- `backend/training_plans.py`：计划请求/保存/接受/拒绝/评估及同条件证据关联。
- `backend/main.py`：5个新 API 及安全错误处理；`backend/session_store.py`：增量表与事务复用。
- `frontend/training_plans.js`、`frontend/app.js`、`frontend/index.html`：功能面板、恢复与计划执行适配。
- `scripts/seed_demo.py`：新计划数据保护；`scripts/run_checks.py`：纳入前端回归。
- `tests/test_phase3_training.py`、`tests/test_phase3_ui.cjs`：后端/Runtime 与前端处理器测试。
- `scripts/check_phase3_training.py`：合成数据/Fake Client 的真实 HTTP 与磁盘/重启闭环。
- `README.md`、本报告、`regression.log`、`demo-example.json`、`browser-plan.png`：说明及可复核证据。

## Verification 与完整测试示例

实际执行：

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B scripts/run_checks.py
D:/Anaconda/envs/aimcoach/python.exe -B scripts/check_phase3_training.py
node --experimental-vm-modules --test tests/test_phase3_ui.cjs
git diff --check
Get-FileHash data/aimcoach.db
```

完整回归：91 Python、50 Node 通过；日志见 `regression.log`。覆盖原 Agent/Tools、评分、实验/Session、历史兼容，以及新增工具参数、六场景目标选择、缺失 Session、100条读取上限、日期先过滤、同条件隔离、模型超时/死循环/伪造结果/单次工具失败恢复/无模型、接受幂等/事务回滚、异常与未完成排除、计划评估及重启。Node 覆盖实际 ESM 处理器请求/接受/不自动启动、视觉偏好、进度反馈、拒绝、错误与恢复；DOM/服务响应为模拟。

真实 HTTP 检查通过：临时 SQLite、原 FastAPI/uvicorn、原 Runtime、原 Session 上传和评分、Fake Client；uvicorn 服务实例停止再启动后基线与评估保持一致。这是在同一测试进程内更换服务实例并重新执行 lifespan，不等同于独立操作系统进程重启测试。例子是合成数据，不是用户历史，不是真实模型表现：

```json
{
  "source": "SYNTHETIC TEST DATA / FAKE CLIENT",
  "scenario_id": "flick_target",
  "sessions": 3,
  "task_parameters": {
    "scenario_id": "flick_target", "difficulty": "normal", "seed": 1,
    "duration_seconds": 10, "target_count": 5, "speed": 120, "direction": "positive"
  },
  "training_goal": "提高稳定性",
  "target": {"metric": "overall_score", "direction": "increase", "minimum_delta": 0},
  "baseline_scores": [77.5, 77.5, 77.5],
  "execution_scores": [100, 100, 100],
  "completion_rate": 1.0,
  "metric_delta": 22.5,
  "causal_improvement_claim": false
}
```

真实 UUID、完整 plan_version/status、工具 trace、四轮调用、推荐前/后 Session IDs、弱项变化和重启结果见 `demo-example.json`。示例“77.5→100”是预设 MISS/命中轨迹的确定性结果，不是 AI 效果实验证明。

真实 IAB 浏览器：8019 独立合成库 + Fake Client，实际点击请求→接受→保持 READY/0÷1→手动开始→5次目标命中→保存→自动评估完成率100%→刷新恢复。由于浏览器 viewport 不同于800×600合成基线，正确返回无同条件证据，不显示改善；截图见 `browser-plan.png`。临时页面和服务已关闭。

未运行：真实 LLM/付费模型、跨浏览器矩阵、长时间性能、独立操作系统进程重启与真实用户干预效果实验。Fake Client 不会在生产路径自动代替缺失 Key。

## 已知限制与人工验收

1. 自然语言目标目前由简单关键词与确定性规则解释，复杂目标可能退回默认基线；用户必须审阅推荐。没有语义模型评测结果。
2. 历史读取有明确上限。超出100条需缩小日期/场景范围重新请求；不会在同一次 Agent 请求中自动扩张快照。
3. 旧训练可查原指标，但不伪造评分，也不参与新场景的综合分趋势。未知未来评分版本会标 abnormal/unsupported，需要相应版本读取器。
4. 三条基线/两条执行只是描述性保护门槛，未进行统计校准、显著性或因果推断；单 Session 计划即便完成，仍可能无法判断效果。
5. 每个计划是一个场景多 Sessions，不是跨场景课程编排；没有新 Agent/Tools 之外的复杂控制流。
6. 最后评估是一个有时间和证据IDs的快照；保留原数据供重算，不新增每次中间评估的独立审计表。界面恢复最近选中的计划，可按ID读取旧计划，未开发计划列表/回放。
7. 浏览器输入仍是客户端报告，不能证明实际操作或防作弊。评分公式和版本没有调整。
8. 实际验收需重启现有后端后刷新 HTTP 页面，才能加载新路由/表；不应对旧服务的404误认为计划不可用。请求模型需用户自行决定是否使用已配置账号额度。

人工步骤：备份DB并重启→打开Test→输入目标和小范围边界/日期→请求（无Key应明确失败，不返回Fake结果）→查看真实来源及SessionIDs→拒绝或重新推荐→结束其他活动实验后接受→确认READY且未自动开练→手动执行各Session→观察保存ACK/计划完成率/证据充分性→刷新和重启后按plan_id查询→再验证旧Flick/旧Agent入口。

建议阅读顺序：

1. `analysis/training_tools.py:60` — load_history，先了解固定快照与范围上限；`:159` / `:196` 为趋势和规则推荐。
2. `analysis/training_tools.py:240` — TrainingContext，理解工具 schema、调用顺序与最终输出核验。
3. `agent/experiment_planner.py:340` — 同一 Runtime 如何接入训练上下文并保留原预算。
4. `backend/training_plans.py:82` — accept_plan；`:116` 为 evaluate_plan，重点检查事务关联与同条件证据。
5. `frontend/training_plans.js:90` — checkProgress，理解保存确认后的自动评估。

工程规范 skill 在本轮用于限制改动范围、保留历史、注释 What/Why、隔离数据库测试及报告真实结果。未提交改动均为本轮实现；两个 `docs/phase2/acceptance-8012.*.log` 是此前已有服务日志，未修改或当作 Phase 3 成果。
