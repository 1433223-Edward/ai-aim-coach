# 人工统一验收 Checklist

从 D:\Projects\AimCoach 执行。仅使用新的合成验收库，保留真实训练库。安装、配置及备份命令见 LOCAL_OPERATIONS.md。

- [ ] Python 3.12 安装 requirements-runtime.txt，运行 `python -B scripts/run_checks.py`，预期 60 Python、27 Node 全通过。
- [ ] 运行 `python -B scripts/check_local_startup.py`；预期 PASS，服务自行停止，临时库清理。
- [ ] 按 LOCAL_OPERATIONS.md 创建独立 Demo DB 并启动；访问 `/health`，无 Key 时 storage=ready、agent=unavailable；训练页正常打开。
- [ ] 使用支持 Pointer Lock 的桌面 Chrome/Edge 打开本地页（内置浏览器可能拒绝）。点击创建新实验，再次点击锁定鼠标。左键可 HIT/MISS，右键/中键不增加射击；Esc 结束未完成采集，不增加已保存进度。
- [ ] 完成一轮三个目标，确认保存反馈及进度增加一；刷新和另一标签页应恢复同一实验/已确认进度。不要把合成数据当作真实训练结果。
- [ ] 采集中调整窗口尺寸：当前采集应中止，恢复实验尺寸才允许继续；完成九轮后刷新仍保留完成状态，显式开始才新建实验。
- [ ] 在 DevTools Network 设置 Offline，完成一轮；应显示未确认数据并可导出。点击导出，打开下载 JSON 核对 SessionId、attempts 和原始记录；不要关闭页面直到保存或导出确认。
- [ ] 保持 Offline 刷新：pending 应仍在，不能当作服务端确认进度。恢复网络并重试，确认同一 SessionId 仅保存一次。删除浏览器站点数据不是本轮恢复策略。
- [ ] 无 Key 点击生成建议，应有明确 unavailable/request ID；目标为空白不能提交。Tab 能聚焦目标输入与按钮；使用读屏器检查 label 与状态播报（本轮未做真实辅助技术验证）。
- [ ] 有自备有效 Key 时才单独验证真实 Qwen；生成后逐项按 AGENT_EVALUATION.md rubric 核对数据/范围/未知条件。默认离线测试不执行或证明此项。
- [ ] 规划期间取消等待、刷新或另一标签页保存新数据，旧回答不得重新显示。取消前端等待不保证供应商立即停止计费。
- [ ] 停止服务，在合成库运行备份/恢复命令，切换到新恢复库重启，确认实验 ID、进度和分析相同。对已有目标再次备份应拒绝覆盖。

本清单为待维护者执行的操作，不代表以上物理鼠标、下载、读屏和真实模型步骤已由自动化完成。不要在真实 DB 内注入损坏数据进行验收。
