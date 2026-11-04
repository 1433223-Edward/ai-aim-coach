// Run the actual trainer controller with a fake DOM and server, without browser dependencies.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../frontend/trainer.js"), "utf8");
const {loadModule} = require("./esm_loader.cjs");
const copy = (value) => JSON.parse(JSON.stringify(value));
const flush = () => new Promise((resolve) => setImmediate(resolve));

class Server {
    constructor() {
        this.experiments = new Map();
        this.current = null;
        this.creations = 0;
        this.failSave = false;
        this.saveGate = null;
        this.onPlan = null;
        this.requests = [];
        this.storage = new Map();
        this.payloads = new Map();
    }

    create() {
        // Match the server contract: resuming never reshuffles an active experiment.
        if (this.current && this.current.status !== "complete") return this.current;
        const schedule = [];
        for (let repeat = 1; repeat <= 3; repeat++) {
            for (const sensitivity of [0.8, 1, 1.2]) schedule.push({sensitivity, repeat});
        }
        this.current = {
            experiment_id: `experiment-${++this.creations}`,
            protocol: {version: "1", target_count: 3, trainer_width: 800, trainer_height: 600},
            schedule, completed_session_ids: [], completed_count: 0,
            status: "incomplete", next_condition: schedule[0],
        };
        this.experiments.set(this.current.experiment_id, this.current);
        return this.current;
    }

    commit(state, id = `saved-${state.completed_count + 1}`) {
        state.completed_session_ids.push(id);
        state.completed_count++;
        state.status = state.completed_count === 9 ? "complete" : "incomplete";
        state.next_condition = state.schedule[state.completed_count] || null;
    }

    async fetch(url, options = {}) {
        this.requests.push({url, options});
        const method = options.method || "GET";
        const body = options.body ? JSON.parse(options.body) : null;
        const respond = (status, payload) => ({ok: status < 400, status, json: async () => copy(payload)});
        if (url === "/experiments/current") {
            return this.current ? respond(200, this.current) : respond(404, {detail: "No experiment"});
        }
        if (url === "/experiments" && method === "POST") return respond(200, this.create());
        if (url.startsWith("/experiments/")) {
            const state = this.experiments.get(decodeURIComponent(url.slice("/experiments/".length)));
            return state ? respond(200, state) : respond(404, {detail: "Missing experiment"});
        }
        if (url === "/sessions") {
            if (this.saveGate) await this.saveGate;
            if (this.failSave) return respond(500, {detail: "Save failed"});
            if (this.payloads.has(body.sessionId)) {
                return JSON.stringify(this.payloads.get(body.sessionId)) === JSON.stringify(body)
                    ? respond(200, {saved: true, session_id: body.sessionId})
                    : respond(409, {detail: "Conflicting ID"});
            }
            const state = this.experiments.get(body.experimentId);
            if (body.experimentOrder !== state.completed_count + 1) return respond(409, {detail: "Stale condition"});
            this.commit(state, body.sessionId);
            this.payloads.set(body.sessionId, copy(body));
            return respond(200, {saved: true, session_id: body.sessionId});
        }
        if (url === "/agent/plan") {
            const state = this.experiments.get(body.experiment_id);
            const answer = {
                status: "ok", answer: "test recommendation", tools_used: ["get_analysis_summary"],
                evidence: {experiment_id: state.experiment_id, snapshot_id: `snapshot-${state.completed_count}`,
                    session_ids: copy(state.completed_session_ids), session_count: state.completed_count,
                    incomplete: state.status !== "complete"},
            };
            if (this.onPlan) await this.onPlan(answer, body);
            return respond(200, answer);
        }
        throw new Error(`Unexpected request: ${method} ${url}`);
    }
}

async function boot(server, tabStorage = new Map()) {
    const handlers = new Map();
    const windowHandlers = new Map();
    const elements = new Map();
    let uuid = 0;
    let downloads = 0;
    const listen = (registry, key, fn) => registry.set(key, [...(registry.get(key) || []), fn]);
    const emit = async (registry, key, event = {}) => {
        for (const callback of registry.get(key) || []) await callback(event);
    };
    function element(id) {
        if (!elements.has(id)) elements.set(id, {
            style: {}, dataset: {}, hidden: true, textContent: "", value: "test goal",
            clientWidth: 800, clientHeight: 600,
            addEventListener: (type, fn) => listen(handlers, `${id}:${type}`, fn),
            click() { return emit(handlers, `${id}:click`); },
        });
        return elements.get(id);
    }
    const document = {
        pointerLockElement: null, getElementById: element, createElement: () => ({click() { downloads++; }}),
        addEventListener: (type, fn) => listen(handlers, type, fn),
        exitPointerLock() {
            document.pointerLockElement = null;
            void emit(handlers, "pointerlockchange");
        },
    };
    element("trainer-area").requestPointerLock = async () => {
        document.pointerLockElement = element("trainer-area");
        await emit(handlers, "pointerlockchange");
    };
    const context = vm.createContext({
        document, window: {addEventListener: (type, fn) => listen(windowHandlers, type, fn)},
        fetch: server.fetch.bind(server), console: {log() {}, error() {}, warn() {}},
        crypto: {randomUUID: () => `browser-${++uuid}-${server.requests.length}`},
        localStorage: {getItem: (key) => server.storage.get(key) || null,
                       setItem: (key, value) => server.storage.set(key, value),
                       removeItem: (key) => server.storage.delete(key),
                       key: (index) => [...server.storage.keys()][index],
                       get length() { return server.storage.size; }},
        performance: {now: () => server.now ?? 100}, Blob, AbortController,
        setTimeout: (fn,ms) => {
            if(server.manualTimers && ms<15000){const timer={manual:true,fn,ms};server.manualTimers.push(timer);return timer;}
            return setTimeout(fn,ms);
        },
        clearTimeout: timer => {if(timer?.manual)timer.fn=null;else clearTimeout(timer);},
        sessionStorage: {getItem: (key) => tabStorage.get(key) || null,
                         setItem: (key, value) => tabStorage.set(key, value)},
        URL: {createObjectURL: () => "blob:test", revokeObjectURL() {}},
    });
    const trainerModule = await loadModule("trainer.js", context, source + "\nexport const inspect = (code) => eval(code);");
    await trainerModule.evaluate();
    await flush();
    return {
        element, document, tabStorage,
        evaluate: (code) => trainerModule.namespace.inspect(code),
        click: () => emit(handlers, "trainer-area:click"),
        button: (id) => emit(handlers, `${id}:click`),
        documentEvent: (type, event) => emit(handlers, type, event),
        get downloads() { return downloads; },
        event: (type, value) => emit(windowHandlers, type, value),
        async finishSession() {
            await this.click();
            assert.equal(this.evaluate("sessionActive"), true);
            for (let index = 0; index < 3; index++) {
                this.evaluate("crosshairX = targetX; crosshairY = targetY;");
                await emit(handlers, "mousedown", {button: 0});
            }
            await flush();
        },
    };
}


module.exports={Server,boot,flush,copy};
