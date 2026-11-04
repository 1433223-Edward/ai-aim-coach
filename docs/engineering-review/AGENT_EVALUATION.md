# Agent 证据评估说明

本轮补齐可复现检查与人工 rubric，不新增 Tool 或自动语义裁判。默认检查无 Key、无模型费用。

## 确定性回归（已执行）

- 零工具最终回答、summary-only 回答：test_agent_guardrails.py 拒绝成功。
- 每个已测 sensitivity 必须有成功 profile 或 compare 覆盖；失败工具不计证据。
- 错误参数、未知工具、坏 JSON、复用 tool ID、空白/截断/拒绝回答、轮次/时间/token 预算：隔离 fake client 回归。
- tools 返回内容与 snapshot 逐项相等；额外伪造结论也拒绝，见 test_engineering_completion.py。
- 上传发生于工具调用间、并行两个请求：test_experiment_consistency.py 证明快照不混用。
- 历史 finding/指标/limitations：来源 hash 和黄金结果保持相等，见 test_demo_fixture.py、test_session_contract.py。

以上证明 runtime 和 evidence contract，不证明模型最终自然语言事实正确。fake client 的最终文本不是事实正确率样本。

## 维护者人工 rubric（真实模型未运行）

使用明确标记的合成 Demo 或已验证实验，记录日期、模型、request_id、usage 中 prompt/tools hash、snapshot_id、Session 范围、耗时与 tokens_accounted。fallback accounting=conservative_bytes 是预算估计，不是供应商账单 token。

1. **Summary 不足**：只获取 summary 后的文本不能被当作有效建议。预期工具继续获取 metric；runtime 回归可验证门槛。
2. **Pooled 与跨 Session**：对照工具的 pooled_attempt_metrics 与 finding。观察必须引用具体 metric/sensitivity；没有 finding 支持时，不能把 pooled 差异称作跨 Session 稳定结论。
3. **未测试条件**：让模型讨论一个未测 sensitivity。只能提出后续测试，不能声称其结果更好。unknown 必须明确。
4. **不完整实验**：少于九轮的数据必须声明不完整；不得把 Attempt 个数说成独立玩家数。
5. **来源引用**：观察、建议、未知应区分；experiment/snapshot 引用应与 response.evidence 一致，观察数值须与对应 profile 相等。

每项记 PASS/FAIL/NOT TESTED，并保留脱敏响应；不保存 Key。任一事实错误则该真实模型样本 FAIL，不能靠通过工具门槛宣布语义正确。默认离线测试没有自动识别所有自然语言误述的能力。

真实模型重复评估、前端专门证据可视化和语义自动裁判暂不实施：前两者分别需要人工付费模型验收和新增展示功能，后者扩大 Agent 功能复杂度。现有 prompt 加入引用与分段要求，但这只是指令，不是事实保证。
