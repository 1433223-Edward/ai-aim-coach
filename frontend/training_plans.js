import {requestJson} from "./api.js";
import {readSettings} from "./settings.js";

export function bindTrainingPlans(core, document, onChange=()=>{}) {
    if (!document.getElementById("trainingPlanPanel")) return;
    const node = id => document.getElementById(id);
    const status = text => {node("trainingPlanStatus").textContent = text;};
    // Persist only the selected identity; the server owns the plan and its original evidence.
    let plan = null, busy = false, lastProgress = -1;
    function render() {
        node("trainingPlanAccept").disabled = busy || plan?.plan_status !== "proposed";
        node("trainingPlanReject").disabled = busy || plan?.plan_status !== "proposed";
        node("trainingPlanEvaluate").disabled = busy || plan?.plan_status !== "accepted";
        node("trainingPlanRequest").disabled = busy;
        if (!plan) return;
        onChange(plan);
        const evaluation = plan.evaluation;
        node("trainingPlanResult").textContent = [
            `Plan ${plan.plan_id} · ${plan.plan_status}`,
            `场景：${plan.scenario_id} · ${plan.sessions} Sessions · 参数：${JSON.stringify(plan.task_parameters)}`,
            `目标：${plan.training_goal} · 检查指标：${JSON.stringify(plan.target)}`,
            `推荐依据：${plan.rationale?.reason} · 相对薄弱项：${plan.rationale?.weak_component || "未识别"}`,
            `基线证据：${plan.rationale?.sufficient ? "至少3条同条件记录" : "证据不足，先建立基线"}`,
            `依据 Session IDs：${plan.evidence_session_ids?.join(", ") || "无同条件记录"}`,
            `历史范围：${JSON.stringify(plan.evidence?.history_filter || {})} 内最近100条；截断：${plan.evidence?.truncated ? "是" : "否"} · 模型来源：${plan.agent?.usage?.client_kind}`,
            `模型解释（未作事实验证）：${plan.model_explanation_unverified}`,
            evaluation ? `计划完成率：${Math.round(evaluation.completion_rate*100)}% · 对比证据：${evaluation.sufficient ? "充分（描述性）" : "不足"}` : "尚未执行或评估。",
            evaluation ? `目标达到：${evaluation.target_reached ?? "待验证"} · 指标变化：${evaluation.metric_delta ?? "无法比较"} · 后续：${evaluation.follow_up}` : "",
            evaluation ? `执行 Session IDs：${evaluation.execution_session_ids?.join(", ") || "暂无"}` : "",
            "同条件变化不代表 AI 建议带来的因果提升。"
        ].filter(Boolean).join("\n");
    }
    async function action(work) {
        if (busy) return;
        busy = true; render();
        try {await work();}
        catch (error) {status(`未完成：${error.message}`);}
        finally {busy = false; render(); checkProgress(core.getTrainingState());}
    }
    function remember(value) {
        plan = value; lastProgress = -1;
        try {localStorage.setItem("aimcoachTrainingPlanId", plan.plan_id);}
        catch {status("计划已在服务器保存，但本地身份存储不可用；请保留 plan_id。");}
        render();
    }
    node("trainingPlanRequest").addEventListener("click", () => action(async () => {
        const area = node("trainer-area");
        const body = {goal: node("trainingPlanGoal").value, trainer_width: area.clientWidth,
            history_filter: {date_from:node("trainingPlanDateFrom").value || null, date_to:node("trainingPlanDateTo").value || null},
            trainer_height: area.clientHeight, bounds: {
                time_budget_seconds: Number(node("trainingPlanBudget").value),
                max_sessions: Number(node("trainingPlanSessions").value),
                max_attempts: Number(node("trainingPlanAttempts").value),
                max_duration_seconds: Number(node("trainingPlanDuration").value),
                max_difficulty: node("trainingPlanDifficulty").value}};
        status("正在读取固定历史快照并请求模型；不会自动开始训练。");
        remember(await requestJson("/agent/training-plans", {method: "POST",
            headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}, 65000));
        status("建议已保存。请查看证据与参数后接受或拒绝。");
    }));
    node("trainingPlanAccept").addEventListener("click", () => action(async () => {
        // Do not associate a plan while another report is collecting or waiting for an ACK.
        const state = core.getTrainingState();
        if (!core.canLeaveTrainingPage() || state.pending || state.sessionSaving) throw new Error("请先结束并保存当前训练。");
        const area = node("trainer-area");
        const reply = await requestJson(`/training-plans/${encodeURIComponent(plan.plan_id)}/accept`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({trainer_width: area.clientWidth, trainer_height: area.clientHeight,
                preferences: readSettings() || {}})});
        remember(reply.plan);
        core.selectScenarioExperiment(reply.experiment);
        const config = reply.experiment.protocol.scenario;
        node("scenarioSelect").value = config.scenario_id;
        for (const [key,id] of Object.entries({difficulty:"scenarioDifficulty",seed:"scenarioSeed",
            duration_seconds:"scenarioDuration",target_count:"scenarioCount",speed:"scenarioSpeed",direction:"scenarioDirection"})) {
            node(id).value = config[key];
        }
        core.setScenarioMode(true, false);
        status("计划已接受、参数已冻结。点击场景库“开始场景”执行；没有自动开始训练。");
    }));
    node("trainingPlanReject").addEventListener("click", () => action(async () => {
        remember(await requestJson(`/training-plans/${encodeURIComponent(plan.plan_id)}/reject`, {method:"POST"}));
        status("已拒绝；原建议和证据保留，可以重新推荐。");
    }));
    async function evaluate() {
        plan.evaluation = await requestJson(`/training-plans/${encodeURIComponent(plan.plan_id)}/evaluate`, {method:"POST"});
        status(plan.evaluation.sufficient ? "效果评估已保存；变化不代表 AI 建议导致的因果提升。" : "效果评估已保存；同条件证据不足，不能声称改善。");
        render();
    }
    node("trainingPlanEvaluate").addEventListener("click", () => action(evaluate));
    function checkProgress(state) {
        if (!busy && plan?.plan_status === "accepted" && state.experiment?.experiment_id === plan.experiment_id &&
            state.experiment.completed_count !== lastProgress && !state.pending && !state.sessionSaving) {
            lastProgress = state.experiment.completed_count;
            void action(evaluate);
        }
    }
    core.subscribeTrainingState(checkProgress);
    void action(async () => {
        const id = localStorage.getItem("aimcoachTrainingPlanId");
        if (id) {remember(await requestJson(`/training-plans/${encodeURIComponent(id)}`)); status("已恢复服务器计划；训练不会自动开始。");}
    });
}
