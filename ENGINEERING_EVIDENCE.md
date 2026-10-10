# AI Aim Coach — Engineering Evidence

## Project Goal

AI Aim Coach 是一个基于真实训练数据的个性化瞄准训练系统。

项目不是复制完整 Aim Trainer，而是验证一条完整的数据与 AI 决策链路：

Trainer
→ Raw Training Data
→ Deterministic Analysis
→ Structured Evidence
→ Analysis Tools
→ LLM Agent
→ Next Experiment Recommendation

当前 MVP 聚焦 Flick Sensitivity Test。

---

# 1. Raw Mouse Input Pipeline

## Problem

普通浏览器鼠标坐标无法直接用于 FPS 风格瞄准训练：

- 鼠标可能离开浏览器窗口；
- Pointer Lock 后不能依赖 clientX / clientY；
- 操作系统鼠标加速度可能影响 movementX / movementY。

## Implementation

建立 Virtual Crosshair：

Physical Mouse
→ Pointer Lock
→ movementX / movementY
→ Trainer Sensitivity
→ Virtual Crosshair

Trainer Sensitivity 定义为：

virtual movement = raw mouse movement × trainer sensitivity

Crosshair 始终由虚拟坐标维护，而不是依赖系统鼠标位置。

## Runtime Failure

初始运行中发现鼠标偶发异常加速和跳变。

进一步定位到输入层后，改用：

requestPointerLock({
    unadjustedMovement: true
})

并保留普通 Pointer Lock fallback。

## Evidence

Runtime 验证后异常跳变消失。

这证明：

Observed Input Failure
→ Input-layer Hypothesis
→ Raw Input Modification
→ Runtime Verification

---

# 2. Training Data Acquisition

## Session Structure

一次 Training Session 包含多个 Attempts。

Attempt 从 Target 出现开始，到第一次成功 HIT 结束。

Raw Attempt 保存：

- Target 位置和时间；
- Cursor 初始位置；
- 完整 movement trajectory；
- Click 位置和时间；
- HIT / MISS。

Session 保存：

- experimentId；
- sessionId；
- trainerSensitivity；
- targetCount；
- experimentOrder；
- sensitivityRepeat；
- attempts[]。

分析指标没有直接写入 Raw Data。

原则：

Raw Data 保存事实。
Derived Metrics 由 Analysis Layer 计算。

---

# 3. Data Integrity

Trainer 完成 Session 后：

Trainer
→ POST /sessions
→ FastAPI
→ Session Integrity Validation
→ SQLite

数据库不直接假设“能被 JSON 解析的数据就是有效实验数据”。

Integrity Validator 检查：

- Attempt 数是否等于 targetCount；
- Session 时间顺序；
- Target 必要字段；
- Movement 时间顺序；
- Click 时间顺序；
- Attempt 是否以 HIT 结束。

Database Session Source 已通过 Runtime 验证：

SQLite
→ session_id retrieval
→ experiment_id query
→ Raw Session reconstruction

---

# 4. Sensitivity Analysis

Sensitivity Analysis v0.1 使用四个确定性指标：

Final Hit Time
Miss Count
First-click Error
Path Efficiency

其中 Path Efficiency 定义为：

direct distance from cursor start to final HIT position
/
actual cursor movement path length

而不是使用 Target Center 作为终点，因为命中只要求最终 Click 落在 Target Radius 内。

## Experiment

正式实验测试：

Sensitivity:
0.8 / 1.0 / 1.2

每个 sensitivity：
3 Sessions

每个 Session：
20 Attempts

总计：
9 Sessions
180 Attempts

实验顺序随机化，以减少固定的学习 / 疲劳顺序影响。

## Observed Result

最稳定的 Finding：

Sensitivity 1.2 的 Path Efficiency 在 3/3 个独立 Session 中，
均低于 0.8 / 1.0 的所有 Session Median。

但 Final Hit Time 没有表现出稳定速度优势。

因此确定性分析只输出：

1.2 shows consistently lower path efficiency.

而不直接输出：

1.2 is bad.

Agent 负责根据用户目标解释这一 Evidence。

---

# 5. Analysis Architecture

Analysis Layer 被重构为纯计算能力：

sessions: list[dict]
→ analyze_sessions()
→ Structured Analysis Result

Analysis Service 不知道：

- SQLite；
- 文件系统；
- FastAPI；
- Agent。

Storage Layer：

experiment_id
→ sessions

Analysis Layer：

sessions
→ deterministic evidence

这使分析逻辑可以独立测试和复用。

---

# 6. Tool Layer

Analysis 能力被封装为三个 Tools：

get_analysis_summary()

get_sensitivity_profile(sensitivity)

compare_sensitivities(sensitivities)

Tools 不负责：

- 读取 Raw Mouse Events；
- 重新设计指标；
- 推荐最佳 sensitivity；
- Agent Decision。

Tools 只暴露 Deterministic Evidence。

---

# 7. Agent Runtime

Agent 使用 Qwen API，并实现显式 Tool Calling Loop。

核心运行过程：

User Goal
→ Qwen
→ Tool Call
→ Python Dispatcher
→ Analysis Tool
→ Structured Evidence
→ Qwen
→ Next Tool / Final Decision

LLM 不直接执行 Python。

真实 Tool Execution 始终由 Application Runtime 控制。

Agent 具有最大 Tool Round 限制，避免无限循环。

## Multi-tool Runtime Evidence

真实运行中 Agent 自主选择：

get_analysis_summary
→ compare_sensitivities
→ get_sensitivity_profile(...)
→ final recommendation

Tool 顺序不是 Python 写死的。

模型根据当前 Context 判断还需要什么 Evidence。

---

# 8. Agent Failure Analysis

第一次只开放 Summary Tool 时，Agent 因缺乏完整 Profile Evidence，
错误地认为系统没有 Flick Speed 数据。

问题不是模型本身，而是：

Tool Context Insufficient.

解决方式不是继续增加 Prompt，而是重新检查 Tool Boundary，
开放 Profile / Comparison Tools。

第二个发现：

LLM 可能把 pooled metric trend 表达成更强的跨 Session conclusion。

因此系统设计保持：

Deterministic Analysis decides what data shows.

LLM interprets evidence and decides what to test next.

---

# 9. Backend Application Integration

Agent Capability 被暴露为：

POST /agent/plan

Request：

{
    "goal": "..."
}

Response：

{
    "goal": "...",
    "answer": "...",
    "tools_used": [...]
}

tools_used 同时提供最小 Agent Observability。

Trainer Session 使用：

POST /sessions

实现 Raw Session Persistence。

---

# 10. Persistence

SQLite 被引入的原因不是为了增加技术栈，而是项目出现了真实 State / History 需求：

- 多个 Sessions；
- Experiment grouping；
- Sensitivity filtering；
- Future training history；
- Database-backed Analysis。

当前没有过早拆分：

attempts
movements
clicks

这些仍作为 Raw JSON 保存。

常用 Session Metadata 独立存列。

---

# 11. Experiment Identity

Experiment 和 Session 被明确区分：

Experiment
= 一组共享实验协议的数据

Session
= 一次实际训练运行

Session 保存：

experimentId
experimentOrder
sensitivityRepeat

从而避免不同实验协议的数据被错误混合分析。

---

# 12. Application Packaging

Frontend 与 FastAPI 最终整合为同一个 Application Origin：

FastAPI :8000
├── /
│   → Aim Trainer
├── /sessions
│   → Persistence
└── /agent/plan
    → Agent

Frontend API 使用 Relative URL：

/sessions
/agent/plan

因此不再需要开发阶段的跨 Origin CORS 配置。

项目可以通过单 FastAPI Process 启动。

---

# Current End-to-End Pipeline

Physical Mouse
↓
Pointer Lock / Raw Mouse Input
↓
Virtual Crosshair
↓
Target / Attempt / Session
↓
Session Integrity Validation
↓
FastAPI
↓
SQLite
↓
Analysis Runtime
↓
Deterministic Analysis Service
↓
Structured Evidence
↓
Analysis Tools
↓
Qwen Tool Calling Agent
↓
Experiment Recommendation
↓
Frontend

---

# Current Limitations

- 当前主要是单用户本地 MVP；
- Experiment ID 仍有部分前端临时状态逻辑；
- 没有账号和多用户隔离；
- Sensitivity Analysis 仍是描述性统计；
- 当前实验数据只来自单个玩家和固定设备环境；
- Agent 延迟较高，因为多个 Tool Round 会产生多次模型调用；
- 尚未进行正式公网部署。

这些属于已知边界，而不是被隐藏的问题。