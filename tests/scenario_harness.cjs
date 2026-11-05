const vm=require("node:vm");
const {loadModule}=require("./esm_loader.cjs");
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const ids=["four_target_switching","six_target_precision","horizontal_tracking","vertical_tracking","flick_target","reactive_tracking"];
async function page(remoteFetch=null,storage=new Map()){
    const nodes=new Map(),callbacks=new Map(),frames=new Map();let now=0,id=0,running=false,pending=false,experiment=null;
    const uploads=[];const requests=[];let failSave=false;
    function node(key){if(!nodes.has(key))nodes.set(key,{value:"",style:{},textContent:"",children:[],
        addEventListener(type,fn){callbacks.set(`${key}:${type}`,[...callbacks.get(`${key}:${type}`)||[],fn]);},
        append(value){this.children.push(value);},remove(){},click(){},getBoundingClientRect:()=>({left:0,top:0}),clientWidth:800,clientHeight:600,scrollIntoView(){}});return nodes.get(key);}
    const document={getElementById:node,createElement:()=>node(Symbol("element")),addEventListener:(type,fn)=>node("document").addEventListener(type,fn)};
    const core={getTrainingState:()=>({experiment,sessionSaving:false,pending}),canLeaveTrainingPage:()=>!running,
        selectScenarioExperiment(value){experiment=value;},
        setScenarioMode(mode,active=false,queued=false){running=active;pending=queued;},refreshTrainingState:async()=>experiment};
    const response=value=>({ok:true,status:200,json:async()=>value});
    const fetch=async(url,options={})=>{
        requests.push(url);
        if(url==="/scenarios")return remoteFetch?remoteFetch(url,options):response(ids.map(scenario_id=>({scenario_id,name:scenario_id})));
        const body=JSON.parse(options.body);
        if(url==="/scenarios/experiments"){
            if(remoteFetch){const reply=await remoteFetch(url,options);experiment=await reply.clone().json();return reply;}
            const category=body.scenario.scenario_id.includes("tracking")?"tracking":"static";
            experiment={experiment_id:"ui-experiment",completed_count:0,status:"incomplete",next_condition:{repeat:1},
                protocol:{settings:{},scenario:{...body.scenario,category,radius:category==="static"&&body.scenario.scenario_id==="six_target_precision"?15.6:24,scoring_version:"aim-score-1"}}};
            return response(experiment);
        }
        if(url==="/scenarios/sessions"){
            uploads.push(body);
            if(failSave)throw new Error("offline");
            if(remoteFetch)return remoteFetch(url,options);
            return response({saved:true,session_id:body.sessionId,result:{overall_score:42,eligible:body.status==="complete",scenario_id:body.scenarioSnapshot.scenario_id,
                difficulty:"normal",scoring_version:"aim-score-1",strength:"coverage",weakness:"stability",metrics:{},previous_same_condition:{count:0,best_score:null}}});
        }
        throw new Error(`Unexpected URL ${url}`);
    };
    for(const [key,value] of Object.entries({scenarioSelect:"legacy",scenarioDifficulty:"normal",scenarioSeed:"1",scenarioDuration:"10",scenarioCount:"5",scenarioSpeed:"120",scenarioDirection:"positive"}))node(key).value=value;
    const context=vm.createContext({document,window:{addEventListener:(type,fn)=>node("window").addEventListener(type,fn)},fetch,
        localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
        performance:{now:()=>now},crypto:{randomUUID:()=>require("node:crypto").randomUUID()},setTimeout,clearTimeout,AbortController,Blob,URL,console,
        requestAnimationFrame:fn=>{frames.set(++id,fn);return id;},cancelAnimationFrame:key=>frames.delete(key)});
    const module=await loadModule("scenario_ui.js",context);await module.evaluate();module.namespace.bindScenarioLibrary(core,document);await flush();
    async function emit(key,type,event={}){for(const fn of callbacks.get(`${key}:${type}`)||[])await fn(event);await flush();}
    return {node,uploads,requests,storage,core,emit,setSaveFailure:value=>{failSave=value;},advance:async time=>{now=time;const batch=[...frames.values()];frames.clear();for(const fn of batch)fn();await flush();},
        get config(){return experiment.protocol.scenario;},setTime:time=>{now=time;}};
}

module.exports={page};
