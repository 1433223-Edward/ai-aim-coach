import {requestJson} from "./api.js";

export function comparableGroups(records) {
    const groups = new Map();
    for (const row of records) {
        const result = row.result;
        // Only eligible completed records share a score axis, and only within the full condition key.
        if (row.status !== "complete" || !result?.eligible || !result.condition_key ||
            !Number.isFinite(result.overall_score) || result.overall_score<0 || result.overall_score>100) continue;
        const key = `${row.scenario_id}:${result.difficulty}:${result.scoring_version}:${result.condition_key}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(row);
    }
    for (const rows of groups.values()) rows.sort((a,b)=>(a.started_at||"").localeCompare(b.started_at||"") || a.session_id.localeCompare(b.session_id));
    return groups;
}

export function sceneSummary(records, scenarioId) {
    const groups = comparableGroups(records.filter(row=>row.scenario_id===scenarioId));
    const latest = [...groups.values()].flat().sort((a,b)=>(b.started_at||"").localeCompare(a.started_at||""))[0];
    if (!latest) return {count:0,latest:null,best:null};
    const peers = [...groups.values()].find(rows=>rows.includes(latest));
    return {count:[...groups.values()].reduce((sum,rows)=>sum+rows.length,0),latest,
        best:Math.max(...peers.map(row=>row.result.overall_score))};
}

function element(document, tag, text, className) {
    const node=document.createElement(tag);if(text!==undefined)node.textContent=text;
    if(className)node.className=className;return node;
}

export function renderTrend(container, rows, metric, label, unit, document) {
    const values=rows.map(row=>metric==="overall_score"?row.result?.overall_score:row.result?.metrics?.[metric]).filter(Number.isFinite);
    const card=element(document,"article",undefined,"metric-chart");
    card.append(element(document,"h3",label));
    if (!values.length) {card.append(element(document,"p","暂无适用指标。","empty-state"));container.append(card);return;}
    card.append(element(document,"strong",`${values.at(-1).toFixed(2)} ${unit}`));
    const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("viewBox","0 0 400 100");svg.setAttribute("role","img");
    svg.setAttribute("aria-label",`${label}，${values.length}次同条件记录，最小${Math.min(...values).toFixed(2)}，最大${Math.max(...values).toFixed(2)} ${unit}`);
    const low=Math.min(...values),range=Math.max(...values)-low;
    const points=values.map((value,i)=>`${12+i*376/Math.max(1,values.length-1)},${range?88-(value-low)*76/range:50}`).join(" ");
    const line=document.createElementNS(svg.namespaceURI,"polyline");line.setAttribute("points",points);
    line.setAttribute("fill","none");line.setAttribute("stroke","currentColor");line.setAttribute("stroke-width","2");svg.append(line);
    if(values.length===1){const point=document.createElementNS(svg.namespaceURI,"circle");point.setAttribute("cx","12");point.setAttribute("cy","50");point.setAttribute("r","3");point.setAttribute("fill","currentColor");svg.append(point);}
    card.append(svg,element(document,"small",`${values.length}条 · 范围 ${low.toFixed(2)}–${Math.max(...values).toFixed(2)} ${unit} · 横轴为训练次序${values.length<2?"，趋势样本不足":""}`));
    // Text values make exact readings available without color perception or pointer hover.
    const details=element(document,"details");details.append(element(document,"summary","查看数值"),element(document,"p",values.map(v=>v.toFixed(2)).join(" → ")));card.append(details);container.append(card);
}

export function createHistoryViews(document, chooseScenario) {
    let records=[],registry=[],plans=[],generation=0,suspended=false,selectedPlan=null;
    const node=id=>document.getElementById(id);
    const text=(id,value)=>{node(id).textContent=value;};
    function charts(container, rows) {
        if(suspended)return;
        container.replaceChildren();
        if(container===node("historyCharts"))node("abilityOverview").replaceChildren();
        if(!rows.length){container.append(element(document,"p","暂无同条件有效成绩。完成并保存训练后再查看。","empty-state"));return;}
        for(const [metric,label,unit] of [["overall_score","综合评分","/100"],["accuracy","Accuracy","比例0–1"],
            ["average_hit_time_ms","平均命中时间","ms"],["targets_per_second","完成速度","次/s"],
            ["tracking_coverage","Tracking Coverage","比例0–1"],["tracking_error_px","Tracking Error","px"],["stability","Stability","比例0–1"]]) {
            if(metric==="overall_score"||rows.some(r=>Number.isFinite(r.result?.metrics?.[metric]))) renderTrend(container,rows,metric,label,unit,document);
        }
        if(container===node("historyCharts")){
            const overview=node("abilityOverview");overview.replaceChildren();
            for(const [name,value] of Object.entries(rows.at(-1)?.result?.normalized_components||{})){
                if(!Number.isFinite(value)||value<0||value>1)continue;
                const label=element(document,"p",`${name} · ${(value*100).toFixed(1)}%（归一化得分，越高越好）`),bar=element(document,"progress");bar.max=1;bar.value=value;bar.setAttribute("aria-label",name);overview.append(label,bar);
            }
        }
    }
    function conditionLabel(rows) {
        const row=rows[0],p=row.parameters||{};
        return `${row.scenario_id} · ${row.result.difficulty} · ${row.result.scoring_version} · seed ${p.seed??"?"} · ${p.duration_seconds??"?"}s / ${p.target_count??"?"}目标 · 速度${p.speed??"?"} ${p.direction??""} · ${row.viewport?.join("×")||"尺寸未知"} · ${row.result.condition_key.slice(0,8)}`;
    }
    function renderCards() {
        const container=node("scenarioCards");container.replaceChildren();
        for(const category of ["Clicking","Switching","Tracking"]) {
            const group=element(document,"section",undefined,"task-group");group.append(element(document,"h2",category));
            const grid=element(document,"div",undefined,"task-grid");
            for(const scene of registry.filter(s=>(s.category==="tracking"?"Tracking":s.scenario_id==="four_target_switching"?"Switching":"Clicking")===category)) {
                const summary=sceneSummary(records,scene.scenario_id),card=element(document,"article",undefined,"task-card");
                if(selectedPlan?.scenario_id===scene.scenario_id)card.append(element(document,"span",`AI PLAN · ${selectedPlan.plan_status}`,"state-badge"));
                card.append(element(document,"span",category,"eyebrow"),element(document,"h3",scene.name),element(document,"p",scene.description));
                card.append(element(document,"small",`难度 easy / normal / hard · ${summary.count}次有效训练（本次载入范围）`));
                card.append(element(document,"div",summary.latest?`最近 ${summary.latest.result.overall_score.toFixed(2)} · 同条件最佳 ${summary.best.toFixed(2)}`:"还没有有效成绩 · 从第一次练习开始","card-score"));
                if(summary.latest)card.append(element(document,"small",conditionLabel([summary.latest])));
                const actions=element(document,"div",undefined,"card-actions"),enter=element(document,"button","进入训练 →","button primary"),detail=element(document,"button","说明与趋势","button quiet");
                enter.type=detail.type="button";enter.addEventListener("click",()=>chooseScenario(scene.scenario_id));
                detail.addEventListener("click",()=>{node("taskDetail").hidden=false;text("taskDetailTitle",scene.name);text("taskDetailDescription",`${scene.description} — 历史趋势仅显示最近条件；完整条件选择请进入 History。`);
                    const peers=summary.latest?[...comparableGroups(records).values()].find(rows=>rows.includes(summary.latest)):[];
                    charts(node("taskDetailChart"),peers||[]);node("taskDetail").scrollIntoView({block:"start",behavior:"smooth"});});
                actions.append(enter,detail);card.append(actions);grid.append(card);
            }
            group.append(grid);container.append(group);
        }
        const legacy=element(document,"article",undefined,"panel");legacy.append(element(document,"h3","Original Flick · 兼容实验"),element(document,"p","保留原 Pointer Lock 与灵敏度实验；旧指标不混入新评分。"));
        const button=element(document,"button","进入原训练","button secondary");button.addEventListener("click",()=>chooseScenario("legacy"));legacy.append(button);container.append(legacy);
    }
    function renderPlans(container) {
        container.replaceChildren();
        if(!plans.length){container.append(element(document,"p","暂无训练计划。AI 请求由你主动发起。","empty-state"));return;}
        for(const plan of plans){const article=element(document,"article",undefined,"plan-outcome");
            article.append(element(document,"h3",`${plan.scenario_id} · ${plan.plan_status}`),element(document,"small",`Plan ${plan.plan_id} · ${plan.sessions} Sessions`));
            if(plan.evaluation){const e=plan.evaluation,progress=element(document,"progress");progress.max=1;progress.value=e.completion_rate;progress.setAttribute("aria-label","计划完成率");
                article.append(progress,element(document,"p",`完成率 ${(100*e.completion_rate).toFixed(0)}% · ${e.sufficient?`${e.target_metric} 变化 ${e.metric_delta}，目标${e.target_reached?"已达到":"未达到"}`:"同条件证据不足，改善待验证"}`),element(document,"small",`依据 ${e.baseline_session_ids.length}条 / 执行 ${e.execution_session_ids.length}条 · ${e.follow_up} · 不作因果归因`));
            }else article.append(element(document,"p","尚未评估。"));
            container.append(article);
        }
    }
    async function refresh() {
        if(suspended)return;
        const request=++generation;
        try {
            const [definitions,scores,historyPlans]=await Promise.all([requestJson("/scenarios"),requestJson("/scenarios/sessions?limit=500"),requestJson("/training-plans?limit=20")]);
            if(request!==generation||suspended)return;
            registry=definitions;records=scores;plans=historyPlans;renderCards();renderPlans(node("planAnalytics"));
            text("hubStatus",`已载入 ${records.length} 条场景记录（最多500条）；不同条件分组展示。`);
            text("coachOverview",`${records.filter(r=>r.status==="complete"&&r.result?.eligible).length} 次有效场景训练 · ${comparableGroups(records).size} 组独立条件。首次训练或不足样本不作改善判断。`);
            const selected=node("historyScene"),value=selected.value;selected.replaceChildren(element(document,"option","全部场景"));selected.firstChild.value="";
            for(const scene of [...registry,{scenario_id:"flick_2d",name:"Original Flick"}]){const option=element(document,"option",scene.name);option.value=scene.scenario_id;selected.append(option);}selected.value=value;
            await refreshHistory();
        }catch(error){text("hubStatus",`概览未载入：${error.message}`);text("historyStatus",`历史未载入：${error.message}`);text("coachOverview",`历史概览不可用：${error.message}`);}
    }
    let historyRequest=0;
    async function refreshHistory() {
        if(suspended)return;
        const request=++historyRequest;
        node("sessionDetailPanel").hidden=true;node("historyCondition").disabled=true;
        // Do not leave a previous condition's chart under newly edited filter labels.
        node("historyCharts").replaceChildren(element(document,"p","正在读取所选条件…","empty-state"));
        node("abilityOverview").replaceChildren();
        const params=new URLSearchParams({limit:"100"});
        for(const [id,key] of [["historyScene","scene"],["historyDate","date"],["historyExperiment","experiment_id"]])if(node(id).value)params.set(key,node(id).value);
        text("historyStatus","正在读取历史…");
        try {
            const [sessions,experiments]=await Promise.all([requestJson(`/history/sessions?${params}`),requestJson(`/history/experiments?limit=100${node("historyScene").value?`&scene=${encodeURIComponent(node("historyScene").value)}`:""}${node("historyDate").value?`&date=${node("historyDate").value}`:""}`)]);
            if(request!==historyRequest||suspended)return;
            node("sessionList").replaceChildren();node("experimentList").replaceChildren();
            for(const session of sessions){const button=element(document,"button",`${session.scene} · ${session.status} · ${session.started_at} · Session ${session.session_id}`,"record-row");
                button.addEventListener("click",async()=>{node("sessionDetailPanel").hidden=false;try{const detail=await requestJson(`/history/sessions/${encodeURIComponent(session.session_id)}`);node("sessionDetail").replaceChildren(element(document,"pre",JSON.stringify({session_id:session.session_id,status:detail.status,score:detail.score,statistics:detail.statistics,attempts:detail.session.attempts?.length,training_plan_id:detail.session.trainingPlanId||"关联见 Experiment protocol"},null,2)));}catch(error){text("sessionDetail",`该记录不能安全展示：${error.message}`);}node("sessionDetailPanel").scrollIntoView({block:"start",behavior:"smooth"});});node("sessionList").append(button);}
            for(const exp of experiments.filter(e=>!node("historyExperiment").value||e.experiment_id===node("historyExperiment").value))node("experimentList").append(element(document,"p",`${exp.scene} · 已保存 ${exp.saved_sessions} / 未完成 ${exp.interrupted_sessions} · ${exp.ended?"已结束":"保留"} · ${exp.experiment_id}`,"record-row"));
            if(!sessions.length)node("sessionList").append(element(document,"p","没有匹配记录。","empty-state"));
            if(!experiments.length)node("experimentList").append(element(document,"p","没有匹配实验。","empty-state"));
            // Chart filters use exactly the Session identities returned by the selected history query.
            const ids=new Set(sessions.map(s=>s.session_id));
            const groups=comparableGroups(records.filter(r=>ids.has(r.session_id)));
            const select=node("historyCondition");select.replaceChildren();
            for(const [key,rows] of groups){const option=element(document,"option",conditionLabel(rows));option.value=key;select.append(option);}
            charts(node("historyCharts"),groups.get(select.value)||[]);
            select.disabled=false;
            text("historyStatus",`${sessions.length} 条记录（最多100条）；图表来自已载入的最多500条场景成绩，未评分旧记录不绘制新评分。`);
        }catch(error){
            if(request!==historyRequest||suspended)return;
            text("historyStatus",`查询失败：${error.message}`);node("historyCondition").replaceChildren();
            for(const id of ["historyCharts","abilityOverview","sessionList","experimentList"])node(id).replaceChildren(element(document,"p","本次查询未完成，未展示旧结果。请重试。","empty-state"));
        }
    }
    node("historyCondition").addEventListener("change",()=>charts(node("historyCharts"),comparableGroups(records).get(node("historyCondition").value)||[]));
    node("filterHistory").addEventListener("click",()=>{void refreshHistory();});
    node("refreshHistory").addEventListener("click",()=>{void refresh();});
    return {refresh,refreshHistory,renderPlans,setSuspended:value=>{suspended=value;if(value){generation++;historyRequest++;}},selectPlan:plan=>{
        selectedPlan=plan;
        node("hubPlanArena").hidden=plan.plan_status!=="accepted";
        text("hubPlan",`${plan.scenario_id} · ${plan.sessions} Sessions · ${plan.plan_status} · ${plan.training_goal}`);
        if(!suspended&&registry.length)renderCards();
    }};
}
