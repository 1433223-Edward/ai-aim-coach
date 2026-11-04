# P0-1：Session 数据契约与回归验证

日期：2026-10-28。此文是基线审计之后的实现记录，不改写 BASELINE / DECISIONS 中的历史结论。本轮不涉及 Agent Runtime、前端 UI 或 Tools 的改造。

## 原因与修复边界

原 validator 主要检查字段存在和部分顺序，默认嵌套元素都是 dict，默认坐标/时间可计算；没有校验 click 是否位于目标圆内，也没约束事件发生于目标出现之后。API 顶层允许类型转换；Storage 默认调用者已经校验；Analysis 只校验实验 ID 和数量，形成三处信任缺口。

现在 API 使用严格顶层类型并调用共享 validator；save_session 在连接数据库前独立执行共享 guard；Analysis 的 validate_analysis_dataset 在计算指标前执行同一校验。已有数据库中的坏记录也不能直接进入公开分析入口 analyze_sessions / analyze_experiment。保存时使用严格 JSON 编码。SQLite 连接使用 closing 显式释放，保证事务结束后临时文件可以在 Windows 上清理。

这属于应用层契约，不是 SQLite 触发器或防篡改系统。直接修改数据库文件/执行 SQL 仍可能写入坏记录，但它们在后续分析入口会被校验。下层 calculate_* / build_* 是接受已验证数据的计算辅助函数，不单独承担完整 Session 校验。

## 现有模型的明确规则

- 顶层 Session、Attempt、target、movement、click 必须为对象；attempts、movements、clicks 必须为数组。允许额外 JSON 字段，不重命名、不修复或修改输入；整体必须可严格 JSON 编码，不接受 NaN / Infinity。
- schemaVersion、sessionId、experimentId 为非空字符串；不强制 UUID 格式或固定 schemaVersion 白名单。未知版本的迁移策略仍待确认，不声称任意未来版本兼容。
- targetCount 为正整数，不接受 bool、浮点数或数字字符串；与非空 attempts 长度一致。trainerSensitivity、trainerWidth、trainerHeight、target.radius 为有限正数，不限定 sensitivity 档位、targetCount=3/20 或 radius=25。
- experimentOrder、sensitivityRepeat 保持可缺失或 null；提供时必须为正整数。旧记录缺失这些字段不补造。
- 事件没有独立 type 枚举：movements 的成员要求 time/rawDX/rawDY/x/y；clicks 要求 time/x/y/hit；target 要求 x/y/radius/appearTime/disappearTime/cursorStartX/cursorStartY。数值为有限 int/float，bool 和数字字符串均不作为数值接受；raw delta 允许负数和零；hit 必须为真正的 bool。
- startTime、endTime、appearTime、disappearTime、事件 time 是非负 performance 时间（ms）。Session end ≥ start；Attempt appear ≥ Session start/前一个 disappear，disappear ≥ appear 且不超过 Session end；事件在 [appear, disappear] 内；各事件数组时间非递减。movement 不得晚于最终 click。允许相同时间戳，避免误伤浏览器时间精度限制。
- startedAt/endedAt 必须为包含 T 的可解析 ISO datetime，且 endedAt ≥ startedAt。保留两者同为无时区时间的兼容性；一有时区一无时区则拒绝。没有强制将墙钟时长与 performance 时长精确对应，它们是不同的时钟。
- clicks 非空；最后一个 click 必须 HIT，前面只能 MISS。所有 click.hit 与上报坐标的圆形几何判断一致：distance(click,target) ≤ radius（圆周计入命中）。不要求 final click.time 与 disappearTime 严格相等，因为原采集会分别调用 performance.now()。
- movements=[] 合法，例如目标在准星附近且无需移动；缺失 movements 不合法。首击命中、多次 MISS、零命中耗时均保留。

## 一致性与真实性

服务器能拒绝“坐标在目标外却标 HIT”“点击早于目标出现”“第一次 HIT 后还继续点击”等字段矛盾。服务器不能证明目标确实在浏览器显示、输入来自真实鼠标或用户真的点击；客户端可同时伪造 target、click、movement、时间等全部字段，使它们自洽。回归中有专门的测试明确展示这个信任边界，不能据此宣称消除了所有伪造可能。

以下规则没有擅自引入：坐标必须始终位于 Session 初始尺寸内（resize 策略未定）、用 rawDX×sensitivity 严格重建全部轨迹（输入模式/历史版本差异未定）、以点击坐标与最近 movement 坐标相等作为新要求、固定墙钟/performance 时长误差阈值、未知 schema 版本的拒绝策略、请求体/事件总数上限。它们需要独立协议决策。

## 历史兼容性

测试只读取已跟踪 JSON fixture 和保留的分析输出，不访问开发者真实 SQLite 数据库。19 份历史 JSON 的内容与字段完整性（含时间、几何）全部通过。

其中 18 份缺少 experimentId，仍不满足当前写入和分析所需的身份契约；这不是本轮新破坏。`require_experiment_id=False` 仅是显式历史检查选项，Storage 与 Analysis 不使用该选项。比较旧正式实验输出时，仅给测试内存副本设置明确的 test-only ID，未修改文件、未推断原始顺序、未导入真实数据库。180 次 Attempt 的 profiles、findings、limitations 与保留输出精确一致。

有意收紧的兼容性：曾经被强转的字符串数值、整数形式的 hit、bool 形式的数值，以及缺时间/尺寸、几何矛盾的记录现在会被拒绝。它们不是当前前端生成的合法类型；不对历史文件进行静默转换。原本已坏的记录需另行定位来源、隔离和人工决定处置，本轮没有清理或迁移。

## 验证方法

在包含项目 FastAPI/Pydantic 依赖的环境运行：

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B -m unittest discover -s tests -v
```

回归文件：tests/test_session_contract.py。使用 unittest 与直接 ASGI 请求，不要求 pytest/httpx、Key、模型 API 或网络。API 测试在导入 main 前将 DB_PATH 替换成 TemporaryDirectory 内的 DB，并隔离 planner 导入；所有数据库写入只发生于临时库。

覆盖：合法手算指标、首击/零移动/圆周/同时间戳、多次 MISS、历史数据与黄金输出；负命中时间、伪造 HIT、假 MISS、异常对象类型、空/缺字段、非有限数、类型强转、时序和计数错误；API 422 不写库、正常保存读取、409/404、直接 save_session 绕过 API、预存坏行在分析入口被拒绝。上传模型错误不回显原始坏值，避免含 NaN 的校验错误响应再次触发 JSON 编码异常。

本轮结果：15 个测试方法全部通过（包含参数化 subTest）；19 份历史 JSON 校验通过，180 次历史实验的 profiles/findings/limitations 精确匹配。`git diff --check` 通过，真实数据库前后 SHA-256 均为 `1c4a46be675fba110ee79e66aa3a01fd6fc7276401ca1968b153366ebffd1dbb`；数据库未用于测试。未 commit / push。

未运行依赖本地真实 DB 的旧 smoke 脚本，未将本次新回归的通过等同于整个项目测试已完成。真实浏览器采集及 Qwen 调用不在此次验收范围内。

## 剩余问题与下一轮最小范围

尚未解决实验编号/刷新进度、协议混合、每次 Tool 重新读取 latest 的一致性、Agent 证据门槛、缺 Key 启动、保存反馈和所有历史测试改造。建议下一轮只做“plan 请求绑定 experiment_id + 请求开始时固定 Session 集合/analysis snapshot”，让同一建议的工具使用同一份证据；前端持久化实验状态可再单独分步实施。
