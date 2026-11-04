const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const {loadModule} = require("./esm_loader.cjs");
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness() {
    const nodes = new Map();
    const listeners = new Map();
    const document = {activeElement: null, pointerLockElement: null,
        getElementById: id => element(id), createElement: tag => element(Symbol(tag)),
        addEventListener: (name, callback) => listen(`document:${name}`, callback),
        exitPointerLock() {}, querySelectorAll: () => [element("homeLink"), element("testLink")],
    };
    const listen = (name, callback) => listeners.set(name, [...listeners.get(name) || [], callback]);
    const remove = (name, callback) => listeners.set(name, (listeners.get(name) || []).filter(item => item !== callback));
    function element(id) {
        if (!nodes.has(id)) nodes.set(id, {id, textContent: "", value: "goal", hidden: false,
            style: {}, dataset: {}, children: [], attributes: {}, open: false, isConnected: true,
            clientWidth: 800, clientHeight: 600,
            setAttribute(name, value) { this.attributes[name] = value; },
            removeAttribute(name) { delete this.attributes[name]; },
            addEventListener: (name, callback) => listen(`${String(id)}:${name}`, callback),
            removeEventListener: (name, callback) => remove(`${String(id)}:${name}`, callback),
            append(...values) { this.children.push(...values); },
            replaceChildren(...values) { this.children = values; },
            focus() { document.activeElement = this; },
            showModal() { this.open = true; }, close() { this.open = false; },
            getBoundingClientRect: () => ({left: 220, right: 800}),
        });
        return nodes.get(id);
    }
    element("homeLink").dataset.page = "home";
    element("testLink").dataset.page = "test";
    const window = {location: {hash: ""}, history: {replaceState(_state, _title, hash) { window.location.hash = hash; }},
        addEventListener: (name, callback) => listen(`window:${name}`, callback),
        removeEventListener: (name, callback) => remove(`window:${name}`, callback),
    };
    const storage = () => { const data = new Map(); return {
        getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value),
        removeItem: key => data.delete(key), key: index => [...data.keys()][index], get length() { return data.size; },
    }; };
    const requests = [];
    let experiment = null;
    const fetch = async (url, options={}) => {
        requests.push(url);
        if (url === "/experiments" && options.method === "POST") experiment = {
            experiment_id: "shell-experiment", protocol: {version: "1", target_count: 3, trainer_width: 800, trainer_height: 600},
            schedule: [{sensitivity: 1, repeat: 1}], completed_count: 0, completed_session_ids: [],
            next_condition: {sensitivity: 1, repeat: 1}, status: "incomplete",
        };
        return {status: experiment ? 200 : 404, ok: !!experiment,
            json: async () => experiment || {detail: "Missing"}};
    };
    const context = vm.createContext({document, window, fetch, localStorage: storage(), sessionStorage: storage(),
        console, setTimeout, clearTimeout, AbortController, Blob, URL, performance: {now: () => 100}, crypto: {randomUUID: () => "shell-session"}});
    const emit = async (name, event={}) => { for (const callback of listeners.get(name) || []) await callback(event); await flush(); };
    return {context, window, document, element, requests, listeners, emit};
}

function fakeCore(initial={}) {
    let state = {sessionActive:false, locked:false, pointerLockPending:false, planBusy:false,
        experiment:null, recommendationEvidence:null, ...initial};
    const listeners = new Set();
    return {getTrainingState: () => structuredClone(state), canLeaveTrainingPage: () => !state.locked && !state.sessionActive && !state.pointerLockPending,
        subscribeTrainingState(listener) { listeners.add(listener); listener(structuredClone(state)); return () => listeners.delete(listener); },
        update(change) { state = {...state,...change}; for (const listener of listeners) listener(structuredClone(state)); },
    };
}

const experiment = {experiment_id:"evidence-experiment", completed_count:1, completed_session_ids:["session-1"],
    schedule:[{sensitivity:1,repeat:1}], protocol:{target_count:3}, next_condition:null, status:"complete"};
const evidence = {experiment_id:experiment.experiment_id,session_ids:["session-1"],snapshot_id:"snapshot-a",session_count:1,attempt_count:3,incomplete:false};
const analysis = {sensitivity_profiles:[{sensitivity:1,attempt_count:3,metrics:{
    final_hit_time_ms:{median:123.456},miss_count:{median:0},first_click_error_px:{median:2.5},path_efficiency:{median:.75}}}],
    limitations:[{code:"descriptive",message:"Descriptive only."}]};

test("actual app initializes no trainer on Home and reuses one controller across hash navigation", async () => {
    const h = harness();
    const app = await loadModule("app.js", h.context);
    await app.evaluate(); await flush();
    assert.equal(h.requests.length,0);
    assert.equal(h.element("homePage").hidden,false);
    h.window.location.hash = "#/test";
    await h.emit("window:hashchange"); await flush(); await flush();
    assert.equal(h.requests.filter(url => url === "/experiments").length,1);
    const shots = h.listeners.get("document:mousedown").length;
    const confirmed = h.element("sessionProgress").textContent;
    h.window.location.hash = "#/home"; await h.emit("window:hashchange");
    assert.equal(h.element("testPage").hidden,true);
    h.window.location.hash = "#/test"; await h.emit("window:hashchange");
    assert.equal(h.listeners.get("document:mousedown").length,shots);
    assert.equal(h.requests.filter(url => url === "/experiments").length,1);
    assert.equal(h.element("sessionProgress").textContent,confirmed);
    assert.equal(h.element("testLink").attributes["aria-current"],"page");
    // Returning Home preserves the durable raw queue instead of reconstructing the controller.
    const pending = JSON.stringify({version:1,session:{sessionId:"unsaved",experimentId:"shell-experiment",attempts:[]}});
    vm.runInContext(`localStorage.setItem("aimcoachPending:unsaved", ${JSON.stringify(pending)});`,h.context);
    h.window.location.hash="#/home";await h.emit("window:hashchange");
    h.window.location.hash="#/test";await h.emit("window:hashchange");
    assert.equal(vm.runInContext('localStorage.getItem("aimcoachPending:unsaved")',h.context),pending);
    assert.equal(h.listeners.get("document:mousedown").length,shots);
    let prevented=false;await h.emit("skipContent:click",{preventDefault(){prevented=true;}});
    assert.equal(prevented,true);assert.equal(h.document.activeElement.id,"pageContent");
    assert.equal(h.window.location.hash,"#/test");
});

test("direct Test hash initializes once and blocked navigation restores URL without hiding training", async () => {
    const h = harness();
    const module = await loadModule("navigation.js",h.context); await module.evaluate();
    h.window.location.hash="#/test";
    const pages=[]; let safe=false; let blocked=0;
    const nav=module.namespace.createNavigation({window:h.window,showPage:async page=>pages.push(page),canNavigate:()=>safe,onBlocked:()=>blocked++});
    await nav.start();
    h.window.location.hash="#/home"; await h.emit("window:hashchange");
    assert.equal(h.window.location.hash,"#/test"); assert.deepEqual(pages,["test"]); assert.equal(blocked,1);
    safe=true; h.window.location.hash="#/home"; await h.emit("window:hashchange");
    assert.deepEqual(pages,["test","home"]);
    nav.destroy();h.window.location.hash="#/test";await h.emit("window:hashchange");
    assert.deepEqual(pages,["test","home"]);
});

test("navigation cannot hide Test while its first restoration is still in flight", async () => {
    const h=harness(); const module=await loadModule("navigation.js",h.context); await module.evaluate();
    let resolve; const gate=new Promise(done=>resolve=done); const pages=[];
    h.window.location.hash="#/test";
    const nav=module.namespace.createNavigation({window:h.window,showPage:async page=>{pages.push(page);await gate;},canNavigate:()=>true,onBlocked:()=>{}});
    const boot=nav.start(); h.window.location.hash="#/home"; await h.emit("window:hashchange");
    assert.equal(h.window.location.hash,"#/test"); assert.deepEqual(pages,["test"]);
    resolve();await boot;
});

test("Drawer empty state never invokes a model or analysis request and restores focus on Escape", async () => {
    const h=harness(); const module=await loadModule("recommendation_ui.js",h.context);await module.evaluate();
    const calls=[]; const core=fakeCore();
    const drawer=module.namespace.bindRecommendationUI(core,h.document,async(...args)=>calls.push(args));
    h.element("openInsights").focus(); drawer.open();
    assert.equal(h.element("insightsDrawer").open,true);
    assert.equal(h.document.activeElement.id,"closeInsights"); assert.equal(calls.length,0);
    assert.match(h.element("evidenceStatus").textContent,/还没有/);
    assert.equal(h.element("agentState").textContent,"INSUFFICIENT EVIDENCE");
    let prevented=false;await h.emit("insightsDrawer:cancel",{preventDefault(){prevented=true;}});
    assert.equal(prevented,true);assert.equal(h.element("insightsDrawer").open,false);
    assert.equal(h.document.activeElement.id,"openInsights");
    drawer.destroy();await h.emit("openInsights:click");
    assert.equal(h.element("insightsDrawer").open,false);
});

test("Drawer renders real metric keys, preserves raw limitations, and labels a different recommendation snapshot", async () => {
    const h=harness();const module=await loadModule("recommendation_ui.js",h.context);await module.evaluate();
    const core=fakeCore({experiment}); const calls=[];
    const drawer=module.namespace.bindRecommendationUI(core,h.document,async url=>{calls.push(url);return {evidence,analysis};});
    drawer.open();await flush();
    assert.deepEqual(calls,["/experiments/evidence-experiment/analysis"]);
    const rows=h.element("metricProfiles").children[0].children[1].children;
    assert.deepEqual(rows.map(row=>row.children[1].textContent),["123.46 ms","0.00","2.50 px","0.75"]);
    assert.equal(h.element("analysisLimitations").children[0].textContent,"Descriptive only.");
    core.update({recommendationEvidence:{...evidence,snapshot_id:"snapshot-other"}});
    assert.match(h.element("evidenceRelationship").textContent,/不同/);
    assert.equal(calls.length,1); // Rendering a recommendation does not repeat a paid request.
    assert.equal(module.namespace.formatMetric(null),"—");
});

test("Drawer rejects mismatched evidence and hides results that finish after closing", async () => {
    const h=harness();const module=await loadModule("recommendation_ui.js",h.context);await module.evaluate();
    const core=fakeCore({experiment});
    const drawer=module.namespace.bindRecommendationUI(core,h.document,async()=>({evidence:{...evidence,experiment_id:"wrong"},analysis}));
    drawer.open();await flush();assert.match(h.element("evidenceStatus").textContent,/分析不可用/);
    assert.equal(h.element("metricProfiles").children.length,0);drawer.close();
    let resolve;const waiting=new Promise(done=>resolve=done);
    const other=module.namespace.bindRecommendationUI(core,h.document,()=>waiting);
    other.open();other.close();resolve({evidence,analysis});await flush();
    assert.equal(h.element("metricProfiles").children.length,0);
});

test("Pointer Lock blocks Drawer entry; loading, failure and success do not change training geometry", async () => {
    const h=harness();const module=await loadModule("recommendation_ui.js",h.context);await module.evaluate();
    const core=fakeCore({locked:true,experiment});
    const drawer=module.namespace.bindRecommendationUI(core,h.document,async()=>({evidence,analysis}));
    drawer.open();assert.equal(h.element("insightsDrawer").open,false);
    core.update({locked:false});drawer.open();await flush();
    core.update({planBusy:true});assert.equal(h.element("agentState").textContent,"GENERATING");
    core.update({planBusy:false,planFailureCode:"model_unavailable"});assert.equal(h.element("agentState").textContent,"REQUEST FAILED");
    core.update({planFailureCode:null,recommendationEvidence:evidence});assert.equal(h.element("agentState").textContent,"RECOMMENDATION READY");
    assert.equal(h.element("trainer-area").clientWidth,800);assert.equal(h.element("trainer-area").clientHeight,600);
    core.update({locked:true});assert.equal(h.element("insightsDrawer").open,false);
});

test("CSS preserves coordinate geometry and reduced motion; HTML loads only one module entry", () => {
    const css=fs.readFileSync(path.join(__dirname,"../frontend/style.css"),"utf8");
    const html=fs.readFileSync(path.join(__dirname,"../frontend/index.html"),"utf8");
    assert.match(css,/#trainer-area\s*\{[^}]*width: 100vw; height: 100vh;/);
    assert.match(css,/@media \(prefers-reduced-motion: reduce\)/);
    assert.equal((html.match(/<script /g)||[]).length,1);assert.match(html,/<script type="module" src="\.\/app.js/);
});
