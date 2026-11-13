import {requestJson} from "./api.js";
import {targetAt} from "./scenario_motion.js";
import {shotFeedback,readSettings} from "./settings.js";

export function bindScenarioLibrary(core,document){
    const area=document.getElementById("trainer-area"),target=document.getElementById("target"),crosshair=document.getElementById("crosshair");
    const select=document.getElementById("scenarioSelect"),status=document.getElementById("scenarioStatus");
    // Keep the active clock separate from paused wall time and freeze it before queuing evidence.
    let active=false,paused=false,session=null,config=null,start=0,elapsed=0,hits=0,nextSample=0,frame=null,cursor={x:0,y:0};
    // One immutable report survives refresh until the server acknowledges its Session ID.
    let pending=null,corrupt=null,saving=false,starting=false,memoryOnly=false,lastResult=null,lastSavedSession=null,lastSavedStatus=null,startError=null;
    try{const raw=localStorage.getItem("aimcoachScenarioPending");pending=raw?JSON.parse(raw):null;
        if(pending && (typeof pending!=="object"||typeof pending.sessionId!=="string"))throw new Error("Invalid queue");
    }catch(error){corrupt="场景待上传状态无效或存储不可访问；请先导出。";}
    if(pending||corrupt)core.setScenarioMode(true,false,true);
    let ghosts=[],currentAttempt=null,shotCount=0;
    const message=text=>{status.textContent=text;};
    const clock=()=>elapsed+(active&&!paused?performance.now()-start:0);
    const state=()=>core.getTrainingState();
    function beginAttempt(time){
        const [x,y]=targetAt(config,session.trainerWidth,session.trainerHeight,time,hits);
        return {attemptId:crypto.randomUUID(),target:{x,y,radius:config.radius,appearTime:time,cursorStartX:cursor.x,cursorStartY:cursor.y},movements:[],clicks:[],tracking_samples:[]};
    }
    function freezeAttempt(time){if(currentAttempt){currentAttempt.target.disappearTime=time;session.attempts.push(currentAttempt);currentAttempt=null;}}
    function showTarget(time){
        const [x,y]=targetAt(config,session.trainerWidth,session.trainerHeight,time,hits);
        target.style.left=`${x}px`;target.style.top=`${y}px`;target.style.width=`${config.radius*2}px`;target.style.height=`${config.radius*2}px`;target.style.display="block";
    }
    function clear(){cancelAnimationFrame(frame);target.style.display="none";target.style.width="50px";target.style.height="50px";document.getElementById("start-message").style.display="block";for(const ghost of ghosts)ghost.remove();ghosts=[];core.setScenarioMode(select.value!=="legacy",false);}
    async function save(){
        if(!pending||saving)return;
        saving=true;const report=pending;
        try{
            const response=await requestJson("/scenarios/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(report)});
            if(!response.saved||response.session_id!==report.sessionId)throw new Error("保存确认 ID 不一致");
            localStorage.removeItem("aimcoachScenarioPending");pending=null;
            const result=response.result;
            lastResult=result;
            lastSavedSession=report.sessionId;lastSavedStatus=report.status;
            document.getElementById("scenarioResult").textContent=[`Overall Score: ${result.overall_score} / 100`,
                `场景：${result.scenario_id} · 难度：${result.difficulty} · ${result.scoring_version}`,
                result.eligible?"有效完成成绩":"未完成或样本不足，综合分为 0",
                `主要优势：${result.strength||"样本不足"} · 最明显薄弱点：${result.weakness||"样本不足"}`,
                `同条件历史：${result.previous_same_condition.count} 次 · 历史最高 ${result.previous_same_condition.best_score??"暂无"}`,
                `核心指标（ms / px / 次每秒；比例为0–1）：\n${JSON.stringify(result.metrics,null,2)}`,
                "数据来自客户端报告，不构成防作弊或跨条件排名。"].join("\n");
            message("数据库已确认保存；评分为当前场景/难度/版本结果。不可跨条件排名。");
            core.setScenarioMode(select.value!=="legacy",false,false);await core.refreshTrainingState();
        }catch(error){message(!pending&&lastResult?`数据库已确认保存；进度同步失败：${error.message}`:`保存或本地清理未确认：${error.message}；${memoryOnly?"仅在内存，请立即导出并保持页面打开。":"保留待上传数据，请重试。"}`);}
        finally{saving=false;core.setScenarioMode(select.value!=="legacy",false,!!pending||!!corrupt);}
    }
    async function finish(completed=false){
        if(!active)return;
        session.elapsed_ms=Math.min(clock(),config.duration_seconds*1000);
        if(config.category==="tracking" && (currentAttempt?.tracking_samples.length||0)<config.duration_seconds*20*.9)completed=false;
        session.status=completed?"complete":"incomplete";
        session.startTime=0;session.endTime=session.elapsed_ms;freezeAttempt(session.elapsed_ms);
        session.endedAt=new Date().toISOString();active=false;paused=false;clear();pending=session;core.setScenarioMode(true,false,true);
        try{localStorage.setItem("aimcoachScenarioPending",JSON.stringify(pending));}catch{memoryOnly=true;message("仅有内存副本，请勿关闭页面；立即重试保存。");}
        await save();
    }
    function tick(){
        if(!active||paused)return;
        const time=Math.min(clock(),config.duration_seconds*1000);
        showTarget(time);
        if(config.category==="tracking" && time>=nextSample){
            // At most one sample per frame, never interpolate missing input into fictitious evidence.
            currentAttempt.tracking_samples.push({time,x:cursor.x,y:cursor.y});nextSample=(Math.floor(time/50)+1)*50;
        }
        if(time>=config.duration_seconds*1000){void finish(config.category==="tracking");return;}
        frame=requestAnimationFrame(tick);
    }
    select.addEventListener("change",()=>{
        if(active){select.value=config.scenario_id;message("请先结束当前场景。");return;}
        core.setScenarioMode(select.value!=="legacy",false,!!pending||!!corrupt);
    });
    document.getElementById("scenarioStart").addEventListener("click",async()=>{
        if(active||starting||pending||corrupt||saving||!core.canLeaveTrainingPage()||state().sessionSaving||state().pending)return;
        if(select.value==="legacy"){message("原 Flick 使用训练区域及 Phase 1 控制按钮。");return;}
        starting=true;startError=null;lastResult=null;document.getElementById("scenarioResult").textContent="";core.setScenarioMode(true,true);
        try{
            const scenario={scenario_id:select.value,difficulty:document.getElementById("scenarioDifficulty").value,
                seed:Number(document.getElementById("scenarioSeed").value),duration_seconds:Number(document.getElementById("scenarioDuration").value),
                target_count:Number(document.getElementById("scenarioCount").value),speed:Number(document.getElementById("scenarioSpeed").value),direction:document.getElementById("scenarioDirection").value};
            const experiment=await requestJson("/scenarios/experiments",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({trainer_width:area.clientWidth,trainer_height:area.clientHeight,scenario,preferences:readSettings()||{}})});
            if(experiment.status==="complete"||experiment.ended)throw new Error("请先结束/新建实验。");
            // Use the server-frozen protocol for motion, evidence and persistence. Form edits cannot change it.
            config=experiment.protocol.scenario;
            session={schemaVersion:"scenario-1",sessionId:crypto.randomUUID(),experimentId:experiment.experiment_id,experimentOrder:experiment.completed_count+1,
                startedAt:new Date().toISOString(),startTime:0,trainerSensitivity:1.0,sensitivityRepeat:experiment.next_condition.repeat,targetCount:config.category==="static"?config.target_count:1,trainerWidth:area.clientWidth,trainerHeight:area.clientHeight,scenarioSnapshot:config,settingsSnapshot:experiment.protocol.settings,attempts:[]};
            core.selectScenarioExperiment(experiment);
            lastResult=null;document.getElementById("scenarioResult").textContent="";
            active=true;paused=false;hits=0;elapsed=0;nextSample=0;start=performance.now();cursor={x:area.clientWidth/2,y:area.clientHeight/2};shotCount=0;currentAttempt=beginAttempt(0);
            core.setScenarioMode(true,true);area.scrollIntoView({block:"start",behavior:"instant"});document.getElementById("start-message").style.display="none";
            const count=config.scenario_id==="four_target_switching"?4:config.scenario_id==="six_target_precision"?6:0;
            for(let i=0;i<count;i++){const node=document.createElement("div");node.className="scenario-ghost";const [x,y]=targetAt(config,area.clientWidth,area.clientHeight,0,i);Object.assign(node.style,{left:`${x}px`,top:`${y}px`,width:`${config.radius*2}px`,height:`${config.radius*2}px`});area.append(node);ghosts.push(node);}
            message("场景进行中：普通指针定位；P 暂停/继续，E 提前结束。激活目标为实心圆。");tick();
        }catch(error){startError=error.message;message(`无法开始：${error.message}`);}
        finally{starting=false;core.setScenarioMode(select.value!=="legacy",active,!!pending||!!corrupt);}
    });
    area.addEventListener("mousemove",event=>{
        if(!active||paused)return;
        const rect=area.getBoundingClientRect();cursor={x:Math.max(0,Math.min(session.trainerWidth,event.clientX-rect.left)),y:Math.max(0,Math.min(session.trainerHeight,event.clientY-rect.top))};
        crosshair.style.left=`${cursor.x}px`;crosshair.style.top=`${cursor.y}px`;
    });
    area.addEventListener("mousedown",event=>{
        if(!active||paused||event.button!==0||config.category!=="static")return;
        const time=clock();if(time>config.duration_seconds*1000)return;
        const rect=area.getBoundingClientRect();cursor={x:Math.max(0,Math.min(session.trainerWidth,event.clientX-rect.left)),y:Math.max(0,Math.min(session.trainerHeight,event.clientY-rect.top))};
        const [x,y]=targetAt(config,session.trainerWidth,session.trainerHeight,time,hits);
        const hit=Math.hypot(cursor.x-x,cursor.y-y)<=config.radius;
        shotFeedback(hit,session.settingsSnapshot,area);
        if(shotCount>=2000){void finish(false);return;}
        currentAttempt.clicks.push({time,x:cursor.x,y:cursor.y,hit});shotCount++;
        if(hit){freezeAttempt(time);hits++;}
        if(hits===config.target_count){void finish(true);return;}if(hit)currentAttempt=beginAttempt(time);showTarget(time);
    });
    function pause(){if(!active)return;if(paused){paused=false;start=performance.now();tick();}else{elapsed=clock();paused=true;cancelAnimationFrame(frame);}core.setScenarioMode(true,true,!!pending,paused);message(paused?"已暂停，P 继续。":"继续训练。");}
    document.getElementById("scenarioPause").addEventListener("click",pause);
    document.getElementById("scenarioEnd").addEventListener("click",()=>{void finish(false);});
    document.getElementById("scenarioExport").addEventListener("click",()=>{
        let raw;try{raw=pending?JSON.stringify(pending,null,2):localStorage.getItem("aimcoachScenarioPending");}catch{raw=JSON.stringify(pending);}
        if(!raw){message("没有待导出的场景报告。");return;}
        const url=URL.createObjectURL(new Blob([raw],{type:"application/json"}));const link=document.createElement("a");link.href=url;link.download="aimcoach-scenario-pending.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
        message("已触发导出，队列未删除；请确认文件已保存。");
    });
    document.getElementById("scenarioRetry").addEventListener("click",()=>{void save();});
    document.addEventListener("keydown",event=>{if(!active||event.repeat||event.target?.closest?.("input,select,textarea,dialog"))return;
        if(event.code==="KeyP")pause();
        if(event.code==="Escape"&&document.body?.dataset.phase4){if(!paused)pause();return;}
        if(event.code==="KeyE"&&document.body?.dataset.phase4){document.getElementById("arenaExit").click();return;}
        if(event.code==="KeyE"||event.code==="Escape")void finish(false);});
    // Background time is never treated as deliberate practice or synthesized tracking coverage.
    window.addEventListener("blur",()=>{if(active&&!paused)pause();});
    document.addEventListener("visibilitychange",()=>{if(document.hidden&&active&&!paused)pause();});
    window.addEventListener("resize",()=>{if(active&&(area.clientWidth!==session.trainerWidth||area.clientHeight!==session.trainerHeight))void finish(false);});
    window.addEventListener("beforeunload",event=>{if(active){
        session.elapsed_ms=Math.min(clock(),config.duration_seconds*1000);session.status="incomplete";session.startTime=0;session.endTime=session.elapsed_ms;freezeAttempt(session.elapsed_ms);session.interruptionReason="page_exit";session.endedAt=new Date().toISOString();
        // A cancelled navigation must not keep mutating an ID that has already entered the queue.
        active=false;paused=false;clear();pending=session;core.setScenarioMode(true,false,true);
        try{localStorage.setItem("aimcoachScenarioPending",JSON.stringify(pending));}catch{memoryOnly=true;event.preventDefault();event.returnValue="";}
        message("退出时的场景记录已停止；如留在本页，请重试保存或导出。");
    }});
    void requestJson("/scenarios").then(items=>{for(const item of items){const option=document.createElement("option");option.value=item.scenario_id;option.textContent=item.name;select.append(option);}const current=state().experiment?.protocol.scenario;if(current){select.value=current.scenario_id;core.setScenarioMode(true,false,!!pending||!!corrupt);
        for(const [key,id] of Object.entries({difficulty:"scenarioDifficulty",seed:"scenarioSeed",duration_seconds:"scenarioDuration",target_count:"scenarioCount",speed:"scenarioSpeed",direction:"scenarioDirection"}))document.getElementById(id).value=current[key];}if(pending||corrupt)message(corrupt||"发现待保存场景记录，请点击重试。");}).catch(error=>message(`场景库不可用：${error.message}`));
    // The workspace observes lifecycle state without taking ownership of clocks or evidence.
    return {pause,finish,clearError:()=>{startError=null;},getState:()=>({active,paused,starting,saving,error:startError,pending:!!pending||!!corrupt,
        sessionId:session?.sessionId||pending?.sessionId||lastSavedSession,status:session?.status||pending?.status||lastSavedStatus,result:lastResult,hits,shotCount,
        elapsedMs:active?clock():session?.elapsed_ms||0,durationMs:config?.duration_seconds*1000,
        targetCount:config?.target_count,scenarioId:config?.scenario_id,experimentOrder:session?.experimentOrder,
        samples:currentAttempt?.tracking_samples.length||session?.attempts?.[0]?.tracking_samples?.length||0})};
}
