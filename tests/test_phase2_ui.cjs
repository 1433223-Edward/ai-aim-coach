const {test}=require("node:test");
const assert=require("node:assert/strict");
const {page}=require("./scenario_harness.cjs");
const ids=["four_target_switching","six_target_precision","horizontal_tracking","vertical_tracking","flick_target","reactive_tracking"];

test("all six shipped scene handlers start, collect bounded evidence, finish and display only the server score",async()=>{
    const {targetAt}=await import("../frontend/scenario_motion.js");
    for(const key of ids){
        const p=await page();p.node("scenarioSelect").value=key;await p.emit("scenarioSelect","change");await p.emit("scenarioStart","click");
        assert.equal(p.core.canLeaveTrainingPage(),false);
        if(p.config.category==="static"){
            for(let index=0;index<5;index++){
                p.setTime((index+1)*400);
                await p.emit("trainer-area","mousedown",{button:0,clientX:parseFloat(p.node("target").style.left),clientY:parseFloat(p.node("target").style.top)});
            }
        }else{
            for(let time=50;time<=10000;time+=50){const [x,y]=targetAt(p.config,800,600,time);await p.emit("trainer-area","mousemove",{clientX:x,clientY:y});await p.advance(time);}
        }
        assert.equal(p.uploads.length,1,key);const saved=p.uploads[0];
        assert.equal(saved.status,"complete");assert.ok(saved.attempts.every(attempt=>attempt.movements.length===0));
        if(p.config.category==="tracking"){
            const samples=saved.attempts[0].tracking_samples;assert.ok(samples.length<=201);assert.equal(new Set(samples.map(sample=>Math.floor(sample.time/50))).size,samples.length);
        }else assert.equal(saved.attempts.length,5);
        assert.match(p.node("scenarioResult").textContent,/42 \/ 100/);
        assert.equal(p.storage.has("aimcoachScenarioPending"),false);
        assert.equal(p.core.canLeaveTrainingPage(),true);
    }
});

test("pause excludes wall waiting and early end remains incomplete",async()=>{
    const p=await page();p.node("scenarioSelect").value="horizontal_tracking";await p.emit("scenarioStart","click");
    await p.emit("scenarioPause","click");await p.advance(5000);assert.equal(p.uploads.length,0);
    await p.emit("scenarioPause","click");await p.advance(5050);await p.emit("scenarioEnd","click");
    assert.equal(p.uploads[0].status,"incomplete");assert.equal(p.uploads[0].elapsed_ms,50);
    assert.equal(p.uploads[0].attempts[0].tracking_samples.length,2);
});


test("failed save survives reload and retries the exact evidence before allowing another run",async()=>{
    const p=await page();p.setSaveFailure(true);
    p.node("scenarioSelect").value="four_target_switching";await p.emit("scenarioStart","click");
    p.setTime(100);await p.emit("scenarioEnd","click");
    const queued=p.storage.get("aimcoachScenarioPending");assert.ok(queued);
    assert.match(p.node("scenarioStatus").textContent,/未确认/);
    await p.emit("scenarioStart","click");assert.equal(p.uploads.length,1);
    const restored=await page(null,p.storage);await restored.emit("scenarioRetry","click");
    assert.deepEqual(restored.uploads[0],JSON.parse(queued));
    assert.equal(restored.storage.has("aimcoachScenarioPending"),false);
});

test("scenario selection updates the real controller identity and legacy mode can create again after completion",async()=>{
    const {Server,boot}=require("./frontend_harness.cjs");
    const server=new Server(),p=await boot(server);
    const state=JSON.parse(p.evaluate("JSON.stringify(activeExperiment)"));
    state.experiment_id="scenario-test";state.protocol.scenario={scenario_id:"flick_target"};
    state.completed_count=state.schedule.length;state.completed_session_ids=state.schedule.map((_,i)=>`saved-${i}`);state.status="complete";state.next_condition=null;
    p.evaluate(`selectScenarioExperiment(${JSON.stringify(state)})`);
    assert.equal(p.tabStorage.get("aimcoachSelectedExperimentId"),"scenario-test");
    p.evaluate("setScenarioMode(false)");await p.click();
    assert.notEqual(p.evaluate("activeExperiment.experiment_id"),"scenario-test");
    assert.equal(p.evaluate("sessionActive"),false);
});
