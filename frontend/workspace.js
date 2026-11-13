import {requestJson} from "./api.js";
import {createHistoryViews} from "./history_views.js";

export function arenaPhase(core, scene, dismissed) {
    if(scene.starting || core.countdown || core.pointerLockPending) return "COUNTDOWN";
    if(scene.active || core.sessionActive) return scene.paused || core.paused ? "PAUSED" : "ACTIVE";
    if(scene.saving || core.sessionSaving) return "SAVING";
    if(scene.pending || core.pending) return "ERROR";
    if(scene.result && !dismissed.has(scene.sessionId)) return scene.status==="complete" ? "COMPLETED" : "ABORTED";
    if(core.sessionId && !dismissed.has(core.sessionId)) {
        if(core.sessionStatus==="incomplete") return "ABORTED";
        if(core.experiment?.completed_session_ids?.includes(core.sessionId)) return "COMPLETED";
    }
    return core.error || scene.error ? "ERROR" : "READY";
}

export function createWorkspace(document, window) {
    // Keep one mounted coordinate surface. Management routes park it off-screen at the same dimensions.
    if(!document.body?.classList || !document.getElementById("arenaHud")) return null;
    document.body.dataset.phase4="true";
    const node=id=>document.getElementById(id),arena=node("testPage"),area=node("trainer-area");
    const dismissed=new Set();let core=null,scene=null,page="home",previousPhase=null,lastResultKey=null,plan=null,lastPlanKey=null;
    const move=(target,...ids)=>{for(const id of ids)node(target).append(node(id));};
    move("hubExperiment","trainingProtocolLabel","experimentIdentity","sessionProgress","conditionValue","conditionDetail","trainingStatus","endExperiment");
    move("scenarioControlsSlot","scenarioConfigPanel");move("coachPlanSlot","trainingPlanPanel");
    move("coachLegacySlot","openInsights","insightsHint");move("arenaSettingsSlot","openSettings");
    move("recoverySlot","savePanel","lifecycleStatus");
    const resultCards=document.createElement("div");resultCards.className="summary-metrics";
    const evidence=document.createElement("details"),evidenceLabel=document.createElement("summary");evidenceLabel.textContent="完整评分与保存证据";
    evidence.append(evidenceLabel,node("scenarioResult"));node("arenaResultSlot").append(resultCards,evidence);
    move("arenaRunButtons","scenarioPause","pauseTraining");
    const commands=document.createElement("div");commands.hidden=true;
    for(const id of ["scenarioStart","scenarioEnd","endTraining","scenarioRetry","scenarioExport"])commands.append(node(id));
    const recovery=document.createElement("div");recovery.className="summary-actions";
    for(const [label,ids] of [["重试未确认数据",["scenarioRetry","retrySaveButton"]],["导出原始数据",["scenarioExport","exportPendingButton"]]]) {
        const button=document.createElement("button");button.className="button secondary";button.textContent=label;
        button.addEventListener("click",()=>{for(const id of ids)node(id).click();});recovery.append(button);
    }
    node("arenaRecoverySlot").append(recovery);
    // Status remains available on Summary, but never takes space on the ACTIVE coordinate surface.
    move("recoverySlot","scenarioStatus");
    arena.replaceChildren(node("arenaHud"),area,node("arenaFooter"),node("arenaOverlay"),node("arenaSummary"),commands);
    arena.className="arena-page parked";arena.hidden=false;arena.inert=true;
    const views=createHistoryViews(document,id=>{
        node("scenarioSelect").value=id;node("scenarioSelect").dispatchEvent(new Event("change"));window.location.hash="#/arena";
    });
    node("hubSettings").addEventListener("click",()=>node("openSettings").click());
    node("arenaPausedSettings").addEventListener("click",()=>node("openSettings").click());
    let confirmationPending=false;
    function confirmAction(message) {
        // A themed modal preserves focus and pause state without blocking the browser event loop.
        if(confirmationPending)return Promise.resolve(false);
        confirmationPending=true;const dialog=node("arenaConfirm");node("arenaConfirmMessage").textContent=message;
        node("arenaConfirmCancel").textContent=scene.getState().active||core.getTrainingState().sessionActive?"取消 · 保持暂停":"取消 · 返回准备";
        return new Promise(resolve=>{
            dialog.returnValue="cancel";
            dialog.addEventListener("close",()=>{confirmationPending=false;resolve(dialog.returnValue==="confirm");},{once:true});
            dialog.showModal();
        });
    }
    node("arenaConfirmCancel").addEventListener("click",()=>node("arenaConfirm").close("cancel"));
    node("arenaConfirmAccept").addEventListener("click",()=>node("arenaConfirm").close("confirm"));
    function isScenario(){return node("scenarioSelect").value!=="legacy";}
    function pause(){if(isScenario())scene.pause();else node("pauseTraining").click();render();}
    async function start(){
        const experiment=core.getTrainingState().experiment;
        if(experiment?.protocol.training_plan_id&&experiment.status==="complete"&&!await confirmAction("当前 AI 计划已完成。继续将创建独立自由训练，不计入原计划。是否继续？"))return;
        if(node("settingsDialog").open)node("settingsDialog").close();
        dismissed.add(scene.getState().sessionId);dismissed.add(core.getTrainingState().sessionId);
        if(isScenario())node("scenarioStart").click();else area.click();render();
    }
    node("arenaStart").addEventListener("click",start);
    node("arenaResume").addEventListener("click",pause);
    node("arenaAgain").addEventListener("click",async()=>{
        const experiment=core.getTrainingState().experiment;
        if(experiment?.protocol.training_plan_id&&experiment.status==="complete"){window.location.hash="#/hub";return;}
        dismissed.add(scene.getState().sessionId);dismissed.add(core.getTrainingState().sessionId);scene.clearError();
        if(core.getTrainingState().error){try{await core.refreshTrainingState();}catch{/* The existing recovery message remains visible. */}}
        render();
    });
    node("arenaExit").addEventListener("click",async()=>{
        const s=scene.getState(),c=core.getTrainingState();
        if(s.active||c.sessionActive){
            if(!(s.paused||c.paused))pause();
            if(!await confirmAction("结束本次训练？已完成的证据将保存为未完成记录，未确认的数据会保留供重试。"))return;
            if(isScenario())await scene.finish(false);else node("endTraining").click();render();
        }else if(!c.pointerLockPending&&!s.starting&&!c.countdown)window.location.hash="#/hub";
        else node("arenaHint").textContent="正在确认开始状态，请稍候；Esc 可取消鼠标锁定。";
    });
    let lastLegacyDetail=null;
    async function legacySummary(id) {
        if(lastLegacyDetail===id)return;lastLegacyDetail=id;
        try {const detail=await requestJson(`/history/sessions/${encodeURIComponent(id)}`);
            if(core.getTrainingState().sessionId===id)node("scenarioResult").textContent=JSON.stringify({session_id:id,status:detail.status,statistics:detail.statistics,note:"原 Flick 无 aim-score-1 分数；不与新场景分数混排。"},null,2);
        }catch(error){node("scenarioResult").textContent=`历史详情未读取：${error.message}`;}
    }
    function render() {
        if(!core || !scene || page!=="test")return;
        const c=core.getTrainingState(),s=scene.getState(),phase=arenaPhase(c,s,dismissed);
        const running=phase==="ACTIVE"||phase==="PAUSED"||phase==="COUNTDOWN";
        document.body.classList.toggle("focus-active",phase==="ACTIVE"||phase==="COUNTDOWN");
        arena.dataset.phase=phase;node("arenaPhase").textContent=phase;
        node("arenaTask").textContent=isScenario()?(node("scenarioSelect").selectedOptions?.[0]?.textContent||s.scenarioId||node("scenarioSelect").value):"Original Flick";
        const completedPlan=!!c.experiment?.protocol.training_plan_id&&c.experiment.status==="complete";
        if(c.experiment?.protocol.training_plan_id)node("arenaTask").textContent+=completedPlan?" · 计划已完成":" · AI Plan";
        const sessionNumber=phase==="READY"?(c.experiment?.status==="complete"||c.experiment?.ended?1:(c.experiment?.completed_count||0)+1):(isScenario()?s.experimentOrder:c.sessionOrder)||Math.min((c.experiment?.completed_count||0)+(running?1:0),c.experiment?.schedule.length||1);
        node("arenaProgress").textContent=`Session ${sessionNumber} / ${c.experiment?.schedule.length||1} · ${isScenario()?`命中 ${phase==="READY"?0:s.hits||0} / ${phase==="READY"?node("scenarioCount").value:s.targetCount||node("scenarioCount").value}`:`Attempts ${phase==="READY"?0:c.completedAttempts||0} / ${c.experiment?.protocol.target_count||"—"}`}`;
        const limit=isScenario()?(phase==="READY"?Number(node("scenarioDuration").value)*1000:s.durationMs):(c.experiment?.protocol.settings?.time_limit_seconds||0)*1000;
        const elapsed=phase==="READY"?0:isScenario()?s.elapsedMs:c.activeDurationMs;
        if(isScenario()&&(s.active?s.scenarioId:node("scenarioSelect").value).includes("tracking"))node("arenaProgress").textContent=`Session ${sessionNumber} / ${c.experiment?.schedule.length||1} · 时长进度 ${limit?Math.min(100,100*(elapsed||0)/limit).toFixed(0):0}% · 样本 ${phase==="READY"?0:s.samples||0}`;
        node("arenaTime").textContent=limit?`${Math.max(0,(limit-(elapsed||0))/1000).toFixed(1)}s`:`${((elapsed||0)/1000).toFixed(1)}s`;
        node("scenarioPause").hidden=!isScenario();node("pauseTraining").hidden=isScenario();
        node("scenarioPause").disabled=!s.active;node("pauseTraining").disabled=!c.sessionActive;
        node("arenaStart").hidden=running||phase==="SAVING";node("arenaStart").disabled=c.pending||s.pending||c.experimentLoading;
        node("arenaStart").textContent=completedPlan?"创建自由训练":"开始训练";
        node("arenaOverlay").hidden=phase!=="PAUSED";
        node("arenaOverlayTitle").textContent="已暂停 · 计时与采样已停止";
        node("arenaOverlayText").textContent="继续使用原配置。Settings 只保存下一次实验偏好，不改变本次快照。";
        const finished=["COMPLETED","ABORTED","ERROR"].includes(phase);
        if(phase==="READY")node("start-message").textContent=isScenario()?`${node("arenaTask").textContent} · ${node("scenarioDifficulty").value}\n${node("scenarioSelect").value.includes("tracking")?"连续跟随":`${node("scenarioCount").value} 目标`} · ${node("scenarioDuration").value} 秒上限\n点击“开始训练”。小尺寸设备建议改用桌面浏览器。`:"Original Flick · 点击“开始训练”创建或继续冻结实验。需要浏览器 Pointer Lock 支持。";
        node("arenaSummary").hidden=!finished;
        node("arenaSummaryTitle").textContent=phase==="COMPLETED"?"训练已完成":phase==="ABORTED"?"训练已中断":"需要恢复或确认保存";
        node("arenaSaveFeedback").textContent=(isScenario()?node("scenarioStatus").textContent:node("saveStatus").textContent)+(c.error?` · ${c.error}`:"")+(isScenario()&&s.result?` · ${s.result.scenario_id} / ${s.result.difficulty} / ${s.result.scoring_version}`:"");
        node("arenaRecoverySlot").hidden=!(s.pending||c.pending);
        node("arenaAgain").disabled=!!(s.pending||c.pending);
        node("arenaAgain").textContent=completedPlan?"计划已完成 · 返回 Hub":"下一轮 / 再次练习";
        if(finished && !isScenario() && c.sessionId&&!c.pending)void legacySummary(c.sessionId);
        const resultKey=isScenario()&&["COMPLETED","ABORTED"].includes(phase)&&s.result?`${s.sessionId}:${s.result.overall_score}`:null;
        if(resultKey!==lastResultKey){lastResultKey=resultKey;resultCards.replaceChildren();
            if(resultKey){const result=s.result;for(const [label,value] of [["综合评分",`${result.overall_score.toFixed(2)} / 100`],["Accuracy",result.metrics.accuracy==null?null:`${(result.metrics.accuracy*100).toFixed(1)}%`],["平均命中",result.metrics.average_hit_time_ms==null?null:`${result.metrics.average_hit_time_ms.toFixed(1)} ms`],["Tracking Coverage",result.metrics.tracking_coverage==null?null:`${(result.metrics.tracking_coverage*100).toFixed(1)}%`],["Tracking Error",result.metrics.tracking_error_px==null?null:`${result.metrics.tracking_error_px.toFixed(1)} px`],["同条件历史",`${result.previous_same_condition.count}次 · 最佳 ${result.previous_same_condition.best_score??"暂无"}`],["优势 / 薄弱项",`${result.strength||"不足"} / ${result.weakness||"不足"}`]]){
                if(value==null)continue;const card=document.createElement("article"),title=document.createElement("small"),number=document.createElement("strong");title.textContent=label;number.textContent=value;card.append(title,number);resultCards.append(card);
            }}
        }
        node("arenaHint").textContent=phase==="SAVING"?"正在等待数据库确认，请保留页面。":phase==="ACTIVE"?"P / Esc 暂停 · E 退出":phase==="READY"?"先检查任务与设置；旧尺寸实验请在 Hub 显式结束后新建。":"暂停与退出不会伪装成有效完成。";
        if(phase!==previousPhase){previousPhase=phase;if(phase==="ACTIVE")node("insightsDrawer").close();}
    }
    return {
        bind(controller,scenario){core=controller;scene=scenario;core.subscribeTrainingState(render);window.setInterval(render,100);},
        canOpenSettings(){const c=core?.getTrainingState(),s=scene?.getState();return !!c&&!c.countdown&&!c.pointerLockPending&&!s?.starting&&(!(s?.active||c.sessionActive)||s?.paused||c.paused);},
        setPage(value){page=value;document.body.classList.toggle("route-arena",value==="test");document.body.classList.remove("focus-active");
            views.setSuspended(value==="test"||value==="home");
            arena.classList.toggle("parked",value!=="test");arena.inert=value!=="test";arena.setAttribute("aria-hidden",value==="test"?"false":"true");
            if(value!=="test"&&value!=="home")void views.refresh();render();},
        setPlan(value){plan=value;views.selectPlan(plan);
            node("coachPlanArena").hidden=plan.plan_status!=="accepted";
            const planKey=`${plan.plan_id}:${plan.plan_status}:${plan.evaluation?.evaluated_at||""}`;
            if(lastPlanKey!==planKey){lastPlanKey=planKey;if(page!=="test"&&page!=="home")void views.refresh();}
            const outcome=node("planAnalytics");if(page==="coach"&&plan.evaluation){outcome.replaceChildren();const progress=document.createElement("progress");progress.max=1;progress.value=plan.evaluation.completion_rate;progress.setAttribute("aria-label","当前计划完成率");const text=document.createElement("p");text.textContent=`${Math.round(progress.value*100)}% · ${plan.evaluation.sufficient?`${plan.evaluation.target_metric} 变化 ${plan.evaluation.metric_delta}`:"同条件证据不足，尚不能判断改善"}`;outcome.append(progress,text);}},
        render
    };
}
