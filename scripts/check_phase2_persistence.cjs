// Shipped scenario handlers use simulated DOM/time, real HTTP and a disposable on-disk database.
const {page}=require('../tests/scenario_harness.cjs');
const {spawn}=require('node:child_process');
const {setTimeout:delay}=require('node:timers/promises');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net'),assert=require('node:assert/strict');
async function run(){
    const directory=fs.mkdtempSync(path.join(os.tmpdir(),'aimcoach-phase2-'));
    const database=path.join(directory,'training.db');
    const reserve=net.createServer();await new Promise(resolve=>reserve.listen(0,'127.0.0.1',resolve));
    const port=reserve.address().port;await new Promise(resolve=>reserve.close(resolve));
    const base=`http://127.0.0.1:${port}`;
    let server;
    async function start(){
        server=spawn(process.env.AIMCOACH_TEST_PYTHON||'D:/Anaconda/envs/aimcoach/python.exe',['-B','-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',String(port)],
            {cwd:path.resolve(__dirname,'..'),env:{...process.env,AIMCOACH_DB_PATH:database,DASHSCOPE_API_KEY:'',PYTHONDONTWRITEBYTECODE:'1'},stdio:'ignore'});
        for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok)return;}catch{}if(server.exitCode!==null)throw Error('API failed to start');await delay(100);}throw Error('Startup timeout');
    }
    async function stop(){if(server?.exitCode===null){const exited=new Promise(resolve=>server.once('exit',resolve));server.kill();await exited;}}
    try{
        await start();
        const {targetAt}=await import('../frontend/scenario_motion.js');
        const saved=[];
        for(const key of ['four_target_switching','six_target_precision','horizontal_tracking','vertical_tracking','flick_target','reactive_tracking']){
            const p=await page((url,options)=>fetch(base+url,options));
            // Wait for the registry request before invoking the actual handlers.
            await delay(100);p.node('scenarioSelect').value=key;await p.emit('scenarioStart','click');
            assert.equal(p.core.canLeaveTrainingPage(),false,key);
            if(p.config.category==='static'){
                for(let i=0;i<5;i++){p.setTime((i+1)*400);await p.emit('trainer-area','mousedown',{button:0,clientX:parseFloat(p.node('target').style.left),clientY:parseFloat(p.node('target').style.top)});}
            }else{
                for(let t=50;t<=10000;t+=50){const [x,y]=targetAt(p.config,800,600,t);await p.emit('trainer-area','mousemove',{clientX:x,clientY:y});await p.advance(t);}
            }
            for(let i=0;i<100&&!p.node('scenarioResult').textContent;i++)await delay(30);
            assert.match(p.node('scenarioResult').textContent,/100 \/ 100/,p.node('scenarioStatus').textContent);
            const body=p.uploads[0];saved.push(body.sessionId);
            const invalid={...body,sessionId:require('node:crypto').randomUUID(),status:'incomplete',endedAt:null};
            const rejected=await fetch(base+'/scenarios/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(invalid)});
            assert.equal(rejected.status,422);
            const replay=await fetch(base+'/scenarios/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
            assert.equal(replay.status,200);
            const overwrite=await fetch(base+'/scenarios/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,extra:'changed'})});
            assert.equal(overwrite.status,409);
            const history=await (await fetch(base+`/history/sessions/${body.sessionId}`)).json();
            assert.equal(history.score.overall_score,100);assert.equal(history.status,'complete');
            console.log(`PASS ${key}: shipped handlers -> HTTP -> SQLite -> history; replay 200 / changed payload 409`);
        }
        const experiments=await (await fetch(base+'/history/experiments')).json();
        assert.equal(experiments.length,6);assert.ok(experiments.every(item=>item.saved_sessions===1));
        await stop();await start();
        const history=await (await fetch(base+'/scenarios/sessions')).json();assert.equal(history.length,6);
        for(const id of saved)assert.equal((await fetch(base+`/history/sessions/${id}`)).status,200);
        console.log('PASS restart: all six Sessions and scores persisted; experiment counts verified');
        console.log(`Disposable SQLite: ${database}`);
    }finally{await stop();}
}
run().catch(error=>{console.error(error);process.exitCode=1;});
