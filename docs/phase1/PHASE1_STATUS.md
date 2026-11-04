# Phase 1 最新状态

实现与自动回归已补齐，等待人工验收；范围、迁移和未验证项以 [PHASE1_REPORT.md](PHASE1_REPORT.md) 为准。71 Python / 43 Node 通过，真实 HTTP/SQLite 持久化通过；物理鼠标完整 E2E 尚待人工。

以下保留上一轮未完成时的历史记录，不代表当前实现范围。

# Phase 1 实施状态（未完成）

2026-11-04。当前不是完整 Phase 1 验收交付。

已实现：backend/settings.py 统一服务端边界；实验 sessions 1–30 默认9、attempts 1–100 默认3、应用内部倍率0.1–5默认1；frontend/settings.js 本地偏好和齿轮面板；新实验通过现有 protocol JSON 冻结配置，活跃实验不受新偏好影响。未配置偏好时保留旧三档九轮协议；显式设置后使用单一指定倍率。

新增 /history/sessions（experiment_id/date/limit）和 /history/experiments。现有详情路由保留。无 SQL schema 迁移，无历史行重写。尚未提供新配置实验的旧版本客户端回滚兼容保证。

本轮没有证据证明原幂等上传链路普遍损坏，不能宣称修复了未复现的保存故障。此前完整链路仍有 ACK、重复 ID 和协议校验。

未完成：准心/颜色/主题/音效/视觉反馈，倒计时、限时、暂停继续、提前结束状态持久化，场景过滤、Session 派生统计详情、空实验完整历史列表、真实前端到SQLite验证。现有 Escape 仍是中止未完成采集，不是暂停。本次不可标记完成。

测试：新增 settings 边界与恢复快照测试；sandbox 临时目录 SQLite 失败，获准 sandbox 外重跑。无付费模型调用、无 commit/push。真实数据库本轮只读检查得到 SHA256 bb3170ea182714a672f4896d2c7ad52426826af04bd62c1c4f49b6aa324e1ed1；不能拿10月28日的旧 hash 当作本轮前后比较。
