# Phase 4 — UI/UX Redesign & End-to-End Integration

2026-11-05。实现与自动回归完成，交付人工验收。项目仍为本地单用户二维训练 Demo。没有迁移框架、重写 Agent Runtime、改写基础评分、增加3D、账号或排行榜；没有实际付费模型调用，没有 commit / push。

## 1. 信息架构与导航

- Home：项目说明，主入口改为 Training Hub。
- `#/hub` — Training Hub：六个任务按 Clicking / Switching / Tracking 分组；独立卡片显示目标、描述、难度、有效次数、最近成绩及最近条件内的最佳成绩。卡片可进入 Arena 或查看任务说明与趋势；原 Flick 保留独立兼容入口。
- `#/arena` — Aim Arena：实际瞄准；旧 `#/test` 为兼容入口。READY 检查任务参数后显式开始，ACTIVE 只保留最小 HUD 与训练区域。
- `#/coach` — AI Coach：历史概览、目标/约束、推荐依据及 Session IDs、接受/拒绝/重新推荐、计划进度和效果；旧 Flick Insights 放到此处作为兼容入口。
- `#/history` — History / Analytics：场景/日期/Experiment ID 查询、Experiment/Session 列表、详情、同条件趋势和归一化能力维度。

保留一个已挂载的训练控制器和坐标区域；管理页面将坐标区域停放到屏幕外、inert、aria-hidden，同时保持与 Arena 相同的尺寸，供计划请求/冻结协议测量。没有创建第二套训练或计时状态。Home 不初始化训练请求，首次 Hub 恢复也不再自动创建旧 Flick 实验；Experiment 由明确开始或计划接受创建。

## 2. 拥挤问题与 Arena 布局

Before：原坐标区域本身为 `100vw × 100vh`，前面还有设置、实验摘要、场景参数、完整计划等大块内容；整个 Test 页面远大于一屏。

After：管理组件保留原 ID 和事件绑定，移到 Hub / Coach / History；Arena 只挂载 HUD、同一个 trainer-area、底栏及非 ACTIVE 状态的覆盖层。桌面 grid 的整页为100dvh，上下各64px，坐标区域分配剩余高度。

```css
.arena-page {
    height: 100dvh;
    grid-template-rows: 64px minmax(0, 1fr) 64px;
}
.arena-page #trainer-area {
    width: 100%; height: 100%; border: 0; padding: 0;
}
```

没有用 body overflow:hidden 切掉按钮，没有对坐标区域做 CSS scale/transform，也没有通过缩小所有字体解决拥挤。READY / ACTIVE / PAUSED / Summary 使用相同区域尺寸；新 Experiment 冻结实际 clientWidth/clientHeight。瞄准坐标与显示位置使用同一 surface，原 target_at、命中几何和 scoring 模块未修改。

旧实验的完整 viewport 协议可能与新 HUD 后的 surface 不同。服务器仍拒绝尺寸不匹配，不缩放旧证据。用户需恢复能容纳旧协议的尺寸，或在 Hub 显式结束旧实验并新建；原记录保留。这是本轮必须说明的兼容性变化。

## 3. Focus Mode 与生命周期

- READY：任务、难度、目标数/跟随时长、开始按钮；不自动开练。
- COUNTDOWN / 启动准备：复用原启动与倒计时状态，禁止重复开始和页面切走。
- ACTIVE：只显示任务名、Session/命中或跟随时长进度、剩余时间/有效耗时、暂停与退出；导航、设置入口隐藏，AI/历史/图表不占训练区域。
- PAUSED：停止有效计时和跟随采样，显示继续、Settings；修改的偏好仅用于下次新实验，原快照不变。
- SAVING：明确等待数据库 ACK，不把完成动作等同保存成功。
- COMPLETED：收到保存确认后展示独立 Summary，包含场景/难度/版本、综合分、适用核心指标、优势/薄弱项和同条件历史；完整原评分证据可展开。
- ABORTED / ERROR：区分未完成、开始冲突、未确认保存；提供重试/导出/返回，查询失败不会把旧图表改名当成本次结果。

P 暂停/继续；Arena 的 Esc 暂停；E 进入退出确认。退出先暂停，再由页面内 `<dialog>` 确认；取消保持暂停，确认才提交 incomplete。没有使用同步 window.confirm 作为最终实现。Settings/确认框内编辑不触发训练快捷键。

浏览器 blur / visibilitychange 暂停有效训练，旧 Flick 倒计时在后台取消；窗口改变坐标尺寸则归档未完成，防止混合坐标系。原 Flick 暂停后仍按既有设计重做当前未完成 Attempt；新场景保留该目标的已记录点击，暂停间隔不计入场景时钟。没有重新定义评分时长或指标。

计划执行仍由 Experiment protocol.training_plan_id 关联，Session 身份/顺序继续由服务器验证。已完成计划的再次练习入口返回 Hub；直接再次开始会明确提示创建独立自由训练，不伪装成原计划的后续 Session。

## 4. Settings 与轻量设计规范

Settings 分为 Controls、Crosshair & Appearance、Experiment 三组；保留全部现有字段。原 Flick 的灵敏度/倒计时/Session 参数与新场景的普通指针和场景参数明确区分。

- 右侧 Drawer 使用独立滚动与 overscroll containment，正常输入/按钮最小44px点击区域。
- 标签、输入和合法范围留出固定间距，统一深色边框、背景、圆角及焦点提示。
- 保存、恢复默认值、关闭均有反馈；恢复默认只编辑表单，保存后才持久化。
- 当前参数不可被偏好修改偷偷改写；纯视觉偏好也按原有冻结设计下次生效。
- 修复必要的整合缺口：Phase 2 自由场景创建原先没有携带用户视觉偏好；现在 ScenarioStart 接受可选 preferences，并通过原 ExperimentSettings 校验后冻结。场景拥有的 Sessions/Attempts/倍率/时钟仍由服务端覆盖为场景协议，后续偏好改变不能修改活动实验。旧客户端不带该字段仍兼容。
- 新场景复用已有 shotFeedback，按冻结的音效/命中/MISS开关执行；不改变 hit 判定或轨迹数据。

Typography、8/16/24/32/48间距、Primary/Secondary/Danger/Icon语义、卡片/表单/模态框边界统一在 workspace.css。保留暗蓝灰底、清晰的高对比目标和准心，不添加粒子/复杂运动装饰。

## 5. 数据可视化与数据边界

所有数值来自真实 API。无历史、未评分旧记录、不可用指标和不足样本均有明确状态，没有固定假数据补图。

- Hub 卡片：实际有效训练次数、最新成绩、最新完整条件内最高分；不同条件的最高分不会冒充同条件最佳。
- 任务详情：最近条件的综合分/适用指标趋势，进一步条件选择进入 History。
- History：综合分、Accuracy、平均命中ms、TPS、Tracking Coverage/Error、Stability 的 SVG 趋势；单点明确标记趋势不足，提供文字数值列表和可访问图像说明。
- 能力维度：所选条件最近一次服务器 normalized_components，使用0–1进度条，不重新计算技能总分，不作跨任务排名。
- Coach：实际计划完成率进度条、目标指标变化或证据不足、前后样本数量及后续建议；不宣称 AI 带来因果提升。

分组使用 scenario_id、difficulty、scoring_version 和服务器完整 condition_key；标签额外显示 seed、时长、目标数、速度/方向、viewport。图表只使用历史查询返回的 Session IDs，避免日期/Experiment 过滤后仍展示无关成绩。请求失败清除旧图表，不重新贴标签复用旧证据。

进入 Arena 时暂停历史刷新，作废在途视图请求，避免 ACTIVE 期间构造复杂图表或让迟到结果覆盖新筛选。

只读接口增量：GET `/training-plans?limit=1..50`（默认20，省略 baseline_records 大载荷）；GET `/scenarios/sessions` 补充真实参数/viewport/时间，坏行标 abnormal 而不拖垮全部记录。沿用原 history 接口、计划详情与评估 API，没有本轮数据库结构重建。

当前边界：场景概览最多500条、历史 Session/Experiment 列表每次最多100条、计划最多20条；超出需缩小日期/场景/ID范围。Session详情可按明确ID读取，未开发无限滚动、回放或在线排名。

## 6. 修改文件与职责

- `frontend/workspace.js`：把原组件挂到管理区，观察现有控制器状态、Focus/Summary、暂停/退出确认、统一入口；不拥有独立训练时钟。
- `frontend/workspace.css`：设计规范、Arena固定空间分配、设置Drawer、管理卡片/图表及窄屏降级。
- `frontend/history_views.js`：有界读取真实 API、同条件分组、卡片/趋势/能力维度/计划进度、失败及过期请求处理。
- `frontend/app.js`、`frontend/navigation.js`、`frontend/index.html`：四区导航、单次控制器初始化和既有组件复用。
- `frontend/scenario_ui.js`：只读状态出口、失焦暂停、退出快捷键衔接、明确开始失败、ACK后的状态和偏好反馈整合。
- `frontend/trainer.js`：旧模式的状态投影、Phase 4 Hub只读恢复、Arena Esc/失焦/resize防护；保留原训练算法与API兼容路径。
- `frontend/settings.js`、`frontend/training_ui.js`：默认值、范围提示、暂停时设置可访问性；`frontend/training_plans.js`：向工作区通知实际计划变化。
- `backend/main.py`、`backend/training_plans.py`：上述有界只读投影及自由场景偏好冻结。
- `tests/test_phase4_ui.cjs`、`tests/test_phase4_history.py`：状态、确认、分组/请求一致性、偏好快照和读取接口回归。
- `tests/frontend_harness.cjs`、`tests/test_frontend_shell.cjs`：补齐真实表单结构，更新新的空间分配断言，保留旧控制器/导航测试，不降低命中/持久化断言。
- `scripts/run_checks.py`、`README.md`、`.gitignore`：检查入口、产品路径说明、真实数据库备份防误提交。

## 7. 实际验证

最终统一回归：94 Python、58 Node 测试通过，所有发货 JS 语法检查通过；见 `regression.log`。包括原 Agent/分析 golden/Session契约、Phase 1/2/3，以及本轮 Focus/导航保护、背景暂停、查询失败清除旧数据、非阻塞退出取消/确认、Hub不自动创建旧实验、偏好冻结。

实际命令：

```powershell
D:/Anaconda/envs/aimcoach/python.exe -B scripts/run_checks.py
node --experimental-vm-modules --test tests/test_phase4_ui.cjs tests/test_frontend_shell.cjs
node --experimental-vm-modules scripts/check_phase2_persistence.cjs
D:/Anaconda/envs/aimcoach/python.exe -B scripts/check_phase3_training.py
git diff --check
```

六场景真实 HTTP / SQLite / 独立服务进程重启检查通过；DOM输入模拟、实际训练处理器和 HTTP/校验/磁盘真实，见 `scenario-persistence.log`。计划 Fake Client 的 HTTP/磁盘/uvicorn实例重启检查通过，见 `plan-integration.json`（该脚本的服务实例在同一测试进程内重启，不把它误称为OS进程重启）。

IAB真实浏览器使用8020独立测试库，明确 Fake Client，没有访问真实模型 Key。以下路径已实测：

1. 空库 Training Hub 显示无成绩，未因只读进入自动创建旧实验。
2. 六类新场景全部实际运行并保存，点击目标的显示位置与服务器命中结果一致；静态全命中、跟随低覆盖得低分。
3. Summary 只在训练结束后出现，数据库确认成功再显示成功；进入 History，实际记录/参数/同条件图表可见。
4. Settings 在暂停时打开，合法范围/默认值/保存反馈可见；暂停时剩余时间9.4s保持，Drawer内部滚动不改变表面尺寸。
5. Coach 请求→拒绝→重新推荐→接受→READY 1/2（没有自动开始）→手动完成2个Session→2/2；后台核对同一 plan_id、Experiment和两条执行Session正确关联，完成率100%，无基线时改善仍为待验证。
6. 停止Fake测试进程后，以相同隔离库启动无Key的正常后端；10条已保存测试Session、2条计划ID保持一致；POST推荐返回503/model_unavailable，计划集合未改变，没有Fake fallback。见 `browser-api-evidence.json`、`restart-no-key.json`。

实际 CSS 尺寸（IAB有120%缩放，按DOM读数校准，而非假定截图像素等于CSS像素）：

- 1366×768：surface 1366×640；document scrollHeight=768、scrollWidth=1366。
- 1440×900：surface 1440×772；document scrollHeight=900、scrollWidth=1440。
- 1920×1080：surface 1920×952；document scrollHeight=1080、scrollWidth=1920。
- 三者 ACTIVE 的 Coach 面板实际高度均为0；没有页面级纵向或横向溢出。
- 1920暂停设置：surface仍1920×952，Drawer高1080、内容1410，文档高仍1080。
- 另有1138×640的READY/ACTIVE测量，surface均1138×512，证明切换不改变坐标区域。

原始测量见 `browser-geometry.json`；截图见 `training-hub.png`、`arena-1366.png`、`focus-mode.png`。IAB缩放/截屏外框与实际CSS尺寸存在差异，布局判断以原始DOM测量为准，未修图冒充验证。

## 8. 未验证项与已知限制

- IAB在测试旧原生退出确认期间发生 Input/Emulation 控制超时，后续操作无法可靠送达。最终已改为非阻塞页面dialog，并通过实际ESM交互测试验证“取消保持暂停 / 确认归档”；**最终弹窗仍需人工浏览器复核**，未标为实测通过。旧8020测试标签可关闭，不应作为最终验收入口。
- 实际失焦/visibility触发、ACTIVE刷新恢复、断网后的保存恢复，已有控制器/临时存储回归；本轮未完成最终浏览器异常矩阵。切换IAB标签没有形成可靠受控失焦测试，未追认为通过。
- 窄屏CSS提供最小训练高度及自然滚动，不为消除滚动压缩控件。一次窄屏override尝试仍读到1920×1080（原始JSON末项标为请求状态NARROW_READY），**不算窄屏实测通过**；需人工检查手机/窄窗口。
- 旧Flick仍依赖浏览器 Pointer Lock，原处理器/保存链路回归通过；本轮未重新验证真实硬件和Chrome/Edge原生锁定。没有声称游戏级输入等价性。
- 真实LLM未调用，真实提供商输出质量、延迟及其完整交互仍需用户决定是否使用已配置账号验证。
- 图表在小样本下只显示观测，没有显著性/因果推断；不同任务、难度、版本/viewport不混合比较。
- 存在单一活动Experiment约束，切换冲突需在Hub显式结束现有实验，不自动丢弃历史。旧冻结区域尺寸不同也需显式处理。
- 本轮未做长时性能、辅助技术完整审计和多浏览器矩阵；不把静态/模拟测试写成这些项目通过。

## 9. 数据保护与验收服务

重启前通过原 `backup_database.py` 只读在线备份：`data/backups/before-phase4-20261105-113453.db`，备份完整性检查通过，目录已Git忽略。重启后逐行比较所有旧表：sessions 27条、scenario_sessions 1条、incomplete_sessions 0条、experiments 3条、experiment_endings 1条，内容及指纹完全一致，见 `data-preservation.json`。

新启动仅补齐此前Phase 3已实现但旧服务未执行的 training_plans 元数据建表。没有清空/重分组/更新旧数据；不能因为SQLite文件hash随合法建表改变而声称训练记录被改写。

最终服务：`http://127.0.0.1:8012/#/hub`。模型仅显示 configured_not_probed，不表示已发起或通过真实调用。验收服务使用原有数据；8020测试数据没有导入。验收运行日志与此前Phase 2运行日志不是功能成果，不自动纳入提交。

## 10. 推荐完整 Demo 路径

Home → Training Hub → 查看六类任务卡片 → Settings调整下次偏好 → 展开自由参数设5–6目标/较短时长 → 卡片进入Arena → READY核对参数 → 开始专注训练 → P暂停/Settings/继续 → 完成并等待数据库ACK → Summary → History按完整条件查看图表与Session详情 → 刷新确认记录仍在 → AI Coach请求/审阅/拒绝或接受计划 → 手动进入Arena完成各Session → Coach查看完成率与真实证据充分性。

没有模型Key时，演示自由训练、历史与503明确错误即可；离线完整计划链路使用已标识的测试脚本，不伪装成真实模型。更细的操作与预期见 `USER_ACCEPTANCE_CHECKLIST.md`。

建议检查：`workspace.js` 的 createWorkspace / arenaPhase / confirmAction；`history_views.js` 的 comparableGroups / refreshHistory；`scenario_ui.js` 的 pause / finish / getState；`trainer.js` 的后台与resize处理；`test_phase4_ui.cjs` 的交互与数据一致性测试。代码位置可用 `rg -n` 准确定位，未重写算法文件。

工程规范 skill 用于限定改动范围、解释坐标与数据流、保留原工程证据、隔离测试及如实记录验证边界。实现完成后停止扩展，等待最终人工验收。
