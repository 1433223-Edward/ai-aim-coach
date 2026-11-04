# P0-1 Code Review：Session 上传与分析契约

日期：2026-10-28。面向维护者，范围为本次 P0-1 实现；报告编写阶段只新增此文，不再修改实现。Before 以当前 HEAD `86655a3` 为依据，After 以当前工作区为依据。片段省略错误文本和无关分支，不是可直接替换的完整函数。

结论：API、Storage 和公开 Analysis 入口已共享完整性校验，已复现的负命中时间、矛盾 HIT 和异常事件结构得到拦截；15 个离线测试方法通过。此结果不代表客户端行为已被认证，也不代表整个项目已完成端到端或生产验证。

## 1. Change Summary

### P0-1 修改的已跟踪文件

- `analysis/session_validator.py`：Validation 模块。将“存在性/部分顺序检查”补为结构、严格类型、有限数值、Session/Attempt/事件时间边界、HIT 几何和结束条件检查；新增 require_valid_session，保留 errors[] 接口和纯函数特性。
- `backend/main.py`：HTTP/API 模块。SessionUpload 开启 strict=True；为 /sessions 的 RequestValidationError 返回不含原始坏值的 422。原 create_session 的显式完整性校验继续使用，没有更改 Agent endpoint 逻辑。
- `backend/session_store.py`：Persistence 模块。save_session 连接数据库前独立校验，严格 JSON 编码；全部连接使用 closing 保证事务结束后关闭。没有改表、迁移、清库或修改已有记录。
- `analysis/analysis_service.py`：Deterministic Analysis 模块。validate_analysis_dataset 检查 dataset 容器并逐 Session 调用共享 validator；指标、聚合与 finding 算法未改。

### P0-1 新增文件

- `tests/test_session_contract.py`：Regression Tests。独立合成 fixture、历史 JSON 只读兼容检查、TemporaryDirectory DB、直接 ASGI API 测试；不需要真实 Key、模型请求、pytest 或 httpx。
- `docs/engineering-review/SESSION_CONTRACT.md`：Contract / Implementation Record。记录明确规则、兼容性、信任边界、验证命令与待确认项。

### 本次报告新增文件

- `docs/engineering-review/P0_1_CODE_REVIEW.md`：本报告。

### 上轮已存在，不能计入本次实现

- `docs/engineering-review/BASELINE.md`
- `docs/engineering-review/DECISIONS.md`
- `docs/engineering-review/IMPROVEMENT_PLAN.md`

这三份是上轮审计产物，本轮实现开始前即为未提交内容，本轮未修改。当前 Git 将其标为 untracked，不能仅凭当前 status 判断其产生时间；这里的归属根据对话中的任务起始状态与操作记录确认。前端、Agent Runtime、Tools、requirements、旧分析器、真实 JSON、旧输出和数据库不属于本轮修改。

## 2. Before / After

### 2.1 异常事件不再触发属性或比较异常

Before：假设 movement 一定是 dict，字段存在不代表类型合法；None 会在 .keys() 处抛 AttributeError，字符串与数字比较可抛 TypeError。

```python
missing = required_movement_fields - movement.keys()
movement_time = movement.get("time")
if previous_time is not None and movement_time is not None:
    if movement_time < previous_time:
        errors.append(...)
```

After：先判断容器，再验证字段；只有通过数值检查才进行比较。_is_number 明确排除 bool，并拒绝 NaN/Infinity。

```python
if not isinstance(event, dict):
    errors.append(f"{event_path} must be an object.")
    continue
valid = {key: number(event, key, event_path,
                     nonnegative=(key == "time")) for key in fields}
if valid["time"]:
    time = event["time"]
    # 再进行顺序和范围比较
```

新增严格 JSON 可编码检查覆盖额外字段里的非有限值；validator 不转换、修复或修改输入。规则统一适用于 target、movement、click 及顶层数据。

### 2.2 时间从“数组内部有序”补为“位于生命周期内”

Before：检查 disappear ≥ appear、部分 Session 起止顺序及各事件数组内部顺序，但没有把 click.time 与 target.appearTime 关联。单次点击时间提前，仍可能通过并得到负 final_hit_time_ms。

```python
if disappear_time < appear_time:
    errors.append(...)
# click.time 可以早于 appearTime
```

After：校验 Session → Attempt → Event 的范围，并检查 Attempt 之间不倒退、movement 不晚于最终 click。

```python
if appear is not None and time < appear:
    errors.append(f"{event_path}: time precedes appearTime.")
if disappear is not None and time > disappear:
    errors.append(f"{event_path}: time exceeds disappearTime.")
```

允许时间相等，不要求 click.time 等于 disappearTime，也不将两个不同时钟的时长强行设为精确相等。

### 2.3 HIT 从末尾标记补为几何和结束条件一致

Before：主要确认最后 click.hit is True；客户端在目标外填 True 可通过。前面是否已有 HIT 也没有限制。

```python
if clicks[-1].get("hit") is not True:
    errors.append(f"{prefix}: final click is not HIT.")
```

After：hit 必须是 bool；首个 HIT 必须是最后一次点击；所有点击的标记都必须与上报几何一致，因此也会拒绝“圆内标 MISS”。

```python
if type(hit) is not bool:
    errors.append(...)
elif hit and event_index != len(events):
    errors.append(f"{event_path}: HIT must end the attempt.")
# 实现中还先验证坐标/radius，以及距离是否有限
if hit != (distance <= target["radius"]):
    errors.append(f"{event_path}: hit disagrees with target geometry.")
```

圆周仍算命中，与原前端 distance <= targetRadius 一致。无需移动的合法 Attempt 继续允许 movements=[]。

### 2.4 API 不再隐式转换顶层类型

Before：Pydantic 默认宽松转换，例如字符串数值或整数形式的计数可能被转换后进入后续校验。

```python
model_config = ConfigDict(populate_by_name=True, extra="allow")
```

After：保留 alias 和额外字段，但使用严格类型；嵌套 raw JSON 由共享 validator 负责。

```python
model_config = ConfigDict(
    populate_by_name=True,
    extra="allow",
    strict=True,
)
```

附加的 upload_validation_error 只对 /sessions 使用 loc/msg/type 构造 422，不回显 input/ctx。原因是非法值如 NaN 若被包含在校验错误响应中，错误响应本身也可能无法编码。其他路由继续使用原 FastAPI handler。

### 2.5 Storage 不再只信任 HTTP 调用者

Before：直接 save_session 会跳过 HTTP validator；json.dumps 默认允许 NaN/Infinity。

```python
raw_data = json.dumps(session, ensure_ascii=False)
with _connect() as connection:
    connection.execute("INSERT INTO sessions ...", ...)
```

After：所有使用该写入入口的调用者都必须满足契约，失败发生在连接数据库之前。

```python
require_valid_session(session)
raw_data = json.dumps(session, ensure_ascii=False, allow_nan=False)
with closing(_connect()) as connection, connection:
    connection.execute("INSERT INTO sessions ...", ...)
```

with connection 只处理 commit/rollback，并不会关闭连接；closing 在事务退出后关闭连接。这项改动来自 Windows 临时数据库清理时实际出现的文件占用错误。首次回归的清理错误已修复，未通过忽略清理异常掩盖它。

### 2.6 Analysis 不再假设数据已由上传入口验证

Before：检查字段是否存在、Attempt 数量及唯一 experimentId，但可能直接计算已有坏数据。

```python
missing_fields = required_fields - session.keys()
actual_count = len(session["attempts"])
```

After：在上述 dataset 检查前先调用完整 validator，将错误聚合为 ValueError；从文件、数据库或直接参数进入公开分析入口都受到保护。

```python
integrity_errors = validate_session_integrity(session)
if integrity_errors:
    problems.extend(f"Session {index}: {error}" for error in integrity_errors)
    continue
```

四指标公式、统计聚合、finding 规则及输出 schema 没有改变。

## 3. Engineering Reasoning

### 为什么复用 validator 而不是仅扩展 API schema

API 不是唯一调用入口：脚本可以直接调用 save_session，Analysis 还可以读取历史数据库或接收 list[dict]。只修请求模型不能保护这些路径。共享纯 Python 校验使 Storage/Analysis 不依赖 FastAPI/Pydantic，并减少三套语义规则发生漂移。

分层重复校验是有意的：HTTP 层提供可定位的 422，Storage 保护直接调用，Analysis 保护已有数据。代价是上传时重复遍历和 JSON 可编码检查，本轮没有为优化性能而削弱边界；大数据量的开销尚未 benchmark。

### 为什么不“修复”非法输入

自动把字符串转数值、把圆外 HIT 改为 MISS 或补造时间，会改变原始事实，且可能让损坏记录继续生成误导性统计。当前选择拒绝并返回错误，不修改原始 payload 或真实记录。

### 为什么保留历史检查选项

18 份历史 JSON 缺 experimentId，但事件内容仍能独立检查。require_experiment_id=False 只用于显式历史检查；正常 Storage/Analysis 不启用。黄金输出对比仅在测试内存副本放置 test-only ID，不恢复或猜测历史身份。

### 解决与未解决的边界

已解决：非法容器/字段类型导致的常见崩溃，负命中时间、矛盾 HIT、错误结束顺序，以及通过应用写入函数绕过 HTTP 校验的问题。

未解决：

- 真实性：客户端可同时伪造一整份自洽数据；几何一致性不是实际鼠标点击的认证。专门测试保留了这个边界。
- 数据库篡改：没有添加 SQL trigger 或防篡改机制；直接 SQL 仍能写坏数据，但公开分析入口会拒绝它。
- 内部辅助函数：calculate_* / build_* 假定输入已验证，不是完整 Session 验证入口。
- 协议未知：resize 后坐标边界、raw delta 重建轨迹、点击与最近 movement 的坐标相等、新 schema 版本策略、时钟差容限和输入规模上限未擅自规定。
- 数值极端：拒绝非有限输入不等于证明所有极端有限值的聚合运算都不会溢出；该类数值压力测试本轮未覆盖。
- 其他 P0：experiment 状态、单次规划快照、Agent 取证门槛、Key 启动依赖、前端保存反馈均不在本次实现范围。

## 4. Verification

### 实际执行命令与结果

实现阶段最终执行：

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B -m unittest discover -s tests -v
```

结果：`Ran 15 tests in 7.968s`，`OK`。15 是测试方法数量；负例和历史文件检查另含 subTest，不应把它说成只有 15 个输入样例。报告阶段未重复运行已经通过且未变更的测试。

实现阶段和报告阶段均执行：

```powershell
git -c safe.directory=D:/Projects/AimCoach diff --check
Get-FileHash data/aimcoach.db -Algorithm SHA256
```

diff --check 通过。数据库 SHA-256 保持为 `1c4a46be675fba110ee79e66aa3a01fd6fc7276401ca1968b153366ebffd1dbb`。hash 检查只读文件字节，真实数据库不作为测试数据库使用。

### 每个测试验证什么

以下方法均在 tests/test_session_contract.py，均已通过：

ContractTests：

1. test_hand_calculated_metrics：合法合成样例；hit time=20ms、miss=1、first-click error=25px、path efficiency=1，并检查输入不被修改。
2. test_all_negative_cases_rejected_by_validator_and_analysis：负命中时间、矛盾 HIT/假 MISS、异常元素类型、缺字段、空数组、数值类型、非有限值、计数和时间错误均被 validator 与 analyze_sessions 拒绝。
3. test_invalid_root_types_are_controlled_errors：Session 根类型和 dataset 容器错误得到受控错误，而不是属性异常。
4. test_zero_movement_equal_timestamps_and_circle_boundary：空轨迹、首击命中、圆周命中与相同时间戳合法，零路径/零耗时语义不变。
5. test_multiple_misses_are_valid：同一 Attempt 多次 MISS 合法，miss_count 正确。
6. test_self_consistent_client_data_is_not_proof_of_real_actions：整份数据可以由客户端一致地改写并通过，明确校验不提供行为真实性证明。
7. test_attempt_ordering_and_mixed_experiments：Attempt 重叠/倒序和跨 experiment 混合分析被拒绝。
8. test_all_versioned_historical_json_without_mutation：19 份历史 JSON 的字段/时间/几何检查通过；缺身份文件仍被正常入口拒绝；内存数据和文件均未修改。
9. test_historical_analysis_matches_retained_golden_output：测试内存副本补 test-only ID 后，180 次历史实验的 profiles、findings、limitations 与保留输出精确一致；不是整个输出对象相等，因为当前 schema/experiment metadata 不同。

StorageTests：

10. test_valid_round_trip_and_duplicate_preserves_original：临时库正常保存/读取，重复 ID 抛 IntegrityError，行数仍为 1。
11. test_direct_storage_rejects_every_negative_case_before_connecting：直接写入函数拒绝全部负例；mock 确认 _connect 未被调用，临时库行数保持 0。
12. test_corrupt_existing_row_cannot_enter_analysis：仅在临时库通过 SQL 制造一条已有坏记录，analyze_experiment 拒绝；不删除该行、不修复原始数据。

ApiTests：

13. test_api_rejects_all_negative_cases_without_writes：对真实 FastAPI ASGI app 发合成请求；全部负例返回 422 且临时库无新增。
14. test_api_round_trip_duplicate_and_not_found：POST 200、GET 200 与 attempts 一致、重复 POST 409、不存在 GET 404，最终只有一行。
15. test_api_rejects_bad_root：数组、字符串、数字、bool 等非 Session 对象返回 422，临时库无写入。

### 隔离方式与未运行项

TemporaryDatabaseCase 在 init_database 前 patch DB_PATH 到 TemporaryDirectory。ApiTests 在导入 main 前隔离 planner 模块，避免缺 Key 或真实模型调用，导入时 DB 初始化同样只作用于临时路径。asgi_request 直接调用真实 app 的 ASGI 接口，覆盖路由、Pydantic、错误响应和 Storage，但不覆盖真实 socket、Uvicorn 或浏览器。

未运行：依赖开发者真实 DB 的 analysis/test 旧 smoke 脚本；全仓 pytest；真实 Qwen/Agent 回归；浏览器 Pointer Lock/采集端到端；负载、输入规模/极端数值压力测试；部署与备份恢复；全新环境安装测试。未安装新依赖。不能把本轮通过表述为“全仓测试全绿”。

## 5. Review Guide

建议按以下顺序亲自阅读，共五处（行号以本报告生成时工作区为准）：

1. [session_validator.py:37](D:/Projects/AimCoach/analysis/session_validator.py:37) — validate_session_integrity。先看顶层和 number 的类型判定，再看时间边界与 click 几何；最后看 :209 的 require_valid_session。重点确认规则来自现有语义，以及历史检查选项不会被写入/分析启用。
2. [main.py:124](D:/Projects/AimCoach/backend/main.py:124) — SessionUpload，随后看 :259 的 create_session 与 :74 的 upload_validation_error。重点看 strict 类型、422 两类错误路径与错误响应不回显原始输入。
3. [session_store.py:92](D:/Projects/AimCoach/backend/session_store.py:92) — save_session。确认 guard 在 _connect 之前，INSERT 仍为参数化，with 的退出顺序是事务后关闭连接。
4. [analysis_service.py:48](D:/Projects/AimCoach/analysis/analysis_service.py:48) — validate_analysis_dataset。确认非法 Session 在任何指标计算前被拒绝，同 experiment 检查继续保留，指标公式不在 diff 中。
5. [test_session_contract.py:53](D:/Projects/AimCoach/tests/test_session_contract.py:53) — invalid_sessions，然后读 :214 的 TemporaryDatabaseCase、:235 的直接写入拦截、:276 的 ApiTests。重点确认负例独立、断言落库副作用、patch 发生在初始化之前，以及真实性测试不被误解为漏洞已完全消除。

## 6. Git Diff 与变更归属

在 PowerShell 中：

```powershell
Set-Location D:/Projects/AimCoach

# 总览：包含此前已有 untracked 审计文档，不等于本轮成果清单
git -c safe.directory=D:/Projects/AimCoach status --short --untracked-files=all

# 本轮四个已跟踪实现文件；当前均是 unstaged 修改
git -c safe.directory=D:/Projects/AimCoach diff --stat -- analysis/session_validator.py backend/main.py backend/session_store.py analysis/analysis_service.py
git -c safe.directory=D:/Projects/AimCoach diff -- analysis/session_validator.py backend/main.py backend/session_store.py analysis/analysis_service.py

# 需要对比 HEAD（也涵盖后续可能 staged 的修改）时
git -c safe.directory=D:/Projects/AimCoach diff HEAD -- analysis/session_validator.py backend/main.py backend/session_store.py analysis/analysis_service.py

# untracked 文件不会出现在普通 git diff 中；单独读取本次新增文件
Get-Content tests/test_session_contract.py
Get-Content docs/engineering-review/SESSION_CONTRACT.md
Get-Content docs/engineering-review/P0_1_CODE_REVIEW.md

# 如需以完整新增 diff 展示测试（Git for Windows 支持 /dev/null）
git -c safe.directory=D:/Projects/AimCoach diff --no-index -- /dev/null tests/test_session_contract.py
git -c safe.directory=D:/Projects/AimCoach diff --no-index -- /dev/null docs/engineering-review/SESSION_CONTRACT.md

# 空白错误检查
git -c safe.directory=D:/Projects/AimCoach diff --check
```

普通 diff 的统计为四个实现文件、214 additions / 305 deletions，不包含 untracked 的测试与文档。新增文件的 no-index diff 返回码 1 表示发现差异，是正常结果，不是测试失败。

safe.directory 使用命令级配置，不修改全局 Git 设置。所有命令均不执行 add/commit/push，也不重写历史。因为没有本轮起始 snapshot commit，Git 单独无法证明任意工作区改动归属；本报告将任务开始前已存在的三份审计文档明确排除，不应使用整个 untracked docs 目录作为本轮实现成果。
