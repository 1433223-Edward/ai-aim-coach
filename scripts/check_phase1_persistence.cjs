// Exercise shipped ESM training handlers against a real FastAPI process and disposable SQLite.
// DOM/input are simulated; HTTP, validation, transactions and disk persistence are real.
const {boot}=require("../tests/frontend_harness.cjs");
const assert=require("node:assert/strict");
const {spawn,execFileSync}=require("node:child_process");
const fs=require("node:fs");
const os=require("node:os");
const path=require("node:path");
const net=require("node:net");
const {setTimeout:delay}=require("node:timers/promises");
const python=process.env.AIMCOACH_TEST_PYTHON||"D:/Anaconda/envs/aimcoach/python.exe";
const root=path.resolve(__dirname,"..");

async function run(){
    const directory=fs.mkdtempSync(path.join(os.tmpdir(),"aimcoach-phase1-"));
    const database=path.join(directory,"training.db");
    const reserve=net.createServer();await new Promise(resolve=>reserve.listen(0,"127.0.0.1",resolve));
    const port=reserve.address().port;await new Promise(resolve=>reserve.close(resolve));
    const base=`http://127.0.0.1:${port}`;
    let server=null;
    async function start(){
        server=spawn(python,["-B","-m","uvicorn","backend.main:app","--host","127.0.0.1","--port",String(port)],
            {cwd:root,env:{...process.env,AIMCOACH_DB_PATH:database,DASHSCOPE_API_KEY:"",PYTHONDONTWRITEBYTECODE:"1"},stdio:"ignore"});
        for(let i=0;i<100;i++){
            try{if((await fetch(base+"/health")).ok)return;}catch{}
            if(server.exitCode!==null)throw new Error("Isolated API failed to start");
            await delay(100);
        }throw new Error("API startup deadline exceeded");
    }
    async function stop(){if(server && server.exitCode===null){const exited=new Promise(resolve=>server.once("exit",resolve));server.kill();await exited;}server=null;}
    const facade={storage:new Map(),requests:[],async fetch(url,options){this.requests.push(url);return fetch(base+url,options);}};
    facade.storage.set("aimcoachSettings",JSON.stringify({sessions:2,attempts:2,sensitivity:1.2}));
    const waitFor=async(predicate)=>{for(let i=0;i<100;i++){if(predicate())return;await delay(50);}throw new Error("Frontend transition deadline exceeded");};
    try{
        await start();
        const page=await boot(facade);await page.evaluate("ready");
        const experiment=page.evaluate("activeExperiment.experiment_id");
        await page.click();
        for(let i=0;i<2;i++){
            page.evaluate("crosshairX=targetX;crosshairY=targetY");
            await page.documentEvent("mousedown",{button:0});
        }
        await waitFor(()=>page.evaluate("!sessionSaving && currentExperimentIndex===1"));
        const session=JSON.parse(page.evaluate("JSON.stringify(currentSession)"));
        const retry=await fetch(base+"/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(session)});
        assert.equal(retry.status,200);
        const conflict=await fetch(base+"/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...session,extraTag:"changed"})});
        assert.equal(conflict.status,409);
        await page.click();
        page.evaluate("crosshairX=targetX;crosshairY=targetY");await page.documentEvent("mousedown",{button:0});
        await page.button("pauseTraining");assert.equal(page.evaluate("paused"),true);
        await page.click();assert.equal(page.evaluate("paused"),false);
        await page.evaluate('finishIncomplete("user_ended")');
        assert.equal(page.evaluate("pendingQueue.list().sessions.length"),0);
        assert.equal(page.evaluate("currentExperimentIndex"),1);
        const partialId=page.evaluate("currentSession.sessionId");
        const partial=await (await fetch(base+`/history/sessions/${partialId}`)).json();
        assert.equal(partial.status,"incomplete");assert.equal(partial.statistics,null);
        const detail=await (await fetch(base+`/history/sessions/${session.sessionId}`)).json();
        assert.equal(detail.statistics.attempt_count,2);
        assert.equal(new Set(detail.session.attempts.map(item=>item.attemptId)).size,2);
        const analysis=await (await fetch(base+`/experiments/${experiment}/analysis`)).json();
        assert.equal(analysis.evidence.session_count,1);assert.equal(analysis.evidence.attempt_count,2);
        assert.equal((await fetch(base+"/history/sessions?limit=0")).status,422);
        assert.equal((await fetch(base+"/history/sessions?date=not-a-date")).status,422);
        assert.equal((await fetch(base+"/sessions/incomplete",{method:"POST",headers:{"Content-Type":"application/json"},body:'{"targetCount":NaN}'})).status,422);
        assert.deepEqual(await (await fetch(base+"/history/sessions?scene=other")).json(),[]);
        const ended=await (await fetch(base+`/experiments/${experiment}/end`,{method:"POST"})).json();
        assert.equal(ended.ended,true);
        const next=await (await fetch(base+"/experiments",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({trainer_width:800,trainer_height:600,settings:{sessions:1,attempts:1,sensitivity:2.0}})})).json();
        assert.notEqual(next.experiment_id,experiment);
        const history=await (await fetch(base+"/history/experiments")).json();
        assert.equal(history.length,2);assert.equal(history.find(item=>item.experiment_id===next.experiment_id).saved_sessions,0);
        await stop();await start();
        const refreshed=await boot(facade,page.tabStorage);await refreshed.evaluate("ready");
        assert.equal(refreshed.evaluate("activeExperiment.experiment_id"),experiment);
        assert.equal(refreshed.evaluate("currentExperimentIndex"),1);
        const rows=await (await fetch(base+`/history/sessions?experiment_id=${experiment}`)).json();
        assert.equal(rows.length,2);
        const counts=JSON.parse(execFileSync(python,["-B","-c",
            "import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps([c.execute('select count(*) from sessions').fetchone()[0],c.execute('select count(*) from incomplete_sessions').fetchone()[0]])); c.close()",database],{encoding:"utf8"}));
        assert.deepEqual(counts,[1,1]);
        console.log(JSON.stringify({result:"PASS",frontend:"shipped ESM with simulated DOM/input",transport:"real HTTP",database:"temporary SQLite",counts,
            checks:["shot-to-commit","ACK-only progress","unique attempts","duplicate 200","conflict 409","pause/resume","incomplete isolation","query statistics","filter validation","end experiment","empty new experiment","process restart restore"]},null,2));
    }finally{
        await stop();
        // Only remove the exact temporary directory allocated by this run.
        const resolved=path.resolve(directory),temporaryRoot=path.resolve(os.tmpdir());
        if(path.dirname(resolved)!==temporaryRoot || !path.basename(resolved).startsWith("aimcoach-phase1-"))throw new Error("Unexpected cleanup path");
        fs.rmSync(resolved,{recursive:true,force:true});
    }
}
run().catch(error=>{console.error(error);process.exitCode=1;});
