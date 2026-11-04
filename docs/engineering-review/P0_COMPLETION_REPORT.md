# Remaining P0 Engineering Completion Report

日期：2026-10-28。项目定位仍为本地单用户 MVP / Demo。范围依据 IMPROVEMENT_PLAN.md 的原始 P0-1～P0-5 编号；附件 Task A/B 对应 P0-2，Task C 对应 P0-4，Task D/E 对应 P0-5，其他剩余任务主要为 P0-3。

## 1. 最终状态与验收门

本阶段剩余工程代码均已实施，离线回归通过，但**整体尚不能标为验收就绪**。

- **P0-1 — DONE**：沿用共享数据契约和入口守卫；合法/非法数据及历史黄金结果回归通过。本轮不修改指标公式。
- **P0-2 — DONE**：沿用 UUID、协议、持久 schedule、确认进度和请求级快照；补充非法恢复状态、损坏存储 JSON 的受控错误检查；并发/隔离回归通过。
- **P0-3 — DONE（本地离线实现与验证）**：原 smoke 有可收集的隔离回归，历史 fixture 附来源 hash 和只读 adapter；可重复的独立合成 Demo seed、统一检查脚本及 CI 配置完成。同一检查命令在本地通过；远程 CI 与全新环境依赖安装为 NOT TESTED，不宣称已经运行。
- **P0-4 — DONE（按计划的 fake model 验证）**：必要证据覆盖、参数和结果验证、有限纠错、错误分类、预算和 request trace 均通过离线测试。真实模型 smoke 保持显式 opt-in，本轮未调用付费 API。
- **P0-5 — NOT TESTED（完整验收门）**：实现、自动回归、无 Key 真实 HTTP、真实浏览器页面/恢复/错误反馈及手动下载已通过。**真实鼠标采集及自动失败下载尚未通过完整浏览器演练**，内置浏览器的 Pointer Lock 被 `WrongDocumentError: The root document of this element is not valid for pointer lock` 阻止。

最终结果：4/5 项达到上述已声明的 DONE 范围；P0-5 代码已实现，但必要的实际交互验收未完成。没有剩余的已知失败自动测试。浏览器交互验收的阻碍为 BLOCKED，未执行的验证为 NOT TESTED；二者不伪装为 DONE。

因此没有使用 `P0 IMPLEMENTATION COMPLETE — READY FOR USER ACCEPTANCE` 总体标记，也不建议先转入 UI 美化或性能优化。先完成第 8 节人工验收，再由维护者更新验收门。

## 2. 每项问题的原因和解决方案

### P0-1：保留已有数据契约

前一阶段已解决原始结构、类型、事件时间和 HIT 字段一致性漏洞。本阶段只运行和保留这些回归，没有重新设计 Session，也未要求固定新的统计样本阈值。

真实命中真实性仍无法由客户端上报字段证明；几何一致性不等于反作弊认证。这一边界继续有专门测试。

### P0-2 / Task A、B：完成已有身份与证据的一致性边界

当前实际代码已经是后端 UUID + experiments 表 + 持久 schedule，不能按旧计划描述再写一套身份系统。本阶段复用该实现：

- `_experiment_state` 在恢复前检查协议版本、Session schema、必要数值、schedule 结构/唯一性和提交顺序连续性；坏状态返回明确 conflict。
- 前端 applyExperimentState 检查身份、完成数、Session 集合、状态、next_condition 与 schedule 是否自洽；错误响应不覆盖当前选择。
- 一个推荐从一个读事务取得确定 Session 集合，AnalysisSnapshot 不可变，三个 Tool 继续显式接收相同对象。后续写入只影响下一请求。
- 损坏 JSON、身份/协议冲突、无记录分别以受控数据错误处理，不冒充模型故障或有效分析。

快照生命周期：request 开始 → SQLite 读事务固定协议和 rows → 释放数据库连接 → 纯计算一次并冻结 JSON → Dispatcher/Tools 读取副本 → 结果/失败返回后局部对象释放。没有进程级 latest 缓存，也没有持锁等待模型。

### P0-3：从作者本地 smoke 变成可复现离线检查

原三个 analysis/test 文件主要通过 main/verify 方法及真实 DB ID 验证，不能由常规测试发现机制提供可靠回归。

本阶段保留原手动函数和历史思考，新增 unittest.TestCase，并使文件默认执行入口运行隔离测试；tests/test_legacy_suite.py 将其纳入统一命令。不会为了通过断言清空或重建开发者数据库。

历史 JSON adapter 只读原文件、校验 manifest hash，在内存副本中显式赋予 `fixture:experiment_002` 这类派生组身份，来源记录声明 order_unknown，不倒填真实实验顺序。

Demo seed 是**全合成**的九轮×三目标数据，不拿旧实验冒充新协议。必须显式指定独立 DB；原训练库路径拒绝，现有非 Demo 内容拒绝，已标记 synthetic 的 Demo 可以重复执行。所有合成 Session 带 syntheticDemo，协议带 demo_fixture。重复 seed 不增加 Session，不覆盖旧行。

### P0-4 / Task C：证据门槛和运行失败边界

原问题不是缺少一段 prompt：模型可以零 Tool 返回文本，JSON schema 不能替代服务端参数检查，工具结果/错误也没有受控处理。

现在接受推荐的条件是：

1. 同一 managed experiment 的数据有效且至少有非空 metric profile；空/无效/未验证协议不能调用模型生成成功建议。
2. 已成功读取 summary，获得实验范围及限制。
3. profile/compare 的成功结果覆盖当前快照中**每个已测试条件**。summary 不含全部指标，不能单独当作取证充分。
4. Tool result 的范围和实际 payload 与快照一致；只检查 snapshot 标签还不够。
5. 最终回答非空、没有拒答/截断或错误的消息结构，且没有超预算。

这不是“至少多少 Session 才显著”的新业务阈值。部分实验仍可为下一步采集提供建议，但 evidence.incomplete=true；未测试条件只能是未来实验条件。自由文本建议仍不等于统计显著性或训练效果证明。

工具参数检查：白名单、对象形状、必需/额外字段、有限数值、排除 bool/字符串、比较至少两个不同条件、唯一性及已测试条件查询。未知条件、坏 JSON、错误结果作为 tool role 的显式 error 回传，失败不计入 tools_used 或取证覆盖；全请求只允许一次工具错误纠正机会。

零工具/缺 metric 的最终文本不会直接成功，可得到一次有界取证提示；仍不满足则 insufficient_evidence。消息检查覆盖空答、拒答、length 截断、finish state 矛盾、坏 tool call、重复 call ID（含跨轮）。最后一轮要求收尾，不执行不能再总结的新 Tool。

预算为技术资源限制，不是业务统计规则：总协作时限 60s，单次 SDK timeout ≤15s/剩余时限，最多 6 次模型尝试、12 次工具调用、每次 completion 上限 1000、累计 accounting 上限 128000、全请求一次瞬态 provider 重试。SDK 内建重试显式为 0，避免叠加不可见重试。provider 缺 usage 时以请求 UTF-8 bytes 和输出预算保守计费，不把估计称为真实 token 数。

错误有 request_id、code、trace 和可用的 evidence；正常结果附 status=ok、request_id、trace、usage。trace 区分成功与失败工具，包含合法参数、snapshot_id、耗时和安全错误码。畸形原始参数不直接回显；provider 异常文本、Key 和请求头不写日志/响应。

### P0-5 / Task D、E：启动可选、保存可确认

原模型客户端在模块 import 时要求 Key，使非 Agent 功能一起失效。现在：

- get_model_client 只在 Agent 请求内读取配置和导入可选 SDK；缺 Key/SDK 初始化失败返回明确 503。
- lifespan 初始化 DB；import 本身不初始化数据库、不联网。
- run.ps1 缺 Key 仅提示 Agent 不可用，继续启动。
- GET /experiments/{id}/analysis 暴露已有确定性分析，不增加 Agent Tool，也不需要 Key。

原保存只靠 console/下载 fallback，没有 durable pending、超时或幂等确认。现在：

- Session 结束先将完整 payload 写入本地队列，每个 Session 独立 key，避免两标签页覆盖同一队列数组。
- POST 前后状态明确：待确认、保存中、瞬态重试、已确认、失败保留、损坏/冲突需导出。中央训练提示也不再将未确认状态显示成可直接重复训练。
- 只在 `{saved:true, session_id:本次ID}` 的确认后清除队列；HTTP 200 或错误 ID 都不能单独视为成功。
- 请求等待覆盖 headers 和 JSON body parsing，默认 15s；瞬态失败使用原 ID/原 payload 自动重试一次。用户可手动重试。
- 服务端同 ID/同 canonical JSON 幂等确认；相同 ID 但不同内容仍 409，不写第二行。canonical JSON 区分 true 与 1，不使用 Python 宽松字典相等误认内容。
- 进度同步 GET 与 POST 确认分开：GET 失败不撤销已确认的保存。损坏队列不自动修复/删除，可完整导出。
- 刷新时恢复 pending 再确认保存；待上传数据未解决时不允许新训练或 Agent 规划。
- 保留自动 raw Session 下载尝试，新增手动队列备份；文案只说“触发下载，请确认文件”，不声称浏览器已成功保存。
- 存储禁用/配额失败时保留内存副本、明确警告、触发导出并在 beforeunload 提醒；不能保证用户强制关闭、清理浏览器数据后仍可恢复。
- Agent 请求支持取消本地等待，旧结果不会显示；Pointer Lock、fallback 的拒绝路径有明确反馈。

必要 UI 仅增加状态/重试/导出/取消控件、状态文本对比色及变更资源版本。实际浏览器检查发现旧 stylesheet 缓存令反馈不可读，增加资源版本后确认 status computed color 为 rgb(238,238,238)。没有做主题、动画或布局重设计。

## 3. 本轮实际修改与之前已有内容

### 修改既有文件

- `agent/experiment_planner.py`：lazy model client、严格 Dispatcher、结果校验、取证覆盖、预算、失败状态和 trace；保留显式 Tool Calling。
- `agent/qwen_test.py`：真实 smoke 仅在 main 内、AIMCOACH_LIVE_SMOKE=1 时运行；导入/收集测试不初始化模型。
- `backend/main.py`：lifespan、无模型分析接口、成功/失败 DTO 与安全错误映射。
- `backend/session_store.py`：可配置隔离 DB 路径、持久状态完整性检查、坏 JSON 分类、严格幂等重试。
- `analysis/analysis_runtime.py`：将已有数据的类型/数值计算错误作为证据 conflict，未改指标公式。
- `frontend/trainer.js`：请求 deadline、取消、pending flush/恢复、确认与反馈、错误状态和输入锁定路径。
- `frontend/index.html`：最小反馈控件、队列脚本及静态资源版本。
- `frontend/style.css`：仅保存/Agent 状态文本的可读性。
- `run.ps1`：Key 可选，保留本地地址。
- `README.md`：当前 Demo/历史协议区别、无 Key 启动、统一检查、独立 seed、API 契约；Key 验证改为 bool，不 echo Key。
- `analysis/test/test_analysis_service.py`、`test_db_source.py`、`test_tools.py`：保留历史函数，新增独立 TestCase、默认隔离执行入口。
- `tests/test_session_contract.py`：重复提交的验收改为相同内容 200、冲突内容 409，并强化 bool/number 不同内容测试；不是弱化失败断言。
- `tests/test_experiment_consistency.py`：fake model 补真实消息 finish state 和必要取证过程，适配新错误 DTO；保留并发/快照断言。
- `tests/test_experiment_frontend.cjs`：模拟持久存储和 deadline，新增真实失败路径回归。
- `docs/engineering-review/IMPROVEMENT_PLAN.md`：保留原编号/方案，新增当前执行状态及未通过的验收门。

### 新增文件

- `frontend/pending_sessions.js`：无依赖、每 Session key 的 pending queue。
- `analysis/legacy_adapter.py`、`tests/fixtures/legacy_manifest.json`：只读历史 adapter 与 20 个文件的 hash（19 份 JSON + 原分析输出）。
- `scripts/seed_demo.py`：独立、幂等、明确合成的 Demo seed。
- `scripts/run_checks.py`、`requirements-test.txt`、`.github/workflows/offline-checks.yml`：统一离线入口、现有依赖的最小测试安装清单和 CI 配置。
- `tests/test_agent_guardrails.py`、`test_demo_fixture.py`、`test_legacy_suite.py`、`test_pending_queue.cjs`：新增离线/fixture/队列验证。
- 本报告及 `artifacts/p0-no-key-browser.jpg`、`artifacts/p0-browser-initial-cached-style.jpg`：真实页面最终反馈与旧缓存失败证据。

### 不应计为本轮新成果

P0-1/P0-2 开始前已经存在未提交实现及审计文档。本阶段不修改 session_validator.py、analysis_service.py 的实现或 metrics；analysis/tools.py 的 snapshot 机制来自 P0-2。BASELINE、DECISIONS、P0_1/P0_2_CODE_REVIEW 和 SESSION_CONTRACT 未重写。

当前 Git diff HEAD 会混合这些阶段；untracked 也不等于本轮创建。未自动 add、commit、push 或重写历史。

## 4. 关键 Before / After

以下 Before 是本轮开始的 P0-2 工作区，不是最早初始提交；片段仅保留关键逻辑。

### 模型启动依赖

```python
# Before: module import blocks the entire app.
API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    raise RuntimeError(...)
client = OpenAI(api_key=API_KEY, ...)

# After: only a model request needs this optional capability.
def get_model_client():
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise ModelUnavailable(...)
    from openai import OpenAI
    return OpenAI(api_key=api_key, timeout=15, max_retries=0, ...)
```

### 证据门槛

```python
# Before
if not assistant_message.tool_calls:
    return {"answer": assistant_message.content or "", ...}

# After
if not calls:
    if summary_seen and covered >= tested:
        return {"status": "ok", "answer": content, "evidence": evidence, ...}
    # One correction may obtain missing evidence; otherwise return a controlled failure.
```

covered 只由成功、已验证的 metric Tool 更新。summary 负责范围，不能用它代替四项指标。

### 工具错误

```python
# Before
arguments = json.loads(raw_arguments)
tool_result = execute_tool(tool_name, arguments, snapshot=analysis_snapshot)

# After
arguments = json.loads(raw_arguments)
validate_tool_arguments(name, arguments)
tool_result = execute_tool(name, arguments, snapshot=analysis_snapshot)
verify_tool_result(name, arguments, tool_result, analysis_snapshot)
# Failed attempts produce explicit tool errors and do not add evidence coverage.
```

### 保存恢复与确认

```javascript
// Before: a failed request only attempts a JSON download.
await saveSessionToBackend(session);

// After: preserve before sending, then clear only after matching acknowledgement.
pendingQueue.put(completedSession);
await saveSessionToBackend(session);
pendingQueue.acknowledge(session.sessionId);
await refreshExperiment();
```

实际实现还处理存储失败、retry budget、队列损坏和同步 GET 失败；片段不能代替完整异常分支。

### 服务端幂等

```python
# Before
if existing:
    raise sqlite3.IntegrityError("Session already exists.")

# After
if existing:
    if canonical(json.loads(existing["raw_data"])) == canonical(session):
        return
    raise sqlite3.IntegrityError("Session ID belongs to a different payload.")
```

同内容重试不覆盖、不追加；不同内容冲突保留原行。

## 5. 关键变量、判断和状态流转

- `request_id`：一次 Agent 请求的可追踪身份，成功/失败均可定位，不包含 Key。
- `analysis_snapshot` / `evidence`：请求独有不可变事实及其范围；所有 Tool 共用，失败也保留已确定的 snapshot metadata。
- `tested` / `covered` / `summary_seen`：从快照导出的已测试集合，与成功取证集合/范围标志；不是统计显著性标志。
- `tool_errors` / `evidence_corrections` / `retries`：分别限制工具纠错、缺证据提示和 provider 瞬态重试，避免多层无限重试。
- `tokens_used`：accounting budget，SDK 提供 usage 时使用真实计数，否则使用保守估计；不是账单查询。
- `PendingSessionQueue.memory`：当前页面备份；独立 localStorage key 是可跨刷新恢复的持久来源；两者均在确认前保留。
- `memoryOnly`：存储失败时的危险状态。它触发明确提醒和 beforeunload，而不是声称数据已经 durable。
- `sessionSaving`：单页面 flush 互斥，重复按钮不重复发起保存；跨页面并发由服务器幂等/条件事务处理。
- `planAbortController` / `planRequestVersion`：取消等待和过期结果防护。取消不是服务器撤销已经发出的模型请求。
- `saved=true + matching session_id`：唯一可清理 pending 的服务端确认；进度 GET 的成功与否不改变这一事实。

完整保存流：Session 完成 → durable put（失败则内存/导出提醒）→ POST → 瞬态一次原 payload 重试 → 匹配 ack → 删除该 queue key → 同步已提交进度。任一步未确认都不自行改 ID、覆盖 payload 或虚增进度。

## 6. 实际验证结果

### 统一回归

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B scripts/run_checks.py
```

最后一次完整运行：**52 个 Python 测试方法通过，25 个 Node 测试通过，两个 JS syntax check 通过**，命令退出 0。Python 约 14.137s，Node 约 1366.84ms；这些不是性能 benchmark。

等价子命令：

```powershell
python -B -m unittest discover -s tests -v
node --test tests/test_experiment_frontend.cjs tests/test_pending_queue.cjs
node --check frontend/trainer.js
node --check frontend/pending_sessions.js
git -c safe.directory=D:/Projects/AimCoach diff --check
```

Python 覆盖：

- P0-1 合法手算指标、负时间、几何矛盾、类型/容器/空缺字段、非法值、API 422 不落库、直接写入守卫、已有坏行拒绝、历史 19 份兼容。
- P0-2 持久身份/完成后恢复、并发创建/同条件竞争、协议拒绝、事务读一致性、多个实验隔离、请求期间写入不影响原快照、新请求看到新数据。
- P0-3 原三个 smoke 的可收集隔离回归；标准差手算、finding 等值/单条件边界；历史 golden 的 profiles/findings/limitations 一致；manifest；独立 seed 的重复/拒绝覆盖/配置为 synthetic 运行库后重跑。
- P0-4 成功取证、零工具、summary 不足、坏参数/JSON、一次恢复、错误 Tool scope/payload、空答/截断/拒答、坏 finish/call ID、六轮收尾、工具/token/deadline 预算、provider timeout/retry/redaction、缺/坏证据也有 request ID。
- P0-5 无 Key import/lifespan、Session API、确定性分析、503；缺可选 SDK；同 payload 幂等与冲突拒绝（含 true/1 区别）。

Node 覆盖：

- 实际 trainer.js 的刷新/跨标签页/完成后选择/非法恢复/旧响应失效。
- 保存成功、未确认不推进、断网刷新恢复、原 ID 重试、已提交但丢响应、重复点击互斥、超时退出、错误确认不清队列。
- 损坏状态保留并可导出、pending 阻止规划、模型取消等待、Pointer Lock/fallback 拒绝、quota 内存保留及关闭提醒。
- PendingSessionQueue 的独立 key 并发、跨实例恢复、冲突/损坏 raw 保留、存储不可用、确认后清理失败可再次幂等重试。

测试过程中曾发现跨 JavaScript realm 的 TypeError 未被识别为网络失败，导致丢响应重试负例失败；已同时检查 error.name 并保持原断言，最终通过。新增测试的 db_path fixture 变量遗漏也已修正。没有跳过失败测试、清库或弱化断言制造结果。

### 真实 HTTP 与浏览器：明确不同层次

使用 `AIMCOACH_DB_PATH` 指向系统 Temp 的独立 DB，在 127.0.0.1:8011 启动真实 Uvicorn，子进程 Key 为空；没有更改用户永久环境配置。

真实 HTTP 使用明确标注 syntheticAcceptance 的合成 Session，验证：首次 POST 200、相同 payload 重试 200、同 ID 不同内容 409、GET 200、确定性分析 200、Agent 503 / model_unavailable / request ID。这不是物理鼠标采集。

真实内置浏览器验证：

- 页面和当前实验加载，HTTP 合成 Session 保存后刷新恢复为 Session 2/9。
- 输入 goal 后看到真实 503 反馈和 request ID，没有显示伪造 answer。
- 手动导出实际产生下载，读取文件确认 format=aimcoach-pending-v1、records=[]。这是空队列下载机制验证，不是完整训练 payload 的浏览器导出验收。
- 原状态文字黑色及旧 CSS 缓存问题定位后，版本化资源实际返回 200，computed color 变为 rgb(238,238,238)。只修改功能反馈的可读性。
- Pointer Lock 请求及 fallback 被该文档环境拒绝，出现 WrongDocumentError；页面没有开始 Session。没有用修改浏览器安全设置或注入假命中来冒充成功。

截图：[真实无 Key 反馈](D:/Projects/AimCoach/docs/engineering-review/artifacts/p0-no-key-browser.jpg)。初始缓存失败截图另行保留。实际验收临时服务已停止，临时 DB 未清空；不会误把该服务当作开发者的真实运行库。

### 未运行或未完成

- **NOT TESTED**：正常桌面浏览器里的真实鼠标完整九轮、真实采集 Session 的自动下载/手动导出、浏览器原生 quota/网络 offline 的完整人工演练。
- **BLOCKED**：本次内置浏览器的完整 Pointer Lock 采集验收，具体 WrongDocumentError 如上。
- **NOT TESTED**：真实 Qwen 付费 smoke、供应商兼容和实际延迟；运行需明确 AIMCOACH_LIVE_SMOKE=1。本轮按要求不依赖真实 Key/费用。
- **NOT TESTED**：远程 CI job、全新环境 pip 安装、全仓 pytest、生产部署、多用户/安全/负载、真实数据库迁移与备份恢复。
- 本地安装版本已经检查，与 requirements-test 中 FastAPI/Pydantic/Uvicorn pins 一致；不把它称为全新安装验证。

## 7. 数据安全、限制和风险

真实 `data/aimcoach.db` 前后 SHA-256 均为 `1c4a46be675fba110ee79e66aa3a01fd6fc7276401ca1968b153366ebffd1dbb`。未把它用于测试、未迁移或清理它。原 JSON 和 analysis/output 无 diff，指标代码本阶段未修改；历史 Git 保留。

剩余边界：

- 证据门槛证明“成功取得指定事实”，不能证明自由文本中的每句话都正确；LLM 仍可能夸大 pooled trend。语义评估/引用与更强输出契约留给 P1，不宣称消除幻觉。
- 请求时限是 SDK timeout + 单调时钟检查；不能强制中断任意挂死的底层线程。浏览器取消只停止等待/展示，服务器已有请求仍受自己的预算约束。
- pending 的永久 409、损坏状态、不同 payload 同 ID 需要人工判断，不能自动覆盖、重分组或删除原始数据；可先导出保存证据。
- localStorage 不是无限可靠存储；禁用/配额/磁盘故障时只保留页面内存并告警。强制关闭、清理站点数据、下载未保存可能造成损失，不能承诺平台故障下绝不丢失。
- UUID/协议/快照不是客户端真实性认证；raw/fallback 模式由客户端上报，不能证明 OS/DPI 输入完全受控。
- 固定训练尺寸、未完成实验没有擅自设计“放弃并覆盖”功能；改变环境无法恢复尺寸时需后续明确产品规则。
- 本地单用户限制不变，没有账号隔离、限流、生产安全或正式公网部署。新配置/预算不是生产 SLA。
- test dependencies 使用已有版本和标准库/Node runner，没有引入 Agent 框架或 UI 框架；完整 runtime freeze 的精简和全锁定仍属 P1。

## 8. 后续人工验收清单

请在正常桌面 Chrome/Edge 等支持 Pointer Lock 的环境，用独立测试 DB 执行，避免把合成数据或手工故障注入真实训练库：

```powershell
# Use a new terminal; keep the training database untouched.
$env:AIMCOACH_DB_PATH = Join-Path $env:TEMP ('aimcoach-manual-' + [guid]::NewGuid().ToString() + '.db')
$env:DASHSCOPE_API_KEY = ''
.\run.ps1
```

1. **无 Key 启动**：首页、上传/读取、确定性分析可用；Agent 在有有效样本时返回明确配置错误，不伪造推荐。
2. **真实输入**：点击进入 Pointer Lock，完成一个三目标 Session；状态依次显示保存中、服务器已确认，刷新恢复下一条件。Esc 中止未完成 Session，条件不推进。
3. **完整周期**：完成九轮、刷新保持完成，明确开始新实验后才出现新 ID。双标签页不增加重复已提交条件。
4. **断网恢复**：在隔离场景完成 Session 后阻断网络，确认 pending 提示、原始数据导出；刷新仍可恢复，网络恢复后原 ID 重试只保存一次。
5. **丢确认/慢响应**：使用隔离测试代理/开发工具，确认超时会退出等待，且未确认数据仍保留；恢复后无重复行。
6. **真实文件导出**：检查自动 fallback 与手动备份文件实际存在，包含预期原始 Session，确认文件内容后再处理永久冲突。仅触发下载不算通过。
7. **错误权限/容量**：拒绝 Pointer Lock、限制存储或模拟 quota 时，页面不误报成功，内存-only 告警和关闭提醒可见。
8. **模型（可选独立验收）**：如维护者决定调用真实模型，显式 opt-in，记录 model/date/request_id/snapshot；确认取证 trace、失败分类与预算，不把 mock 或无 Key 检查当作真实模型成功。

第 2、4、6 项中未完成的实际浏览器检查是 P0-5 总体状态不能为 DONE 的直接原因。完成并记录后再由维护者决定总体验收，不需要先做视觉改造或性能工作。

## 9. 建议阅读的关键代码

1. [experiment_planner.py:314](D:/Projects/AimCoach/agent/experiment_planner.py:314) — run_experiment_planner。先看 snapshot/tested/covered，再看失败 helper、预算、纠错和最后一轮；对照 :253 validate_tool_arguments、:288 verify_tool_result。
2. [main.py:76](D:/Projects/AimCoach/backend/main.py:76) — lifespan；再读 planner.py:49 get_model_client 和 main.py:243 plan_next_experiment / :334 read_analysis。理解 import、启动和模型请求的职责分离。
3. [pending_sessions.js:10](D:/Projects/AimCoach/frontend/pending_sessions.js:10) — put/list/acknowledge。重点看独立 key、内存 fallback、损坏 raw 保留与确认后清理。
4. [trainer.js:613](D:/Projects/AimCoach/frontend/trainer.js:613) — retryPendingSessions；对照 :671 completeSession、:1190 restoreApplication、:119 fetchJson。理解保存/确认/恢复/超时的先后顺序。
5. [session_store.py:263](D:/Projects/AimCoach/backend/session_store.py:263) — save_session 的严格幂等与写事务；对照 scripts/seed_demo.py:40 的只读目标检查、analysis/legacy_adapter.py:9 的来源声明，审查是否保护真实证据。

测试入口为 scripts/run_checks.py；新核心注释使用简单英文，重点说明 What/Why、关键条件和状态转换。

## 10. Git 与变更查看

```powershell
Set-Location D:/Projects/AimCoach
git -c safe.directory=D:/Projects/AimCoach status --short --untracked-files=all
git -c safe.directory=D:/Projects/AimCoach diff -- agent/experiment_planner.py backend/main.py backend/session_store.py frontend/trainer.js frontend/index.html frontend/style.css run.ps1 README.md
git -c safe.directory=D:/Projects/AimCoach diff --check
Get-Content frontend/pending_sessions.js
Get-Content tests/test_agent_guardrails.py
Get-Content tests/test_pending_queue.cjs
```

已有 P0-1/P0-2 修改与本阶段修改共存于工作区，普通 diff 不含 untracked 文件，HEAD 对比也不是本阶段独立补丁。第 3 节已区分归属；未通过重置、覆盖历史文件或自动提交制造干净状态。
