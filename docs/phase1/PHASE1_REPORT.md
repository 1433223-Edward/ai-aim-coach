# Phase 1：Experiment Settings & Persistence

实现与自动回归已完成，等待 Phase 1 人工验收。真实物理鼠标完整 E2E 尚未通过工具环境验证，不能将下面的模拟输入集成结果称为物理训练通过。

## 1. 原始保存问题

没有复现“合法完整 Session 普遍无法写入 SQLite”的故障：原来已有 Pydantic/共享校验、事务、相同 ID 同内容幂等和匹配 ID 的 ACK。本轮确认的缺口是：Esc 中止直接丢弃未完成 Session；缺少暂停/限时归档、完整设置快照和历史列表/详情。现在完整与未完成分流保存，进度只计算服务端确认的完整 Session。

## 2. 主要改动

- `backend/settings.py`：服务端统一配置边界；前端 `frontend/settings.js` 提供镜像 defaults、验证、本地偏好和视觉/音效。默认值一致性有回归断言，服务端仍是最终校验方。
- `backend/session_store.py`：冻结新实验配置、设置快照校验、未完成归档、结束实验、历史查询/统计。旧 Session 原始 JSON 不重写；旧三字段设置及旧 pending 没有新快照字段时仍兼容。
- `backend/main.py`：复用现有实验创建/详情，新增归档、结束和历史 API，参数/错误受控处理。
- `analysis/session_validator.py`：新 Attempt 可带唯一 `attemptId`；旧记录无 ID 仍合法。仅归档入口允许不足额 Attempts，完整上传和 Analysis 仍拒绝 incomplete 状态。
- `frontend/trainer.js`：冻结配置应用、倒计时、P 暂停/继续、E 提前结束、活动时间限额、刷新退出时同步入可靠队列。暂停期间完成过的 Attempts 保留；正在进行的 Attempt 存入 pausedAttempts，继续时重做，避免把暂停时间算成命中时间。
- `frontend/index.html`、`style.css`、`training_ui.js`：齿轮 Settings、生命周期按钮、动态协议文案、PAUSED/COUNTDOWN/ENDED 状态。训练表面仍为原来的 viewport 尺寸，目标半径仍固定 25px。
- `scripts/seed_demo.py`：兼容新增空表，但有归档/结束数据仍拒绝重新 seed，保留真实/非 Demo 数据保护。
- `tests/frontend_harness.cjs`：从原测试抽出共用 harness，保留原断言；新增 Phase 1 Python/Node 测试和真实持久化集成脚本，`scripts/run_checks.py` 纳入回归。

没有重写 Agent Runtime、修改四项分析公式、增加 Tools 或训练任务。Git 中此前已有的 P0/前端重构修改不计作本轮新成果；没有 commit/push。

## 3. Settings 默认值与语义

- Sessions per Experiment：默认 9，范围 1–30；Attempts per Session：默认 3，范围 1–100。
- 内部指针倍率：默认 1，范围 0.1–5；不等价于 FPS 游戏灵敏度。
- 每 Session 时间上限：默认 0（不限），范围 0–3600 秒，按活动训练时间计算，不包含暂停；开始倒计时默认 0，范围 0–10 秒。
- Crosshair：Circle 默认，另有 Cross/Dot；颜色默认 #ffffff，大小默认 16px（4–40），粗细默认 2px（1–8）；Dot 是实心点，主要由大小决定外观。
- Target：默认 #e74c3c；背景默认 dark，另有 slate/midnight。颜色必须为六位 HEX。
- 命中音效、命中视觉反馈、未命中视觉反馈：默认关闭。音效使用浏览器合成短音，无外部资源；视觉反馈尊重 reduced-motion。

偏好存 localStorage 的 aimcoachSettings。保存偏好只影响下次新实验，不改变当前实验。原来没有显式偏好的三档九轮协议仍保留；保存设置后的新实验使用所选单一倍率，重复所选 Session 数。场景/难度参数仍属于 protocol（固定二维 Flick、25px 目标半径、实验固定窗口），没有把颜色或准心大小冒充新指标。

创建时把配置写入现有 protocol JSON，新增协议带 settings_contract_version=1；新 Session 带 settingsSnapshot。服务端校验 Snapshot 与协议一致，防止只依赖浏览器状态。旧协议不会被升级重写。

暂停不推进条件；Esc、E、限时到达及页面退出产生 incomplete 记录。退出时将固定 payload 同步入原 pending 队列，恢复后走相同 ID 的重试/ACK。浏览器若取消导航，该 Session 已停止，避免队列中的 ID 继续被修改。强制崩溃、浏览器清理站点数据仍不保证保存尚未完成的内存采集。

“结束当前实验”与“提前结束 Session”分开：前者仅在退出采集且 pending 已确认后释放 active_slot，保留原 protocol、schedule 和所有历史。状态保留 incomplete/complete，再加 ended 标记，不把少量样本冒充实验已完成。

## 4. 数据库迁移与回滚

有最小增量迁移：启动 init_database 通过 CREATE TABLE IF NOT EXISTS 新增 `incomplete_sessions` 和 `experiment_endings`，不删除/修改原 sessions、experiments 列及既有 raw_data。迁移可重复执行。

- incomplete_sessions：原始中断报告、Session/Experiment 关联、开始和保存时间；ID 幂等，跨完整/未完成表的 ID 冲突拒绝。完整 Attempts 仍验证；尚未命中的原始片段仅保留证据，不当成可分析完整样本。
- experiment_endings：独立结束元数据，避免修改冻结协议。
- Analysis/Agent 仍只读取原完整 sessions 表；未完成记录统计为 null，并明确说明排除原因。

本轮没有在真实库执行迁移；测试均在临时库。真实库前后 SHA256 相同：`bb3170ea182714a672f4896d2c7ad52426826af04bd62c1c4f49b6aa324e1ed1`。

正式启动前可用既有 `scripts/backup_database.py` 将当前库备份到不存在的新文件。回滚时先停服务，保留迁移后的完整库，不能 DROP 归档表来回退；旧客户端不认识 ended/归档语义，不建议继续写新库。如必须恢复旧版本，使用迁移前备份复制出的新路径并保留新库全部数据，不覆盖原库。只读检查可以保留新增表。

## 5. 历史 API

- `GET /history/experiments`：包含空实验、旧库实验汇总、完整/未完成数量；支持 date、scene、limit、offset。
- `GET /history/experiments/{experiment_id}`：协议/状态与关联记录；旧实验明确 protocol_verified=false，不猜测其未知 schedule。
- `GET /history/sessions`：支持 experiment_id、date、scene、limit、offset；区分 complete/incomplete。limit 1–500，offset 0–1000000。
- `GET /history/sessions/{session_id}`：原始 Attempts、逐 Attempt 指标与原 Session 统计；归档记录不生成完整统计。旧 raw 缺 experimentId 时只在分析副本中使用 SQL 的原关联，并返回 derived_fields，原始 JSON 保持不变。
- `POST /sessions/incomplete`：中断报告归档，ACK 仍要求 saved=true 且 ID 一致。
- `POST /experiments/{experiment_id}/end`：幂等结束，不删除历史。
- 原 `POST /experiments`、`GET /experiments/{id}`、`POST/GET /sessions` 与确定性分析 API 复用。

当前只有 flick_2d 场景；未知场景返回空列表，不新增任务。日期按已存开始日期/实验创建日期筛选，不替历史缺失时区作推断。查询使用参数化 SQL。

## 6. 实际验证

实际执行：

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B scripts/run_checks.py
node --experimental-vm-modules scripts/check_phase1_persistence.cjs
git -c safe.directory=D:/Projects/AimCoach diff --check
Get-FileHash data/aimcoach.db -Algorithm SHA256
```

结果：**71 Python、43 Node 全通过**；全部前端模块语法检查通过。原 Agent fake、历史黄金与契约回归保留。日志为 [regression.log](regression.log)、[persistence.log](persistence.log)。

真实持久化集成运行的是仓库实际 ESM 射击处理器，使用模拟 DOM/输入，但 HTTP、FastAPI/Pydantic、SQLite 事务/磁盘和子进程重启都是真实的：写入 1 个完整 Session、1 个未完成报告；验证两个唯一 Attempt ID、相同请求 200 且不增行、冲突 409、正确 ACK 后推进、暂停继续、归档排除分析、详情统计、过滤错误 422、结束实验、新空实验与重启恢复。未使用真实库和模型 Key。

另在真实 IAB 浏览器、8016 隔离服务执行 Settings 保存 → 结束原实验 → 新实验创建 → 刷新 → API 回读：恢复为 2 Sessions/2 Attempts/1.2 倍率、Cross、Slate；原 9 轮实验保留。证据见 [browser-persistence.json](browser-persistence.json) 和 [设置截图](settings-browser.jpg)。这是实际浏览器 Experiment 持久化验证。

真实浏览器 Pointer Lock 仍失败，所以**没有完成物理鼠标完整 Session E2E**；不能把模拟输入集成称作这一项通过。真实模型、实际音频听感、强制崩溃恢复、跨浏览器和读屏器也未验证。

过程中发现并修复：新增表导致 Demo seed 的未知表保护拒绝重复运行（只调整已知空表白名单，仍拒绝真实数据）；DOM harness 缺 dataset（补足 DOM 模型，未降低断言）；旧三字段配置/旧 pending 的兼容缺口；不完整状态尝试走完整上传入口的漏洞。sandbox 的临时目录/套接字限制通过获准的外部隔离测试验证，没有改用真实库制造通过。

## 7. 人工验收与已知限制

先重启后端，再刷新 HTTP Test 页面；旧进程不会自动加载新 API。默认 `.\run.ps1` 后访问 `http://127.0.0.1:8000/#/test`；若沿用 8012，重启原命令中的同端口服务。不要直接用 file:// 打开 HTML。

建议用独立 Demo 库：先运行 `python -B -m scripts.seed_demo --db "$env:TEMP\aimcoach-phase1-acceptance.db"`（该路径不要预先设为配置库），再设置 `$env:AIMCOACH_DB_PATH` 指向它并启动。已有非 Demo 路径被拒绝时换新文件名，不删除训练库。

1. 在 Settings 保存边界值与视觉偏好；刷新确认保留，当前 protocol 不变。
2. 显式结束当前实验，点击训练区创建新实验，确认数量、倍率和 API protocol.settings 一致；旧历史仍可查。
3. 用支持 Pointer Lock 的 Chrome/Edge：倒计时期间不采集，左键完成；P 暂停/继续，当前未完成 Attempt 重做；E/Esc 及限时结束归档，进度不虚增。
4. 断网完成或结束 Session，确认 pending/错误反馈；刷新恢复网络后重试，相同 ID 只有一条记录。只有服务端 ACK 才显示确认保存。
5. 通过历史 API 检查 complete/incomplete、日期/场景/实验过滤及统计，再重启服务核对。

限制：没有账号系统或新的历史可视化页面；暂停不会跨重启恢复为可继续运行的采集，刷新后作为未完成记录归档；浏览器强制崩溃可能不触发 beforeunload；音频受浏览器/系统策略影响；同一条件的跨标签页完整保存冲突仍保留 pending 并要求导出核对，不自动丢弃。服务端验证字段一致性与冻结配置，不证明客户端真实动作或计时真实性，不保证训练改善。

建议审查：[配置边界](D:/Projects/AimCoach/backend/settings.py:6)、[协议校验](D:/Projects/AimCoach/backend/session_store.py:246)、[归档服务](D:/Projects/AimCoach/backend/session_store.py:482)、[生命周期](D:/Projects/AimCoach/frontend/trainer.js:65)、[真实集成脚本](D:/Projects/AimCoach/scripts/check_phase1_persistence.cjs:1)。

本轮停止在 Phase 1 人工验收，不继续新增训练任务或 Agent 功能。
