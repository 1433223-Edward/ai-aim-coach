const {test}=require("node:test"),assert=require("node:assert/strict"),vm=require("node:vm");
const {loadModule}=require("./esm_loader.cjs");
const {page}=require("./scenario_harness.cjs");

test("Focus lifecycle keeps ACTIVE, pause, save failure and confirmed Summary distinct",async()=>{
    const module=await loadModule("workspace.js",vm.createContext({}));await module.evaluate();
    const phase=module.namespace.arenaPhase;
    const base={pending:false,sessionSaving:false,experiment:{completed_session_ids:[]}},scene={};
    assert.equal(phase(base,scene,new Set()),"READY");
    assert.equal(phase(base,{active:true},new Set()),"ACTIVE");
    assert.equal(phase(base,{active:true,paused:true},new Set()),"PAUSED");
    assert.equal(phase(base,{saving:true,pending:true},new Set()),"SAVING");
    assert.equal(phase(base,{pending:true},new Set()),"ERROR");
    assert.equal(phase(base,{error:"Experiment conflict"},new Set()),"ERROR");
    assert.equal(phase(base,{result:{overall_score:0},status:"incomplete",sessionId:"one"},new Set()),"ABORTED");
    assert.equal(phase(base,{result:{overall_score:80},status:"complete",sessionId:"one"},new Set()),"COMPLETED");
    assert.equal(phase(base,{result:{},status:"complete",sessionId:"one"},new Set(["one"])),"READY");
});

test("charts isolate full condition keys and ignore incomplete or abnormal scores",async()=>{
    const module=await loadModule("history_views.js",vm.createContext({}));await module.evaluate();
    const {comparableGroups,sceneSummary}=module.namespace;
    const row=(id,key,value,status="complete")=>({session_id:id,scenario_id:"flick_target",started_at:`2026-11-0${id}`,
        status,result:{eligible:true,overall_score:value,condition_key:key,difficulty:"normal",scoring_version:"v1"}});
    const rows=[row("1","A",90),row("2","B",30),row("3","B",50),row("4","B",100,"incomplete"),row("5","B",100,"abnormal")];
    assert.equal(comparableGroups(rows).size,2);
    const summary=sceneSummary(rows,"flick_target");
    assert.equal(summary.count,3);assert.equal(summary.latest.session_id,"3");assert.equal(summary.best,50);
    rows.push({...row("6","B",99),result:{...rows[1].result,scoring_version:"v2",overall_score:99}});
    assert.equal(comparableGroups(rows).size,3);
});

test("loss of window focus pauses tracking and does not synthesize background samples",async()=>{
    const p=await page();p.node("scenarioSelect").value="horizontal_tracking";await p.emit("scenarioStart","click");
    await p.advance(100);await p.emit("window","blur");await p.advance(5100);
    assert.equal(p.uploads.length,0);
    await p.emit("scenarioPause","click");await p.advance(5150);await p.emit("scenarioEnd","click");
    assert.equal(p.uploads[0].elapsed_ms,150);
    assert.equal(p.uploads[0].status,"incomplete");
    assert.ok(p.uploads[0].attempts[0].tracking_samples.length<=4);
});

test("all management hash routes work and ACTIVE Arena navigation remains blocked",async()=>{
    const listeners=new Map(),window={location:{hash:"#/hub"},history:{replaceState(_a,_b,hash){window.location.hash=hash;}},
        addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener(){}};
    const module=await loadModule("navigation.js",vm.createContext({}));await module.evaluate();
    const pages=[];let safe=true;
    const router=module.namespace.createNavigation({window,showPage:async page=>pages.push(page),canNavigate:()=>safe,onBlocked(){}});
    await router.start();
    for(const hash of ["#/coach","#/history","#/arena"]){window.location.hash=hash;await router.start();}
    assert.deepEqual(pages,["hub","coach","history","test"]);
    safe=false;window.location.hash="#/history";await router.start();assert.equal(window.location.hash,"#/test");
});

test("entering Arena suspends history fetching and chart refresh work",async()=>{
    const module=await loadModule("history_views.js",vm.createContext({fetch:()=>{throw Error("No history requests during Arena");}}));await module.evaluate();
    const nodes=new Map(),document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,{addEventListener(){}});return nodes.get(id);}};
    const views=module.namespace.createHistoryViews(document,()=>{});views.setSuspended(true);
    await views.refresh();await views.refreshHistory();
});

test("failed history filters remove stale charts instead of relabeling old evidence",async()=>{
    const module=await loadModule("history_views.js",vm.createContext({URLSearchParams,AbortController,setTimeout,clearTimeout,
        fetch:async()=>({ok:false,status:503,json:async()=>({detail:"Storage unavailable"})})}));await module.evaluate();
    const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,{value:"",children:["old-score"],textContent:"",addEventListener(){},replaceChildren(...values){this.children=values;}});return nodes.get(id);};
    const document={getElementById:node,createElement:tag=>({tag,textContent:""})};
    const views=module.namespace.createHistoryViews(document,()=>{});await views.refreshHistory();
    assert.equal(node("historyCondition").disabled,true);
    assert.match(node("historyStatus").textContent,/查询失败/);
    for(const id of ["historyCharts","abilityOverview","sessionList","experimentList"]){assert.notEqual(node(id).children[0],"old-score");assert.match(node(id).children[0].textContent,/未展示旧结果/);}
});

test("Phase 4 Hub restoration is read-only and legacy background time remains paused",async()=>{
    const {Server,boot}=require("./frontend_harness.cjs");
    const server=new Server();server.phase4=true;server.now=100;
    const p=await boot(server);await p.evaluate("ready");
    assert.equal(server.creations,0);assert.equal(p.evaluate("getTrainingState().experiment"),null);
    await p.click();assert.equal(server.creations,1);assert.equal(p.evaluate("sessionActive"),false);
    await p.click();assert.equal(p.evaluate("sessionActive"),true);
    server.now=1000;await p.event("blur");
    assert.equal(p.evaluate("paused"),true);
    const elapsed=p.evaluate("getTrainingState().activeDurationMs");
    server.now=10000;assert.equal(p.evaluate("getTrainingState().activeDurationMs"),elapsed);
});

test("exit confirmation is nonblocking, cancel preserves pause and accept archives incomplete evidence",async()=>{
    const module=await loadModule("workspace.js",vm.createContext({}));await module.evaluate();
    const nodes=new Map();
    function element(id){if(!nodes.has(id))nodes.set(id,{value:"",textContent:"",hidden:false,open:false,dataset:{},children:[],selectedOptions:[],
        handlers:new Map(),classList:{toggle(){},remove(){}},append(...values){this.children.push(...values);},
        replaceChildren(...values){this.children=values;},setAttribute(){},
        addEventListener(type,fn,options={}){this.handlers.set(type,[...this.handlers.get(type)||[],{fn,once:options.once}]);},
        click(){return Promise.all((this.handlers.get("click")||[]).map(h=>h.fn()));},
        showModal(){this.open=true;},close(value){this.open=false;this.returnValue=value;const callbacks=this.handlers.get("close")||[];this.handlers.set("close",callbacks.filter(h=>!h.once));for(const h of callbacks)h.fn();}});return nodes.get(id);}
    const document={body:{dataset:{},classList:{toggle(){},remove(){}}},getElementById:element,createElement:()=>element(Symbol("node"))};
    const window={location:{hash:"#/arena"},setInterval(){},confirm(){throw Error("Native blocking confirmations must not be used");}};
    element("scenarioSelect").value="horizontal_tracking";element("scenarioSelect").selectedOptions=[{textContent:"Horizontal Tracking"}];
    const state={active:true,paused:false,saving:false,pending:false,elapsedMs:100,durationMs:10000,scenarioId:"horizontal_tracking",sessionId:"run-1"};
    let aborted=0;
    const scene={getState:()=>state,pause(){state.paused=!state.paused;},async finish(completed){assert.equal(completed,false);aborted++;state.active=false;state.pending=true;},clearError(){}};
    const core={getTrainingState:()=>({sessionActive:false,experiment:{completed_count:0,completed_session_ids:[],schedule:[{}],protocol:{}}}),subscribeTrainingState(fn){fn();}};
    const workspace=module.namespace.createWorkspace(document,window);workspace.bind(core,scene);workspace.setPage("test");
    const first=element("arenaExit").click();assert.equal(state.paused,true);assert.equal(element("arenaConfirm").open,true);
    await element("arenaConfirmCancel").click();await first;
    assert.equal(aborted,0);assert.equal(state.paused,true);
    const second=element("arenaExit").click();await element("arenaConfirmAccept").click();await second;
    assert.equal(aborted,1);assert.equal(state.active,false);assert.equal(state.pending,true);
});
