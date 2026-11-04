# AI Aim Coach：真实基线

审计日期：2026-10-28（Asia/Shanghai）。基线提交：`86655a328d102efdcb9aa16c3cda711d4113680b`（Initial commit，2026-10-10）。定位：本地单用户工程 MVP / Demo，非已部署的商业或生产系统。

## 证据规则与审计边界

- **已验证事实**：本轮直接检查代码、Git、只读数据库或运行离线验证获得的结果。
- **合理推断**：由代码路径可推导的风险，但没有完整浏览器或真实模型复现。
- **待验证问题**：缺少运行环境、原始记录或实验元数据，不能据此作出通过结论。

审计开始时 Git 工作区干净，所有分支历史仅发现上述一次提交；没有可用的逐步重构提交链。未发现适用的仓库 AGENTS.md。本轮仅新增本目录三份 Markdown，不修改代码、原始 JSON、历史分析输出、数据库或 Git 历史；不提交、不推送。Git 所有权检查使用命令级 `-c safe.directory=D:/Projects/AimCoach`，未修改全局配置。

已检查 README.md、ENGINEERING_EVIDENCE.md、全部当前 Python/JS/HTML/CSS 源文件、三个 analysis/test 脚本、旧分析器、分析输出、启动脚本、依赖清单和 Git 文件列表。README.pdf 作为既有二进制证据保留，未进行 PDF 内容和排版审计。未安装依赖、未启动真实 API、未发起 Qwen 请求、未进行浏览器实操或公网部署；因此不宣称本轮端到端通过。

## 仓库及职责

- `frontend/`：原生 HTML/CSS/JS，DOM 准星与目标、Pointer Lock、Session 采集、HTTP 上传和建议展示，无构建工具。
- `backend/main.py`：FastAPI 请求模型、完整性校验、保存/读取接口、Agent 接口、同源静态托管。
- `backend/session_store.py`：SQLite 元数据列与 raw JSON，按 ID / experiment 读取、选择最近保存实验。
- `analysis/session_validator.py`：上传完整性校验，当前并非完整的类型和语义契约。
- `analysis/analysis_service.py`：纯计算，Attempt 指标、Session 和 sensitivity 聚合、确定性 findings。
- `analysis/analysis_runtime.py`：连接 Storage 与 Analysis。
- `analysis/tools.py`：三个证据查询工具；实际每次调用都会读取最新实验并重新分析。
- `agent/experiment_planner.py`：Qwen OpenAI-compatible 客户端、工具 schema、白名单 dispatcher、显式模型循环。
- `analysis/old_files/`、`analysis/output/`、`data/experiment_*`：保留的旧链路和实验材料，不属于当前线上工具的数据源。
- `analysis/test/`、`agent/qwen_test.py`：依赖本地状态的手动 smoke 验证，不是完整自动化测试集。

## 当前数据流与前端行为

1. 点击训练区请求 `requestPointerLock({unadjustedMovement:true})`，失败时请求普通 Pointer Lock；fallback 后不能宣称输入仍是未加速原始输入。
2. `movementX/Y × trainerSensitivity` 更新虚拟准星，限制在当前训练区域内；每个 mousemove 记录相对时间、原始 delta 和虚拟坐标，同时更新 DOM。
3. 目标在区域中随机出现，radius 固定为 25 CSS px；Attempt 从出现到第一次几何命中结束，MISS 留在同一 Attempt 中。mousedown 未限定左键。
4. 当前代码 `targetCount=3`，三档 sensitivity 各重复三次，Fisher–Yates 随机排列，共 9 个 Session / 27 个 Attempt；不是 README 历史正式实验的 180 次。
5. Session 含 schemaVersion=0.1、UUID、浏览器本地 experiment 编号、ISO 起止时间、performance 时间、窗口尺寸、条件顺序、重复号和 attempts。
6. Session 完成即推进内存实验进度、退出 Pointer Lock，然后异步 POST `/sessions`；失败会尝试 JSON 下载。保存状态只写 console，没有持久化待上传队列、超时或用户可见确认。自动下载是否成功未验证。
7. Esc 中止会清空当前未完成 Session，条件不推进。页面刷新会重新随机排列并将进度归零，但 experiment 编号不变；多标签页共享计数器，进度不共享。
8. 用户输入 goal → POST `/agent/plan` → 显示 answer 和 tools_used；使用 textContent，模型文本不会被当成 HTML 执行。生成按钮有 loading/禁用状态，但请求没有取消/超时；开始训练没有反向检查规划是否正在执行。Agent 面板位于满屏训练区之后，需滚动；深色背景下部分文本未显式设浅色，视觉可读性待浏览器确认。
9. 建议为自由文本，不会自动改变下一轮固定 schedule。闭环是“数据到建议展示”，尚未实现“接受结构化计划并执行、再评价效果”。

## HTTP 契约

- `POST /sessions`：Pydantic 校验顶层，允许额外字段，attempts 为 list[dict]；随后 validator，再 INSERT。正常返回 `{session_id,saved}`；校验错误 422，主键冲突 409，保存异常 500。validator 在保存 try 块之外，嵌套类型异常可逃逸为服务器错误。
- `GET /sessions/{session_id}`：返回 raw JSON；不存在 404。读取异常没有专用业务错误映射。
- `POST /agent/plan`：goal 长度 1–1000（后端没有 trim；全空格仍可通过），响应 `{goal,answer,tools_used}`。没有 experiment_id 请求字段。planner 异常统一 502，响应构建在 try 块之外。
- `/` 及静态资源在 API 路由之后挂载，单 FastAPI Origin，当前不需要跨域配置。
- 没有历史列表、实验状态、独立分析、健康检查或结构化计划执行接口。

## 数据库结构与真实数据

已通过 SQLite URI `mode=ro` 查询实际数据库。仅有 sessions 表：session_id TEXT PRIMARY KEY；experiment_id、schema_version、started_at、trainer_sensitivity REAL、target_count INTEGER、raw_data TEXT 为 NOT NULL；ended_at、experiment_order INTEGER、sensitivity_repeat INTEGER 可空；saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP。除主键隐含索引外未发现显式索引、外键或迁移版本表。无范围 CHECK。SQL 使用参数化查询。

每次操作新建短生命周期连接；`with connection` 管事务，但代码没有显式 close，连接释放依赖对象回收，不宜视为严格关闭。没有显式 WAL 配置、数据库备份恢复流程或 experiment_id 索引。导入 main.py 即调用 init_database；缺少 Key 时 planner 导入先失败。

**当前本地 SQLite**：`data/aimcoach.db`，被 .gitignore 排除。只有 `exp_001`，10 个 Session / 32 次 Attempt。0.8：3 / 9；1.0：4 / 14；1.2：3 / 9（前者 Session 数，后者 Attempt 数）。targetCount 混合 3 和 5；尺寸包含 1270×1307 与 1997×1308。10 份均通过当前 validator，但通过不等于实验协议一致。不能将其当作 9×20 的正式数据集。

**已跟踪 JSON**：experiment_000 有 1 / 5，内部 experimentId=exp_001；experiment_001 有 9 / 27；experiment_002 有 9 / 180。后两个目录的 JSON 均缺少 experimentId、experimentOrder、sensitivityRepeat；文件名也含 rep-undefined / order-undefined。目录名不是有效的内部实验身份或随机顺序证明。

**历史输出**：旧分析器 schema 0.1 对 experiment_002 的纯内存重算与 `analysis/output/analysis_result.json` 解析后精确相等。当前服务 schema 0.2 要求 experimentId，不能直接消费这批旧 JSON；本轮只在内存副本补临时 ID 后做了交叉验证，没有写回或导入。

数据库前后 SHA-256 均为：`1c4a46be675fba110ee79e66aa3a01fd6fc7276401ca1968b153366ebffd1dbb`。

## 确定性分析

- Final Hit Time：最后 HIT 的 click.time − target.appearTime，单位 ms。
- Miss Count：click.hit 为假的次数。
- First-click Error：首 click 至 target center 距离，单位 CSS px。
- Path Efficiency：起点至最终 HIT 坐标直线距离 / 起点经 movements 至最终 click 的折线路径长度；零距离零路径返回 1。
- 每指标提供 sample_count、mean、median、样本标准差（单样本返回 0），另有含 MISS 的 Attempt 比例。
- 按 sensitivity 汇总 pooled Attempt 指标，同时保留 Session-level summary；当前同一 Attempt 指标在 Session 和 pooled 汇总中计算两次。
- 唯一 finding 规则：候选 sensitivity 的每个 Session path median 都低于其他条件的全部 Session median。没有最小重复数门槛，一个候选 Session 也可能产生 consistently_lower；不是显著性检验或泛化结论。
- dataset validation 检查字段、Attempt 数和唯一 experimentId，不检查协议、窗口、输入模式、重复 session ID、采样数或全部原始完整性。limit 文案固定，未从环境元数据推导。

历史 180 次数据可复算：1.2 的三个 Session path median 为约 0.82749、0.83192、0.74941，均低于其他条件最低约 0.86198；支持原有有限观察。pooled hit-time median：0.8≈851.6ms，1.0≈887.9ms，1.2≈861.9ms；这些中位数本身不能证明跨 Session 的稳定速度优势。

## Agent Runtime 与 Tool Dispatcher

模块导入读取 DASHSCOPE_API_KEY，缺少即抛 RuntimeError；OpenAI 客户端指向 DashScope compatible-mode/v1，MODEL 写死为 qwen3.8-flash。本轮仅确认配置文本，没有验证模型当前可用性或供应商兼容性。

每请求创建独立 messages（system + goal），没有跨请求记忆。最多 6 次 completion 调用，tool_choice=auto；无 tool_calls 时直接接受 content（允许空串）；有调用时先回填 assistant/tool_calls，再逐个 JSON 解析、白名单分派并回填 tool_call_id 的 tool 消息。最后一轮若仍调用工具，其结果不会再交给模型总结，随后抛超轮错误。六轮不是最多六个工具调用，同一轮可有多个调用。

白名单仅允许 summary、profile(sensitivity)、compare(sensitivities)，未使用 eval 或任意 Python 执行。JSON schema 只是发给模型的约定；服务端没有严格参数对象/类型/额外字段校验、工具错误恢复或输出证据门槛。未知工具 ValueError、缺参 KeyError、畸形 JSON 等会中断整次请求。

tools_used 在执行前追加名称，未记录参数、成功失败、耗时、token、模型版本、证据快照或最终建议引用。每个工具重新选择最近保存的实验并重算全部数据；请求期间上传可能使不同工具看到不同证据，属于代码路径支持的并发风险，尚未通过真实并发复现。

没有应用级 timeout、retry 策略、总时限、token/tool 数预算或取消；SDK 可能有自己的默认超时与重试，不能说完全没有。同步 FastAPI endpoint 在工作线程执行同步模型 I/O；不会直接等同于阻塞整个事件循环，但长请求会占用工作线程资源。

## 本轮验证结果与限制

使用 `python -B` 禁止 pyc，数据库连接临时在进程内替换成只读连接；未运行旧脚本写输出的 main。

- `node --check frontend/trainer.js`：通过，仅语法检查。
- `python -B -m pytest analysis/test --collect-only -q -p no:cacheprovider`：0 tests，退出码 5；文件内主要是 main / verify_*，不会被常规 pytest 自动执行。
- 经 runpy 执行 test_db_source：通过；test_analysis_service：Expected one sensitivity profile 断言失败；test_tools：预期 9 Session 断言失败。它们绑定可变的真实数据库，失败不能直接解释成指标算法错误。
- 10 份数据库记录均通过当前 validator；旧输出内存重算精确匹配。
- 内存负例：movement=None → AttributeError；target.x='bad' → validator=[]、计算 TypeError；最终 click 提前至 appearTime−1 → validator=[]、hit time=−1ms；将 HIT 坐标移至 1e9 → validator=[]。均未保存。
- 用进程内 Fake OpenAI 客户端验证：模型不调用工具即可返回 answer 且 tools_used=[]。这是 runtime 缺少强制证据门槛的事实，不是对真实 Qwen 行为的预测。
- 移除进程内 Key 后导入 planner → 明确的缺 Key RuntimeError；没联网。
- 当前解释器 D:/Anaconda/python.exe 可用，但未安装 fastapi；没有完整复现 README 的 Conda 环境。因此 API 状态码和浏览器交互以代码检查为主，不标记实测通过。

未见 CI、自动 API/Agent/前端测试、容器配置、迁移/恢复测试、发布脚本或部署证据。requirements 是大范围环境 freeze（含 notebook/debug 依赖），不是最小运行依赖；未验证各版本能否在全新环境安装。当前 run.ps1 绑定 127.0.0.1，是合适的本地边界。鉴权、多用户隔离、TLS、限流、持久磁盘和安全运维均未实现，不能据此宣称公网部署准备完成。
