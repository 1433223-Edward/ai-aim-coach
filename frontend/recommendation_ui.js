import {requestJson} from "./api.js";

export const METRICS = [
    ["final_hit_time_ms", "Hit time", "ms"], ["miss_count", "Miss count", ""],
    ["first_click_error_px", "First-click error", "px"], ["path_efficiency", "Path efficiency", ""],
];

export function formatMetric(value, unit = "") {
    return typeof value === "number" && Number.isFinite(value)
        ? `${value.toFixed(2)}${unit ? ` ${unit}` : ""}` : "—";
}

export function bindRecommendationUI(core, document, request = requestJson) {
    const dialog = document.getElementById("insightsDrawer");
    const entry = document.getElementById("openInsights");
    const evidenceStatus = document.getElementById("evidenceStatus");
    const profiles = document.getElementById("metricProfiles");
    let previousFocus = null;
    let evidence = null;
    let requestRevision = 0;
    let inFlight = null;
    let scopeKey = "";

    function renderScope(state) {
        const id = state.experiment?.experiment_id;
        document.getElementById("drawerExperiment").textContent = id || "尚未选择实验";
        const recommendation = state.recommendationEvidence;
        document.getElementById("recommendationScope").textContent = recommendation
            ? `建议来源 Snapshot ${recommendation.snapshot_id} · ${recommendation.session_count} Sessions`
            : "尚无当前证据范围内的 AI 建议。生成操作才会请求模型。";
        const mismatched = recommendation && evidence && recommendation.snapshot_id !== evidence.snapshot_id;
        document.getElementById("evidenceRelationship").textContent = mismatched
            ? "此分析快照与建议来源不同，不作为该建议的引用依据。"
            : recommendation && evidence ? "分析与建议使用相同 Snapshot。" : "以下为独立确定性分析，不是模型生成的指标。";
        const stage = state.planBusy ? "loading" : state.planFailureCode ? "error"
            : state.recommendationEvidence ? "success" : !state.experiment?.completed_count ? "insufficient" : "idle";
        document.getElementById("agentState").textContent = {
            loading: "GENERATING", error: "REQUEST FAILED", success: "RECOMMENDATION READY",
            insufficient: "INSUFFICIENT EVIDENCE", idle: "AWAITING YOUR GOAL",
        }[stage];
        document.getElementById("agentState").dataset.state = stage;
        dialog.setAttribute("aria-busy", String(state.planBusy));
    }

    async function loadEvidence(state) {
        const revision = ++requestRevision;
        inFlight?.abort();
        inFlight = new AbortController();
        evidence = null;
        profiles.replaceChildren();
        document.getElementById("analysisLimitations").replaceChildren();
        document.getElementById("sampleInfo").textContent = "—";
        renderScope(state);
        if (!state.experiment?.completed_count) {
            evidenceStatus.textContent = "还没有已保存 Session。完成并确认保存后，可查看真实分析。";
            return;
        }
        evidenceStatus.textContent = "正在读取已保存数据的确定性分析…";
        try {
            const data = await request(`/experiments/${encodeURIComponent(state.experiment.experiment_id)}/analysis`,
                {cache: "no-store", signal: inFlight.signal});
            // Discard a late result after closing, changing identity, or changing committed sessions.
            if (revision !== requestRevision || !dialog.open) return;
            if (data.evidence?.experiment_id !== state.experiment.experiment_id ||
                JSON.stringify(data.evidence.session_ids) !== JSON.stringify(state.experiment.completed_session_ids)) {
                throw new Error("分析来源已变化，请关闭后重新打开面板。");
            }
            evidence = data.evidence;
            evidenceStatus.textContent = "Pooled Attempt metrics · Median（按 sensitivity 分组）";
            document.getElementById("sampleInfo").textContent = `${evidence.session_count} Sessions · ${evidence.attempt_count} Attempts` +
                `${evidence.incomplete ? " · 实验未完成" : " · 实验完成"} · Snapshot ${evidence.snapshot_id}`;
            for (const profile of data.analysis.sensitivity_profiles) {
                const section = document.createElement("section");
                section.className = "metric-profile";
                const title = document.createElement("h4");
                title.textContent = `Sensitivity ${profile.sensitivity} · ${profile.attempt_count} Attempts`;
                section.append(title);
                const list = document.createElement("dl");
                for (const [key, label, unit] of METRICS) {
                    const row = document.createElement("div");
                    const term = document.createElement("dt");
                    const value = document.createElement("dd");
                    term.textContent = label;
                    value.textContent = formatMetric(profile.metrics?.[key]?.median, unit);
                    row.append(term, value);
                    list.append(row);
                }
                section.append(list);
                profiles.append(section);
            }
            for (const limitation of data.analysis.limitations || []) {
                const item = document.createElement("li");
                // Preserve the original limitation object; do not invent a semantic summary.
                item.textContent = typeof limitation === "string" ? limitation
                    : typeof limitation.message === "string" ? limitation.message : JSON.stringify(limitation);
                document.getElementById("analysisLimitations").append(item);
            }
            renderScope(core.getTrainingState());
        } catch (error) {
            if (revision === requestRevision && dialog.open) evidenceStatus.textContent = `分析不可用：${error.message}`;
        }
    }

    function close() {
        if (!dialog.open) return;
        ++requestRevision;
        inFlight?.abort();
        dialog.close();
        if (previousFocus?.isConnected) previousFocus.focus({preventScroll: true});
    }
    function open() {
        if (!core.canLeaveTrainingPage() || dialog.open) return;
        previousFocus = document.activeElement;
        dialog.showModal();
        document.getElementById("closeInsights").focus();
        const state = core.getTrainingState();
        scopeKey = JSON.stringify([state.experiment?.experiment_id, state.experiment?.completed_session_ids]);
        void loadEvidence(state);
    }
    entry.addEventListener("click", open);
    const closeButton = document.getElementById("closeInsights");
    const onCancel = (event) => { event.preventDefault(); close(); };
    const onBackdrop = (event) => {
        const bounds = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right)) close();
    };
    closeButton.addEventListener("click", close);
    dialog.addEventListener("cancel", onCancel);
    dialog.addEventListener("click", onBackdrop);
    const unsubscribe = core.subscribeTrainingState((state) => {
        if (state.locked || state.sessionActive || state.pointerLockPending) close();
        renderScope(state);
        const nextScope = JSON.stringify([state.experiment?.experiment_id, state.experiment?.completed_session_ids]);
        if (dialog.open && nextScope !== scopeKey) {
            scopeKey = nextScope;
            void loadEvidence(state);
        }
    });
    return {open, close, destroy() {
        // Ordinary page switches keep this binding; explicit teardown removes every owned listener.
        close(); unsubscribe();
        entry.removeEventListener("click", open);
        closeButton.removeEventListener("click", close);
        dialog.removeEventListener("cancel", onCancel);
        dialog.removeEventListener("click", onBackdrop);
    }};
}
