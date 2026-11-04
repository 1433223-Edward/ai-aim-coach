const {test}=require("node:test");
const assert=require("node:assert/strict");
const {Server,boot,flush,copy}=require("./frontend_harness.cjs");

test("refresh restores server identity, schedule and confirmed progress", async () => {
    const server = new Server();
    const first = await boot(server);
    const schedule = first.evaluate("JSON.stringify(experimentSchedule)");
    await first.finishSession();
    assert.equal(first.evaluate("currentExperimentIndex"), 1);
    const refreshed = await boot(server, first.tabStorage);
    assert.equal(refreshed.evaluate("activeExperiment.experiment_id"), "experiment-1");
    assert.equal(refreshed.evaluate("currentExperimentIndex"), 1);
    assert.equal(refreshed.evaluate("JSON.stringify(experimentSchedule)"), schedule);
    assert.equal(server.creations, 1);
});

test("only the primary button can record shots or advance attempts", async () => {
    const page = await boot(new Server());
    await page.click();
    page.evaluate("crosshairX = targetX; crosshairY = targetY;");
    for (const button of [1, 2, 3, 4]) await page.documentEvent("mousedown", {button});
    assert.equal(page.evaluate("currentAttempt.clicks.length"), 0);
    assert.equal(page.evaluate("currentSession.attempts.length"), 0);
    await page.documentEvent("mousedown", {button: 0});
    assert.equal(page.evaluate("currentSession.attempts.length"), 1);
    assert.equal(page.evaluate("currentSession.attempts[0].clicks[0].hit"), true);
});

test("navigation waits for pending Pointer Lock and aligns the original coordinate surface first", async () => {
    const page = await boot(new Server());
    let release;
    const lock = new Promise(resolve => { release = resolve; });
    let aligned = false;
    page.element("trainer-area").scrollIntoView = options => {
        assert.equal(options.block, "start"); aligned = true;
    };
    page.element("trainer-area").requestPointerLock = () => {
        assert.equal(aligned, true); return lock;
    };
    const waiting = page.click();
    await flush();
    assert.equal(page.evaluate("canLeaveTrainingPage()"), false);
    await page.documentEvent("pointerlockerror");
    assert.equal(page.evaluate("canLeaveTrainingPage()"), true);
    release(); await waiting;
    assert.equal(page.evaluate("canLeaveTrainingPage()"), true);
});

test("a late Pointer Lock event on a hidden Test page cannot start collection", async () => {
    const page = await boot(new Server());
    page.element("trainer-area").closest = () => ({});
    page.document.pointerLockElement = page.element("trainer-area");
    await page.documentEvent("pointerlockchange");
    assert.equal(page.document.pointerLockElement, null);
    assert.equal(page.evaluate("sessionActive"), false);
    assert.equal(page.evaluate("currentSession"), null);
    assert.equal(page.evaluate("currentExperimentIndex"), 0);
});

test("presentation subscribers cannot mutate protocol copies or interrupt a committed save", async () => {
    const page = await boot(new Server());
    page.evaluate('const viewCopy = getTrainingState(); viewCopy.experiment.protocol.target_count = 999;');
    assert.equal(page.evaluate("activeExperiment.protocol.target_count"), 3);
    page.evaluate('let updates = 0; subscribeTrainingState(() => { if (++updates > 1) throw new Error("view failed"); });');
    await page.finishSession();
    assert.equal(page.evaluate("currentExperimentIndex"), 1);
    assert.equal(page.evaluate("pendingQueue.list().sessions.length"), 0);
});

test("available browser environment is preserved without assuming unknown device settings", async () => {
    const server = new Server();
    const page = await boot(server);
    page.evaluate("window.devicePixelRatio = 2; window.visualViewport = {scale: 1.25};");
    await page.finishSession();
    const saved = [...server.payloads.values()][0];
    assert.equal(saved.clientEnvironment.devicePixelRatio, 2);
    assert.equal(saved.clientEnvironment.visualViewportScale, 1.25);
    assert.equal(saved.trainerWidth, 800);
    assert.equal(saved.trainerHeight, 600);
    const unknown = await boot(new Server());
    await unknown.click();
    assert.equal(unknown.evaluate("currentSession.clientEnvironment.devicePixelRatio"), null);
    assert.equal(unknown.evaluate("currentSession.clientEnvironment.visualViewportScale"), null);
});

test("two pages share identity and re-read progress before training", async () => {
    const server = new Server();
    const [first, second] = await Promise.all([boot(server), boot(server)]);
    await first.finishSession();
    await second.click();
    assert.equal(second.evaluate("currentSession.experimentOrder"), 2);
    assert.equal(second.evaluate("currentSession.experimentId"), "experiment-1");
    assert.equal(server.creations, 1);
});

test("pending and failed uploads never advance local progress", async () => {
    const server = new Server();
    const page = await boot(server);
    let release;
    server.saveGate = new Promise((resolve) => { release = resolve; });
    await page.finishSession();
    assert.equal(page.evaluate("sessionSaving"), true);
    assert.equal(page.evaluate("currentExperimentIndex"), 0);
    await page.click();
    assert.equal(page.evaluate("sessionActive"), false);
    server.failSave = true;
    release();
    await flush();
    assert.equal(page.evaluate("currentExperimentIndex"), 0);
    assert.equal(server.current.completed_count, 0);
    const refreshed = await boot(server);
    assert.equal(refreshed.evaluate("currentExperimentIndex"), 0);
});

test("completed experiment remains selected after refresh until explicit new start", async () => {
    const server = new Server();
    const state = server.create();
    for (let i = 0; i < 9; i++) server.commit(state);
    const page = await boot(server);
    assert.equal(page.evaluate("experimentFinished"), true);
    assert.equal(server.creations, 1);
    await page.click();
    assert.equal(server.creations, 2);
    assert.equal(page.evaluate("activeExperiment.experiment_id"), "experiment-2");
    assert.equal(page.evaluate("sessionActive"), false);
});

test("recommendation sends explicit identity and discards evidence changed during model calls", async () => {
    const server = new Server();
    const page = await boot(server);
    server.commit(server.current);
    server.onPlan = async (answer, body) => {
        assert.equal(body.experiment_id, "experiment-1");
        server.commit(server.current);
    };
    await page.evaluate("generateExperimentPlan()");
    assert.equal(page.element("agentResult").hidden, true);
    assert.match(page.element("agentStatus").textContent, /数据已更新/);
    assert.equal(page.evaluate("currentExperimentIndex"), 2);
    server.onPlan = null;
    await page.evaluate("generateExperimentPlan()");
    assert.equal(page.element("agentResult").hidden, false);
    assert.match(page.element("agentStatus").textContent, /snapshot-2/);
});

test("cross-tab update invalidates answers and focus reloads server progress", async () => {
    const server = new Server();
    const page = await boot(server);
    server.commit(server.current);
    await page.evaluate("generateExperimentPlan()");
    assert.equal(page.element("agentResult").hidden, false);
    server.commit(server.current);
    await page.event("storage", {key: "aimcoachEvidenceRevision"});
    await flush();
    assert.equal(page.element("agentResult").hidden, true);
    assert.equal(page.evaluate("currentExperimentIndex"), 2);
    assert.match(page.element("agentStatus").textContent, /旧建议已隐藏/);
    server.commit(server.current);
    await page.event("focus");
    await flush();
    assert.equal(page.evaluate("currentExperimentIndex"), 3);
});

test("viewport mismatch prevents collection and resize aborts an unfinished session", async () => {
    const server = new Server();
    const page = await boot(server);
    page.element("trainer-area").clientWidth = 900;
    await page.click();
    assert.equal(page.evaluate("sessionActive"), false);
    assert.match(page.element("start-message").textContent, /800/);
    page.element("trainer-area").clientWidth = 800;
    await page.click();
    assert.equal(page.evaluate("sessionActive"), true);
    page.element("trainer-area").clientWidth = 900;
    await page.event("resize");
    assert.equal(page.evaluate("sessionActive"), false);
    assert.equal(server.current.completed_count, 0);
});

test("an older response cannot restore a previously selected experiment", async () => {
    const server = new Server();
    const page = await boot(server);
    server.commit(server.current);
    server.onPlan = async () => {
        const old = server.current;
        while (old.status !== "complete") server.commit(old);
        const next = server.create();
        page.evaluate(`applyExperimentState(${JSON.stringify(next)})`);
    };
    await page.evaluate("generateExperimentPlan()");
    assert.equal(page.evaluate("activeExperiment.experiment_id"), "experiment-2");
    assert.equal(page.element("agentResult").hidden, true);
});

test("notification storage failure does not turn a committed save into an error", async () => {
    const server = new Server();
    const page = await boot(server);
    page.evaluate("localStorage.setItem = () => { throw new Error('storage unavailable'); };");
    await page.finishSession();
    assert.equal(page.evaluate("currentExperimentIndex"), 1);
    assert.equal(page.evaluate("experimentError"), "");
});

test("refresh preserves this tab's selected experiment when another tab starts a new one", async () => {
    const server = new Server();
    const page = await boot(server);
    while (server.current.status !== "complete") server.commit(server.current);
    server.create();
    const refreshed = await boot(server, page.tabStorage);
    assert.equal(refreshed.evaluate("activeExperiment.experiment_id"), "experiment-1");
    assert.equal(refreshed.evaluate("experimentFinished"), true);
    assert.equal(server.current.experiment_id, "experiment-2");
});

test("a missing stored selection is reported instead of silently replacing identity", async () => {
    const server = new Server();
    const storage = new Map([["aimcoachSelectedExperimentId", "missing-experiment"]]);
    const page = await boot(server, storage);
    assert.equal(page.evaluate("activeExperiment"), null);
    assert.match(page.element("start-message").textContent, /Missing experiment/);
    assert.equal(server.creations, 0);
});

test("failed uploads survive refresh and retry the same immutable Session", async () => {
    const server = new Server();
    server.failSave = true;
    const page = await boot(server);
    await page.finishSession();
    assert.equal(page.evaluate("hasUnconfirmedData()"), true);
    assert.match(page.element("saveStatus").textContent, /未全部确认/);
    const original = server.requests.find((r) => r.url === "/sessions").options.body;
    server.failSave = false;
    const refreshed = await boot(server, page.tabStorage);
    await flush();
    assert.equal(refreshed.evaluate("hasUnconfirmedData()"), false);
    assert.equal(refreshed.evaluate("currentExperimentIndex"), 1);
    assert.equal(server.current.completed_count, 1);
    assert.ok(server.requests.filter((r) => r.url === "/sessions").every((r) => r.options.body === original));
});

test("lost acknowledgement retries the same ID without duplicate storage", async () => {
    const server = new Server();
    const original = server.fetch.bind(server);
    let lost = false;
    server.fetch = async (url, options) => {
        const response = await original(url, options);
        if (url === "/sessions" && !lost) {
            lost = true;
            throw new TypeError("reply lost after commit");
        }
        return response;
    };
    const page = await boot(server);
    await page.finishSession();
    assert.equal(server.current.completed_count, 1);
    assert.equal(server.payloads.size, 1);
    assert.equal(page.evaluate("hasUnconfirmedData()"), false);
    assert.match(page.element("saveStatus").textContent, /保存成功/);
    const saves = server.requests.filter((r) => r.url === "/sessions");
    assert.equal(saves.length, 2);
    assert.equal(saves[0].options.body, saves[1].options.body);
});

test("duplicate retry clicks cannot start a second flush", async () => {
    const server = new Server();
    let release;
    server.saveGate = new Promise((resolve) => { release = resolve; });
    const page = await boot(server);
    await page.finishSession();
    await Promise.all([page.button("retrySaveButton"), page.button("retrySaveButton")]);
    assert.equal(server.requests.filter((r) => r.url === "/sessions").length, 1);
    release();
    await flush();
    assert.equal(server.current.completed_count, 1);
});

test("timeout releases saving state and keeps durable pending data", async () => {
    const server = new Server();
    server.saveGate = new Promise(() => {});
    const page = await boot(server);
    page.evaluate("requestTimeoutMs = 5;");
    await page.finishSession();
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(page.evaluate("sessionSaving"), false);
    assert.equal(page.evaluate("hasUnconfirmedData()"), true);
    assert.match(page.element("saveStatus").textContent, /超时/);
    assert.equal(server.current.completed_count, 0);
});

test("wrong acknowledgement is not treated as a successful save", async () => {
    const server = new Server();
    const original = server.fetch.bind(server);
    server.fetch = async (url, options) => {
        const response = await original(url, options);
        if (url === "/sessions") return {ok: true, status: 200, json: async () => ({saved: true, session_id: "wrong"})};
        return response;
    };
    const page = await boot(server);
    await page.finishSession();
    assert.equal(page.evaluate("hasUnconfirmedData()"), true);
    assert.equal(page.evaluate("currentExperimentIndex"), 0);
    assert.match(page.element("saveStatus").textContent, /不一致/);
});

test("corrupt pending state blocks collection and remains manually exportable", async () => {
    const server = new Server();
    server.storage.set("aimcoachPending:broken", "{bad");
    const page = await boot(server);
    assert.match(page.element("saveStatus").textContent, /损坏/);
    await page.click();
    assert.equal(page.evaluate("sessionActive"), false);
    await page.button("exportPendingButton");
    assert.equal(page.downloads, 1);
    assert.equal(server.storage.get("aimcoachPending:broken"), "{bad");
    await page.evaluate("generateExperimentPlan()");
    assert.match(page.element("agentStatus").textContent, /待上传/);
});

test("cancelled model waiting never displays a late answer", async () => {
    const server = new Server();
    server.onPlan = () => new Promise(() => {});
    const page = await boot(server);
    server.commit(server.current);
    const pending = page.evaluate("generateExperimentPlan()");
    await flush();
    await page.button("cancelPlanButton");
    await pending;
    assert.equal(page.element("agentResult").hidden, true);
    assert.equal(page.evaluate("planBusy"), false);
    assert.match(page.element("agentStatus").textContent, /取消/);
});

test("pointer lock fallback and error paths do not leave an active Session", async () => {
    const server = new Server();
    const page = await boot(server);
    page.element("trainer-area").requestPointerLock = async () => { throw new Error("blocked"); };
    await page.click();
    assert.equal(page.evaluate("sessionActive"), false);
    assert.match(page.element("start-message").textContent, /无法锁定/);
    await page.documentEvent("pointerlockerror");
    assert.match(page.element("start-message").textContent, /锁定失败/);
});

test("illegal restored completion state is rejected instead of trained", async () => {
    const server = new Server();
    server.create().status = "complete"; // Deliberately contradict completed_count=0.
    const page = await boot(server);
    assert.equal(page.evaluate("activeExperiment"), null);
    assert.match(page.element("start-message").textContent, /状态无效/);
});

test("quota failures keep raw data in memory and warn before closing", async () => {
    const server = new Server();
    server.failSave = true;
    const page = await boot(server);
    page.evaluate("localStorage.setItem = () => { throw new Error('quota'); };");
    await page.finishSession();
    assert.equal(page.evaluate("pendingQueue.memoryOnly"), true);
    assert.equal(page.evaluate("pendingQueue.list().sessions.length"), 1);
    assert.match(page.element("saveStatus").textContent, /仅在当前页面内存/);
    let warned = false;
    await page.event("beforeunload", {preventDefault() { warned = true; }});
    assert.equal(warned, true);
    assert.match(page.element("start-message").textContent, /未确认保存/);
});
