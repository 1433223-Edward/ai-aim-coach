const {test}=require("node:test");
const assert=require("node:assert/strict");
const vm=require("node:vm");
const {loadModule}=require("./esm_loader.cjs");
const flush=()=>new Promise(resolve=>setImmediate(resolve));

async function page(storage=new Map()) {
    const nodes=new Map(),handlers=new Map(),requests=[];
    let listener, experiment=null, pending=false, mode=false, unavailable=false;
    const plan={plan_id:"fake-plan-1",plan_status:"proposed",scenario_id:"flick_target",sessions:2,
        task_parameters:{scenario_id:"flick_target",difficulty:"easy",seed:1,duration_seconds:10,target_count:5,speed:120,direction:"positive"},
        evidence:{snapshot_id:"test-snapshot"},evidence_session_ids:[],rationale:{sufficient:false},agent:{usage:{client_kind:"fake_client"}}};
    const node=id=>{if(!nodes.has(id))nodes.set(id,{value:"",textContent:"",disabled:false,clientWidth:800,clientHeight:600,
        addEventListener:(type,fn)=>handlers.set(`${id}:${type}`,fn)});return nodes.get(id);};
    const document={getElementById:node};
    for(const [id,value] of Object.entries({trainingPlanGoal:"提高准确率",trainingPlanBudget:"30",trainingPlanSessions:"2",
        trainingPlanAttempts:"5",trainingPlanDuration:"10",trainingPlanDifficulty:"easy"}))node(id).value=value;
    const core={getTrainingState:()=>({experiment,pending,sessionSaving:false}),canLeaveTrainingPage:()=>true,
        selectScenarioExperiment:value=>{experiment=value;listener?.(core.getTrainingState());},
        setScenarioMode:value=>{mode=value;},subscribeTrainingState:fn=>{listener=fn;fn(core.getTrainingState());}};
    const context=vm.createContext({document,console,AbortController,setTimeout,clearTimeout,
        localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
        fetch:async(url,options={})=>{
            requests.push({url,...options});
            if(unavailable)return {ok:false,status:503,json:async()=>({detail:"Model is unavailable"})};
            let body=structuredClone(plan);
            if(url.endsWith("/accept")){
                plan.plan_status="accepted";plan.experiment_id="execution-1";
                body={plan:structuredClone(plan),experiment:{experiment_id:"execution-1",completed_count:0,
                    protocol:{scenario:plan.task_parameters,training_plan_id:plan.plan_id}}};
            }else if(url.endsWith("/reject")){plan.plan_status="rejected";body=structuredClone(plan);}
            else if(url.endsWith("/evaluate"))body={sufficient:false,completion_rate:experiment?.completed_count/2||0};
            return {ok:true,status:200,json:async()=>body};
        }});
    const module=await loadModule("training_plans.js",context);await module.evaluate();
    module.namespace.bindTrainingPlans(core,document);await flush();await flush();
    return {node,requests,storage,core,emit:async id=>{await handlers.get(`${id}:click`)();await flush();await flush();},
        setPending:value=>{pending=value;},setUnavailable:()=>{unavailable=true;},
        progress:async count=>{experiment.completed_count=count;listener(core.getTrainingState());await flush();await flush();},
        get mode(){return mode;}};
}

test("request shows proposal, limits and Fake Client identity without creating or starting an Experiment",async()=>{
    const p=await page();await p.emit("trainingPlanRequest");
    assert.equal(p.requests.length,1);
    const body=JSON.parse(p.requests[0].body);
    assert.equal(body.bounds.time_budget_seconds,30);assert.equal(body.bounds.max_attempts,5);
    assert.equal(p.node("trainingPlanAccept").disabled,false);
    assert.match(p.node("trainingPlanResult").textContent,/fake_client/);
    assert.equal(p.storage.get("aimcoachTrainingPlanId"),"fake-plan-1");
});

test("acceptance passes user preferences, selects frozen execution and evaluates progress without auto-start",async()=>{
    const p=await page(new Map([["aimcoachSettings",JSON.stringify({crosshair_color:"#ff0000"})]]));
    await p.emit("trainingPlanRequest");await p.emit("trainingPlanAccept");
    assert.equal(p.core.getTrainingState().experiment.protocol.training_plan_id,"fake-plan-1");
    assert.equal(p.node("scenarioDifficulty").value,"easy");assert.equal(p.mode,true);
    const accept=p.requests.find(r=>r.url.endsWith("/accept"));
    assert.equal(JSON.parse(accept.body).preferences.crosshair_color,"#ff0000");
    assert.equal(p.requests.some(r=>r.url==="/scenarios/experiments"||r.url==="/scenarios/sessions"),false);
    await p.progress(1);
    assert.ok(p.requests.filter(r=>r.url.endsWith("/evaluate")).length>=2);
    assert.match(p.node("trainingPlanStatus").textContent,/证据不足/);
});

test("reject and reload preserve server identity; pending evidence and model failures stay explicit",async()=>{
    const p=await page();await p.emit("trainingPlanRequest");p.setPending(true);await p.emit("trainingPlanAccept");
    assert.equal(p.requests.some(r=>r.url.endsWith("/accept")),false);
    p.setPending(false);await p.emit("trainingPlanReject");
    assert.equal(p.node("trainingPlanAccept").disabled,true);
    const restored=await page(p.storage);assert.match(restored.requests[0].url,/fake-plan-1/);
    restored.setUnavailable();await restored.emit("trainingPlanRequest");
    assert.match(restored.node("trainingPlanStatus").textContent,/Model is unavailable/);
});
