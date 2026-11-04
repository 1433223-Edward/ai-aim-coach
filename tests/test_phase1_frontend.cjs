const {test}=require("node:test");
const assert=require("node:assert/strict");
const vm=require("node:vm");
const {loadModule}=require("./esm_loader.cjs");
const {Server,boot,flush}=require("./frontend_harness.cjs");

test("settings reject malformed preferences and preserve valid values across reload",async()=>{
    const values=new Map();
    const context=vm.createContext({localStorage:{getItem:key=>values.get(key)||null}});
    const module=await loadModule("settings.js",context);await module.evaluate();
    const {validateSettings,readSettings}=module.namespace;
    for(const value of [{sessions:0},{attempts:101},{sensitivity:Infinity},{crosshair_type:"Triangle"},{hit_sound:1},{crosshair_size:3},{countdown_seconds:11}])assert.throws(()=>validateSettings(value));
    values.set("aimcoachSettings",JSON.stringify({sessions:2,attempts:5,sensitivity:1.2,hit_feedback:true}));
    assert.equal(readSettings().attempts,5);assert.equal(readSettings().crosshair_type,"Circle");
    values.set("aimcoachSettings","{");assert.equal(readSettings(),null);
});

test("pause retains completed attempts, resume creates a new active attempt, stop queues a distinct incomplete report",async()=>{
    const server=new Server(),page=await boot(server);
    await page.click();
    page.evaluate("crosshairX=targetX;crosshairY=targetY");
    await page.documentEvent("mousedown",{button:0});
    assert.equal(page.evaluate("currentSession.attempts.length"),1);
    await page.button("pauseTraining");
    assert.equal(page.evaluate("paused"),true);assert.equal(page.evaluate("currentAttempt"),null);
    assert.equal(page.evaluate("currentSession.pausedAttempts.length"),1);
    await page.click();assert.equal(page.evaluate("paused"),false);
    assert.equal(page.evaluate("currentSession.attempts.length"),1);
    const pending=page.evaluate('finishIncomplete("user_ended")');
    await flush();
    assert.equal(page.evaluate("sessionActive"),false);
    assert.equal(page.evaluate("pendingQueue.list().sessions[0].status"),"incomplete");
    assert.equal(page.evaluate("pendingQueue.list().sessions[0].attempts.length"),1);
    assert.equal(page.evaluate("currentExperimentIndex"),0);
    await pending;
});

test("countdown collects no attempts; expiry archives an incomplete report",async()=>{
    const server=new Server();server.create().protocol.settings={countdown_seconds:2,time_limit_seconds:1};server.manualTimers=[];
    const page=await boot(server);const starting=page.click();await flush();
    assert.equal(page.evaluate("sessionActive"),false);assert.equal(page.evaluate("countdown"),true);
    async function fire(){const timer=server.manualTimers.find(item=>item.fn);assert.ok(timer);const callback=timer.fn;timer.fn=null;callback();await flush();}
    await fire();assert.equal(page.evaluate("sessionActive"),false);
    await fire();await starting;assert.equal(page.evaluate("sessionActive"),true);
    await fire();await flush();
    assert.equal(page.evaluate("sessionActive"),false);
    assert.equal(page.evaluate("currentSession.interruptionReason"),"time_limit");
    assert.equal(page.evaluate("currentSession.attempts.length"),0);
});

test("time limit excludes paused time instead of changing attempt timestamps",async()=>{
    const server=new Server();server.create().protocol.settings={countdown_seconds:0,time_limit_seconds:1};server.manualTimers=[];server.now=100;
    const page=await boot(server);await page.click();server.now=600;await page.button("pauseTraining");
    assert.equal(server.manualTimers.filter(item=>item.fn).length,0);
    server.now=5000;await page.click();
    assert.equal(server.manualTimers.find(item=>item.fn).ms,500);
    await page.evaluate('finishIncomplete("user_ended")');
    assert.equal(page.evaluate("currentSession.activeDurationMs"),500);
});

test("page exit archives once even if navigation is cancelled and retried",async()=>{
    const page=await boot(new Server());await page.click();
    await page.event("beforeunload",{preventDefault(){}});
    const raw=page.evaluate("JSON.stringify(pendingQueue.list().sessions[0])");
    assert.equal(page.evaluate("sessionActive"),false);
    assert.equal(JSON.parse(raw).interruptionReason,"page_exit");
    await page.event("beforeunload",{preventDefault(){}});
    assert.equal(page.evaluate("JSON.stringify(pendingQueue.list().sessions[0])"),raw);
});
