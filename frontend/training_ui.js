// This view reads controller snapshots; it never advances conditions or writes training state.
export function bindTrainingUI(core, document) {
    return core.subscribeTrainingState((state) => {
        const experiment = state.experiment;
        const condition = experiment?.next_condition;
        document.getElementById("trainingProtocolLabel").textContent=experiment
            ? `${experiment.protocol.sensitivities?.length || new Set(experiment.schedule.map(item=>item.sensitivity)).size} 档 sensitivity · ${experiment.schedule.length} Sessions · 每轮 ${experiment.protocol.target_count} Attempts`
            : "等待服务端冻结配置";
        document.getElementById("pauseTraining").disabled=!state.sessionActive;
        document.getElementById("endTraining").disabled=!state.sessionActive;
        document.getElementById("openSettings").disabled=!!((state.scenarioRunning&&!state.scenarioPaused)||state.locked||(state.sessionActive&&!state.paused)||state.pointerLockPending||state.countdown);
        document.getElementById("experimentIdentity").textContent = experiment?.experiment_id || "等待服务端实验";
        document.getElementById("sessionProgress").textContent = experiment
            ? `${experiment.completed_count} / ${experiment.schedule.length}` : "— / —";
        document.getElementById("conditionValue").textContent = condition ? `${condition.sensitivity} ×` : "—";
        document.getElementById("conditionDetail").textContent = condition
            ? `Repeat ${condition.repeat} · ${experiment.protocol.target_count} targets`
            : experiment?.status === "complete" ? "实验已完成 · 显式开始下一轮" : "等待实验条件";
        const locked = state.scenarioRunning || state.sessionActive || state.locked || state.pointerLockPending;
        const status = state.paused ? "PAUSED" : state.countdown ? "COUNTDOWN" : locked ? "TRAINING" : state.sessionSaving ? "SAVING" : state.experimentLoading
            ? "SYNCING" : state.pending ? "PENDING" : state.error ? "ATTENTION"
                : experiment?.ended ? "ENDED" : experiment?.status === "complete" ? "COMPLETE" : "READY";
        const badge = document.getElementById("trainingStatus");
        badge.textContent = status;
        badge.dataset.state = status.toLowerCase();
        document.getElementById("openInsights").disabled = locked || !!experiment?.protocol.scenario;
        document.getElementById("insightsHint").textContent = locked
            ? "训练中请先按 Esc 退出，再查看 Insights。" : "查看已保存数据与 AI 建议";
    });
}
