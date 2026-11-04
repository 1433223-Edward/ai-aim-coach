import {readSettings, DEFAULT_SETTINGS, applyVisualSettings, shotFeedback} from "./settings.js";
import {PendingSessionQueue} from "./pending_sessions.js";
import {requestJson} from "./api.js";

// ============================================================
// AI Aim Coach
// Build 0 - Virtual Crosshair Input
//
// 当前文件只负责：
// Physical Mouse
//      ↓
// Pointer Lock
//      ↓
// movementX / movementY
//      ↓
// Trainer Sensitivity
//      ↓
// Virtual Crosshair
//
// Collection, server-backed experiment progress and scoped recommendations.
// ============================================================


// ------------------------------------------------------------
// 1. 获取页面元素
// ------------------------------------------------------------

const trainerArea = document.getElementById("trainer-area");
const crosshair = document.getElementById("crosshair");
const target = document.getElementById("target");
const startMessage = document.getElementById("start-message");

// The server owns identity, protocol, schedule and confirmed progress.
let activeExperiment = null;
let experimentSchedule = [];
let currentExperimentIndex = 0;
let experimentFinished = false;
let experimentLoading = false;
let sessionSaving = false;
let planBusy = false;
let experimentError = "";
let trainerSensitivity = 1.0;
let currentInputMode = "unadjusted_requested";

// A revision prevents an older asynchronous answer from replacing newer state.
let planRequestVersion = 0;
let recommendationEvidence = null;
let pointerLockPending = false;
let planFailureCode = null;
let paused = false;
let countdown = false;
let activeStarted = 0;
let activeElapsed = 0;
let lifecycleTimer = null;
let frozenSettings = DEFAULT_SETTINGS;
let pauseRequested = false;

function lifecycleMessage(text) { document.getElementById("lifecycleStatus").textContent=text; }
function activeMilliseconds() { return activeElapsed + (sessionActive && !paused ? performance.now()-activeStarted : 0); }
function stopTimer() { clearTimeout(lifecycleTimer);lifecycleTimer=null; }
function armLimit() {
    stopTimer();
    if(frozenSettings.time_limit_seconds>0) lifecycleTimer=setTimeout(()=>{void finishIncomplete("time_limit");},Math.max(0,frozenSettings.time_limit_seconds*1000-activeMilliseconds()));
}
async function finishIncomplete(reason) {
    if(!sessionActive)return;
    currentSession.activeDurationMs=activeMilliseconds();
    currentSession.status="incomplete";currentSession.interruptionReason=reason;
    currentSession.interruptedAttempt=currentAttempt;
    paused=false;pauseRequested=false;stopTimer();
    lifecycleMessage("未完成记录进入可靠保存队列；不会推进实验条件，也不进入分析样本。");
    await completeSession();
    lifecycleMessage(pendingQueue.list().sessions.some(item=>item.sessionId===currentSession.sessionId)
        ? "未完成记录保存或本地清理尚未确认，请重试/导出；实验进度未推进。"
        : "未完成记录已由服务端确认归档；实验进度未推进，可重做当前条件。");
}
export function pauseTraining() {
    if(!sessionActive||paused)return;
    activeElapsed=activeMilliseconds();paused=true;pauseRequested=true;stopTimer();
    // Keep completed attempts intact. A partly observed attempt is not a completed observation.
    currentSession.pausedAttempts ||= [];if(currentAttempt)currentSession.pausedAttempts.push(currentAttempt);
    currentAttempt=null;target.style.display="none";document.exitPointerLock();
    startMessage.style.display="block";startMessage.textContent="已暂停 · 按 P 或点击训练区继续";
    lifecycleMessage("已暂停。点击训练区或“暂停 / 继续”重新锁定后继续；当前 Attempt 重做。");publishState();
}

const stateListeners = new Set();

// The shell receives copies and cannot mutate the controller's protocol or progress.
export function getTrainingState() {
    return {paused, countdown, sessionActive, sessionSaving, planBusy, experimentLoading, pointerLockPending,
        locked: document.pointerLockElement === trainerArea,
        experiment: activeExperiment ? JSON.parse(JSON.stringify(activeExperiment)) : null,
        recommendationEvidence: recommendationEvidence ? JSON.parse(JSON.stringify(recommendationEvidence)) : null,
        pending: hasUnconfirmedData(), error: experimentError, planFailureCode};
}

export function subscribeTrainingState(listener) {
    stateListeners.add(listener);
    listener(getTrainingState());
    return () => stateListeners.delete(listener);
}

function publishState() {
    // Presentation failures must never interrupt saving or raw event collection.
    for (const listener of stateListeners) {
        try { listener(getTrainingState()); }
        catch (error) { console.error("Training UI update failed", error); }
    }
}

export function canLeaveTrainingPage() {
    // Require the existing Escape/abort path, including the asynchronous lock request gap.
    return !sessionActive && !pointerLockPending && document.pointerLockElement !== trainerArea;
}

function invalidateRecommendation() {
    planRequestVersion++;
    if (recommendationEvidence) {
        agentStatus.textContent = "实验数据已变化，旧建议已隐藏；请重新生成。";
    }
    recommendationEvidence = null;
    agentResult.hidden = true;
}

function applyExperimentState(state) {
    // Reject malformed or self-contradictory restored state instead of starting the wrong condition.
    if (!state || typeof state.experiment_id !== "string" || !state.experiment_id ||
        !Array.isArray(state.schedule) || state.schedule.length === 0 ||
        !Number.isInteger(state.completed_count) || state.completed_count < 0 ||
        state.completed_count > state.schedule.length ||
        !Array.isArray(state.completed_session_ids) || state.completed_session_ids.length !== state.completed_count ||
        new Set(state.completed_session_ids).size !== state.completed_count ||
        !["complete", "incomplete"].includes(state.status) ||
        (state.status === "complete") !== (state.completed_count === state.schedule.length) ||
        JSON.stringify(state.next_condition) !== JSON.stringify(state.ended ? null : state.schedule[state.completed_count] || null) ||
        state.protocol?.version !== "1" ||
        ![state.protocol.target_count, state.protocol.trainer_width, state.protocol.trainer_height]
            .every((value) => Number.isInteger(value) && value > 0) ||
        state.schedule.some((condition) => !Number.isFinite(condition.sensitivity) || condition.sensitivity <= 0 ||
            !Number.isInteger(condition.repeat) || condition.repeat <= 0)) {
        throw new Error("实验恢复状态无效；不会覆盖已有选择或开始训练。");
    }
    const identityChanged = activeExperiment?.experiment_id !== state.experiment_id;
    const previousIds = activeExperiment?.completed_session_ids || [];
    // Invalidate displayed evidence whenever its source identity or session set changes.
    if (activeExperiment?.experiment_id !== state.experiment_id ||
        JSON.stringify(previousIds) !== JSON.stringify(state.completed_session_ids)) {
        invalidateRecommendation();
    }
    activeExperiment = state;
    if(identityChanged)lifecycleMessage("冻结配置已载入；P 暂停/继续，E 提前结束 Session。");
    applyVisualSettings({...DEFAULT_SETTINGS,...(state.protocol.settings||{})},crosshair,target,trainerArea);
    try {
        // Keep only this tab's selection across reload; progress and evidence stay on the server.
        sessionStorage.setItem("aimcoachSelectedExperimentId", state.experiment_id);
    } catch (error) {
        console.warn("Tab selection storage unavailable.", error);
    }
    experimentSchedule = state.schedule;
    currentExperimentIndex = state.completed_count;
    experimentFinished = state.status === "complete" || state.ended === true;
    experimentError = "";
    updateExperimentMessage();
}

// These deadlines bound UI waiting; the server has an independent Agent budget.
let requestTimeoutMs = 15000;
let planTimeoutMs = 70000;
let planAbortController = null;
let queueStorage = null;
try { queueStorage = localStorage; } catch (error) { /* Browser storage may be disabled. */ }
const pendingQueue = new PendingSessionQueue(queueStorage);

function hasUnconfirmedData() {
    const pending = pendingQueue.list();
    return pending.sessions.length > 0 || pending.errors.length > 0;
}

// Keep controller-specific deadlines at the caller; transport owns no training state.
const fetchJson = (url, options = {}, timeoutMs = requestTimeoutMs) => requestJson(url, options, timeoutMs);

async function refreshExperiment({initial = false, startNew = false} = {}) {
    experimentLoading = true;
    updateExperimentMessage();
    try {
        let state;
        if (startNew) {
            state = await createExperiment();
        } else {
            let selectedId = activeExperiment?.experiment_id;
            if (initial && !selectedId) {
                try {
                    selectedId = sessionStorage.getItem("aimcoachSelectedExperimentId");
                } catch (error) {
                    console.warn("Tab selection storage unavailable.", error);
                }
            }
            const path = selectedId ? `/experiments/${encodeURIComponent(selectedId)}` : "/experiments/current";
            try {
                state = await fetchJson(path, {cache: "no-store"});
                if (selectedId && state.experiment_id !== selectedId) {
                    throw new Error("服务器返回了不同实验，已拒绝恢复。");
                }
            } catch (error) {
                // Only a fresh installation creates automatically; never replace a missing selected ID.
                if (initial && !selectedId && error.status === 404) {
                    state = await createExperiment();
                } else {
                    throw error;
                }
            }
        }
        applyExperimentState(state);
        return state;
    } catch (error) {
        experimentError = `无法恢复实验：${error.message}`;
        invalidateRecommendation();
        throw error;
    } finally {
        experimentLoading = false;
        updateExperimentMessage();
    }
}

async function createExperiment() {
    const preferences = readSettings();
    return fetchJson("/experiments", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({trainer_width: trainerArea.clientWidth,
                              trainer_height: trainerArea.clientHeight, ...(preferences ? {settings: preferences} : {})}),
    });
}

function updateExperimentMessage() {
    try {
        if (experimentLoading || sessionSaving) {
            startMessage.textContent = "正在同步已保存的实验进度...";
            return;
        }
        if (hasUnconfirmedData()) {
            startMessage.textContent = "存在未确认保存的数据；请使用页面下方的重试或原始数据导出按钮。";
            return;
        }
        if (experimentError) {
            startMessage.textContent = experimentError;
            return;
        }
        if (!activeExperiment) {
            startMessage.textContent = "正在载入实验...";
            return;
        }
        if (experimentFinished) {
            startMessage.textContent = activeExperiment.ended ? "实验已提前结束，历史保留；点击创建新设置实验" : "实验已保存完成；可生成建议，或点击创建下一轮实验";
            return;
        }
        const condition = experimentSchedule[currentExperimentIndex];
        startMessage.textContent = `Session ${currentExperimentIndex + 1} / ${experimentSchedule.length}` +
            ` | Sensitivity ${condition.sensitivity} | 点击开始`;
    } finally {
        publishState();
    }
}

// 准星当前坐标。
// 初始化时会放到训练区域中心。
let crosshairX = trainerArea.clientWidth / 2;
let crosshairY = trainerArea.clientHeight / 2;

const targetRadius = 25;

let targetX = 0;
let targetY = 0;
// ------------------------------------------------------------
// 3. Clamp
//
// 把某个数字限制在 min ~ max 范围内。
//
// 例如：
// clamp(1200, 0, 1000)
// → 1000
//
// 这样准星就不会跑出训练区域。
// ------------------------------------------------------------

function clamp(value, min, max) {
    return Math.max(min, Math.min(value, max));
}


// ------------------------------------------------------------
// 4. Render
//
// Trainer State 发生变化后，
// 把新的准星位置显示到页面上。
// ------------------------------------------------------------

function renderCrosshair() {
    crosshair.style.left = `${crosshairX}px`;
    crosshair.style.top = `${crosshairY}px`;
}


// 初始化显示。
renderCrosshair();
updateExperimentMessage();

// ------------------------------------------------------------
// Spawn Target
//
// 在训练区域内部随机生成一个目标。
// 必须保留 targetRadius 的边距，
// 否则圆的一部分可能出现在屏幕外。
// ------------------------------------------------------------

// ------------------------------------------------------------
// Current Attempt
//
// 一个 Attempt 从 Target 出现开始，
// 到第一次 HIT 结束。
// ------------------------------------------------------------

let currentAttempt = null;

// ------------------------------------------------------------
// Training Session
//
// 当前第一版只负责：
// - 记录本轮训练开始时间
// - 保存 sensitivity
// - 保存完成的 attempts
// - 达到 targetCount 后结束
// ------------------------------------------------------------

let currentSession = null;

let sessionActive = false;

function spawnTarget() {

    const width = trainerArea.clientWidth;
    const height = trainerArea.clientHeight;

    targetX =
        targetRadius +
        Math.random() * (width - targetRadius * 2);

    targetY =
        targetRadius +
        Math.random() * (height - targetRadius * 2);

    target.style.left = `${targetX}px`;
    target.style.top = `${targetY}px`;

    target.style.display = "block";

    // 新 Target 出现，就意味着新的 Attempt 开始。
    currentAttempt = {
        attemptId: crypto.randomUUID(),
        target: {
            x: targetX,
            y: targetY,
            radius: targetRadius,

            // performance.now() 更适合记录训练中的相对时间。
            appearTime: performance.now(),

            // 保存 Target 出现瞬间准星位置。
            cursorStartX: crosshairX,
            cursorStartY: crosshairY
        },

        movements: [],

        clicks: []
    };
}
function startSession() {

    // Start only from confirmed server progress and the experiment's fixed viewport.
    if (!activeExperiment || experimentFinished || sessionSaving || planBusy) {
        return;
    }


    // --------------------------------------------------------
    // 获取当前实验条件
    // --------------------------------------------------------

    const condition =
        experimentSchedule[currentExperimentIndex];


    // 本轮真正改变的实验变量。
    trainerSensitivity =
        condition.sensitivity;

    const protocol = activeExperiment.protocol;
    if (trainerArea.clientWidth !== protocol.trainer_width ||
        trainerArea.clientHeight !== protocol.trainer_height) {
        experimentError = `请恢复实验窗口尺寸 ${protocol.trainer_width} × ${protocol.trainer_height} 后重试。`;
        document.exitPointerLock();
        updateExperimentMessage();
        return;
    }
    invalidateRecommendation();
    currentSession = {
        /*
         * 数据结构版本。
         *
         * 后面 Attempt / Session 字段一定还会调整。
         * schemaVersion 可以让未来的 Python 分析程序知道：
         * “这份 JSON 是哪个版本产生的？”
         */
        schemaVersion: "0.1",

        // Bind raw collection to the server-owned protocol and condition.
        experimentId: activeExperiment.experiment_id,
        protocolVersion: protocol.version,
        inputMode: currentInputMode,
        // Browser reports aid later inspection; they do not prove physical DPI or input authenticity.
        // Null preserves unknown values without inventing a device or changing the fixed protocol.
        clientEnvironment: {
            devicePixelRatio: Number.isFinite(window.devicePixelRatio) && window.devicePixelRatio > 0
                ? window.devicePixelRatio : null,
            visualViewportScale: Number.isFinite(window.visualViewport?.scale) && window.visualViewport.scale > 0
                ? window.visualViewport.scale : null,
        },
        sessionId: crypto.randomUUID(),

        /*
         * startedAt：
         * 现实世界时间，方便知道这轮训练是哪天进行的。
         *
         * startTime：
         * performance.now()，适合计算训练内部时间差。
         */
        startedAt: new Date().toISOString(),
        startTime: performance.now(),

        trainerSensitivity,

        // ----------------------------------------------------
        // 新增：实验元数据
        // ----------------------------------------------------

        // 当前 sensitivity 是它自己的第几次重复
        // 例如 0.8 的第 2 次实验
        sensitivityRepeat: condition.repeat,

        // 当前 Session 是整个随机实验中的第几轮
        // 例如整个 9 Session 实验中的第 6 轮
        experimentOrder: currentExperimentIndex + 1,

        /*
         * 当前坐标数据依赖训练区域尺寸。
         *
         * 如果只保存 x=800，
         * 却不知道画布到底是 1920 还是 1280，
         * 后续无法正确解释这个坐标。
         */
        trainerWidth: trainerArea.clientWidth,
        trainerHeight: trainerArea.clientHeight,

        targetCount: protocol.target_count,

        attempts: []
    };

    frozenSettings = {...DEFAULT_SETTINGS,sessions:experimentSchedule.length,attempts:protocol.target_count,sensitivity:condition.sensitivity,...(protocol.settings || {})};
    currentSession.settingsSnapshot = frozenSettings;
    currentSession.scene = "flick_2d";
    applyVisualSettings(frozenSettings,crosshair,target,trainerArea);
    paused=false;activeElapsed=0;activeStarted=performance.now();
    sessionActive = true;
    armLimit();
    publishState();

    console.log(
        `Start Session ${currentExperimentIndex + 1}` +
        ` | sensitivity=${trainerSensitivity}` +
        ` | repeat=${condition.repeat}`
    );

    spawnTarget();
}

// ------------------------------------------------------------
// Hit Detection
//
// 判断当前虚拟准星是否进入目标圆形范围。
// ------------------------------------------------------------
function isTargetHit() {
    const dx = crosshairX - targetX;
    const dy = crosshairY - targetY;

    const distance = Math.sqrt(
        dx * dx +
        dy * dy
    );

    return distance <= targetRadius;
}

// ------------------------------------------------------------
// Export Session
//
// 把当前 Training Session：
// JavaScript Object
//      ↓
// JSON String
//      ↓
// Blob
//      ↓
// 浏览器下载为 .json 文件
// ------------------------------------------------------------

function downloadSessionAsJson(session) {

    /*
     * null, 2 的作用是格式化 JSON，
     * 让导出的文件对人也比较容易阅读。
     */
    const jsonString = JSON.stringify(session, null, 2);

    /*
     * Blob 可以理解成浏览器内存中的一个临时文件。
     */
    const blob = new Blob(
        [jsonString],
        { type: "application/json" }
    );

    /*
     * 给这个临时文件创建一个浏览器可以访问的 URL。
     */
    const url = URL.createObjectURL(blob);

    /*
     * 临时创建一个 <a>，
     * 利用浏览器原生 download 能力保存文件。
     */
    const link = document.createElement("a");

    link.href = url;

    /*
     * 文件名里保留 Session ID 的前一部分，
     * 以后多轮训练时不容易覆盖。
     */
    const shortSessionId = session.sessionId.slice(0, 8);

    const sensitivityLabel =
    String(session.trainerSensitivity)
        .replace(".", "_");


    link.download =
        `aim-sens-${sensitivityLabel}` +
        `-rep-${session.sensitivityRepeat}` +
        `-order-${String(
            session.experimentOrder
        ).padStart(2, "0")}` +
        `-${shortSessionId}.json`;

    link.click();

    /*
     * 文件触发下载后释放临时 URL，
     * 避免一直占浏览器内存。
     */
    URL.revokeObjectURL(url);
}

// ============================================================
// Session Persistence v0.1
//
// Training Session
//      ↓
// POST /sessions
//      ↓
// FastAPI
//      ↓
// SQLite
//
// Trainer 只负责产生并上传 Raw Session。
// 它不负责数据库操作本身。
// ============================================================

const SESSION_API =
    "/sessions";


async function saveSessionToBackend(session) {
    const result = await fetchJson(session.status === "incomplete" ? "/sessions/incomplete" : SESSION_API, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(session),
    });
    // HTTP 200 alone is not an acknowledgement of this payload.
    if (result.saved !== true || result.session_id !== session.sessionId) {
        throw new Error("保存确认与当前 Session 不一致；数据仍在待上传队列。");
    }
    return result;
}

// ============================================================
// Attempt Completion
//
// HIT 发生以后：
// Current Attempt
//      ↓
// Finalize
//      ↓
// Session.attempts
//
// 不负责 Session 是否结束。
// ============================================================

function completeCurrentAttempt() {
    currentAttempt.target.disappearTime =
        performance.now();

    currentSession.attempts.push(
        currentAttempt
    );

    console.log(
        `Attempt ${currentSession.attempts.length} completed`
    );

    // Attempt 已经进入 Session，
    // 当前引用不再继续使用。
    currentAttempt = null;
}

// ============================================================
// Session Persistence
//
// 优先保存到 Backend / SQLite。
//
// 如果 Backend 不可用，
// 才退回本地 JSON 下载，避免真实训练数据丢失。
// ============================================================

function setSaveStatus(message) {
    document.getElementById("saveStatus").textContent = message;
    document.getElementById("retrySaveButton").disabled = sessionSaving || planBusy || sessionActive;
    publishState();
}

function exportPendingSessions() {
    // A download trigger is not proof the browser stored the file; ask the user to check it.
    const payload = JSON.stringify({format: "aimcoach-pending-v1", records: [...pendingQueue.rawEntries()]}, null, 2);
    const url = URL.createObjectURL(new Blob([payload], {type: "application/json"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = "aimcoach-pending-backup.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setSaveStatus("已触发原始数据下载；请确认文件确实保存。待上传队列未删除。" +
        (pendingQueue.memoryOnly ? " 数据仅在当前页面内存中，请保持页面打开。" : ""));
}

async function retryPendingSessions() {
    // Only one local flush runs at a time. Server idempotency handles other tabs and lost replies.
    if (sessionSaving || sessionActive || planBusy) return;
    const pending = pendingQueue.list();
    if (pending.errors.length) {
        setSaveStatus("待上传状态损坏或冲突；请先导出备份，不能自动覆盖或删除。");
        return;
    }
    if (!pending.sessions.length) {
        setSaveStatus("没有待上传数据。");
        return;
    }
    sessionSaving = true;
    invalidateRecommendation();
    setSaveStatus(`保存中：${pending.sessions.length} 个 Session 待确认。`);
    updateExperimentMessage();
    let acknowledged = 0;
    try {
        for (const session of pending.sessions) {
            for (let attempt = 0; attempt < 2; attempt++) {
                try {
                    await saveSessionToBackend(session);
                    break;
                } catch (error) {
                    // Retry transient failures once, without generating a new payload or ID.
                    if (attempt === 0 && error.retryable) {
                        setSaveStatus("暂时无法确认保存，正在使用原 Session ID 重试一次...");
                        continue;
                    }
                    throw error;
                }
            }
            acknowledged++;
            if (!pendingQueue.acknowledge(session.sessionId)) {
                throw new Error("服务器已确认保存，但本地队列清理失败；再次重试不会重复写入。");
            }
        }
        setSaveStatus(`保存成功：服务器已确认 ${acknowledged} 个 Session。`);
        try {
            // Sync progress separately: a failed GET must not undo a confirmed POST.
            await refreshExperiment();
            localStorage.setItem("aimcoachEvidenceRevision", crypto.randomUUID());
        } catch (error) {
            setSaveStatus(`服务器已确认保存；进度同步或跨页通知未完成：${error.message}`);
        }
    } catch (error) {
        setSaveStatus(`保存未全部确认：${error.message} ` + (pendingQueue.memoryOnly
            ? "数据仅在当前页面内存中，请立即导出并保持页面打开。"
            : "数据保留在浏览器待上传队列，可重试或导出。"));
        try { downloadSessionAsJson(pending.sessions[Math.min(acknowledged, pending.sessions.length - 1)]); }
        catch (downloadError) { setSaveStatus("自动下载未成功触发；数据仍保留，请使用手动导出按钮。"); }
    } finally {
        sessionSaving = false;
        document.getElementById("retrySaveButton").disabled = false;
        updateExperimentMessage();
    }
}

async function completeSession() {
    currentSession.status ||= "complete";
    currentSession.activeDurationMs ??= activeMilliseconds();
    stopTimer();
    currentSession.endTime = performance.now();
    currentSession.endedAt = new Date().toISOString();
    // Persist the exact completed report before any network request or progress transition.
    const completedSession = currentSession;
    const durable = pendingQueue.put(completedSession);
    sessionActive = false;
    currentAttempt = null;
    target.style.display = "none";
    invalidateRecommendation();
    document.exitPointerLock();
    if (!durable) {
        setSaveStatus("浏览器存储不可用；数据仅在当前页面内存中，请立即导出并保持页面打开。");
        try { exportPendingSessions(); } catch (error) { /* Keep the in-memory payload for manual retry. */ }
    }
    await retryPendingSessions();
}

// ------------------------------------------------------------
// 5. 请求 Pointer Lock
//
// 用户点击训练区域后，
// 浏览器锁定真实系统鼠标。
//
// Pointer Lock 必须由用户交互触发，
// 所以不能页面加载后自动进入。
// ------------------------------------------------------------

trainerArea.addEventListener("click", async () => {
    // Ignore programmatic clicks when Home is visible; hidden geometry cannot define a protocol.
    if (trainerArea.closest?.("[hidden]")) return;
    // Avoid collecting against unfinished saves or a recommendation in progress.
    if (experimentLoading || sessionSaving || planBusy || pointerLockPending || countdown || hasUnconfirmedData()) {
        if (hasUnconfirmedData()) setSaveStatus("请先重试或导出待上传数据；不会创建重复训练条件。");
        return;
    }
    if (!activeExperiment || experimentFinished) {
        try {
            // Creating a new experiment is explicit; refresh alone keeps the completed evidence.
            await refreshExperiment({initial: !activeExperiment, startNew: experimentFinished});
        } catch (error) {
            console.error(error);
        }
        return; // Pointer Lock needs a fresh user gesture after asynchronous setup.
    }
    if (document.pointerLockElement === trainerArea) {
        return;
    }

    pointerLockPending = true;
    publishState();
    // Page chrome is outside the unchanged viewport-sized surface; show all targets before locking.
    trainerArea.scrollIntoView?.({block: "start", behavior: "instant"});
    try {
        /*
         * 请求不经过操作系统鼠标加速度处理的输入。
         *
         * 对 Aim Trainer 很重要：
         * 我们希望相同物理鼠标位移尽可能产生相同 raw delta，
         * 而不是因为移动速度不同，被系统额外放大。
         */
        currentInputMode = "unadjusted_requested";
        await trainerArea.requestPointerLock({
            unadjustedMovement: true
        });
    } catch (error) {
        /*
         * 某些浏览器可能不支持 unadjustedMovement。
         * 第一版先退回普通 Pointer Lock，
         * 不让整个 Trainer 因兼容性直接无法运行。
         */
        console.warn(
            "Raw mouse input unavailable, fallback to normal Pointer Lock.",
            error
        );

        currentInputMode = "adjusted_fallback";
        try {
            await trainerArea.requestPointerLock();
        } catch (fallbackError) {
            experimentError = "浏览器无法锁定鼠标；未开始训练，请检查权限后重试。";
            updateExperimentMessage();
        }
    }
    finally {
        pointerLockPending = false;
        publishState();
    }
});


// ------------------------------------------------------------
// 6. 监听 Pointer Lock 状态变化
//
// document.pointerLockElement 表示：
// 当前究竟是哪一个 DOM 元素拥有 Pointer Lock。
// ------------------------------------------------------------

document.addEventListener("pointerlockchange", async () => {
    const isLocked = document.pointerLockElement === trainerArea;

    if (isLocked) {
        if(paused && sessionActive) {
            try {
                const state=await fetchJson(`/experiments/${encodeURIComponent(currentSession.experimentId)}`,{cache:"no-store"});
                if(state.experiment_id!==currentSession.experimentId || state.ended || state.completed_count!==currentSession.experimentOrder-1){
                    await finishIncomplete("condition_changed");return;
                }
            }catch(error){
                document.exitPointerLock();lifecycleMessage(`无法确认继续条件，仍保持暂停：${error.message}`);return;
            }
            paused=false;pauseRequested=false;activeStarted=performance.now();
            trainerArea.scrollIntoView?.({block:"start",behavior:"instant"});
            startMessage.style.display="none";spawnTarget();armLimit();publishState();return;
        }

        // A late browser lock after leaving Test must never collect against hidden geometry.
        if (trainerArea.closest?.("[hidden]")) {
            document.exitPointerLock();
            return;
        }

        try {
            // Re-read after locking: another tab may have committed this condition meanwhile.
            await refreshExperiment();
            if (document.pointerLockElement !== trainerArea) return;
            if (experimentFinished || sessionSaving || planBusy) {
                document.exitPointerLock();
                return;
            }
            startMessage.style.display = "none";
            // Restoration and modal focus changes may scroll while the server read is pending.
            trainerArea.scrollIntoView?.({block: "start", behavior: "instant"});
            const seconds=activeExperiment.protocol.settings?.countdown_seconds || 0;
            if(seconds) {
                countdown=true;publishState();
                for(let remaining=seconds;remaining>0;remaining--) {
                    startMessage.style.display="block";startMessage.textContent=`${remaining}`;
                    await new Promise(resolve=>setTimeout(resolve,1000));
                    if(document.pointerLockElement!==trainerArea){countdown=false;return;}
                }
                countdown=false;publishState();startMessage.style.display="none";
            }
            startSession();
        } catch (error) {
            document.exitPointerLock();
        }

    } else {
        countdown=false;
        if(pauseRequested && paused){publishState();return;}
        if(sessionActive){void finishIncomplete("escape");return;}
        target.style.display =
            "none";


        // Escape archives interrupted data above; an idle unlock only restores the prompt.

        startMessage.style.display =
            "block";

        updateExperimentMessage();
    }
});


// ------------------------------------------------------------
// 7. Mouse Movement
//
// 这是 Build 0 最核心的数据流。
// ------------------------------------------------------------

// ------------------------------------------------------------
// Click
//
// MISS：什么都不发生，继续瞄准当前 Target。
//
// HIT：当前 Target 完成，立即生成下一个。
// 这就是之前确定的方案 B + 即时生成方案 A。
// ------------------------------------------------------------

// ============================================================
// Shooting / Click Interaction
//
// MISS:
//     记录 Click，继续当前 Attempt。
//
// HIT:
//     完成 Attempt。
//     如果达到 targetCount → 完成 Session。
//     否则生成下一个 Target。
// ============================================================

document.addEventListener(
    "mousedown",
    (event) => {

        // Only the primary button records shots; other buttons must not change evidence.
        if (event.button !== 0) return;

        if (
            document.pointerLockElement
            !== trainerArea
        ) {
            return;
        }

        if (
            !sessionActive
            || paused
            || currentAttempt === null
        ) {
            return;
        }


        // ----------------------------------------------------
        // Record Click
        // ----------------------------------------------------

        const hit =
            isTargetHit();

        const shotTime=performance.now();
        currentAttempt.clicks.push({
            time: shotTime,

            x: crosshairX,
            y: crosshairY,

            hit
        });
        shotFeedback(hit,frozenSettings,trainerArea);


        // ----------------------------------------------------
        // MISS
        //
        // 保留数据，但 Attempt 不结束。
        // ----------------------------------------------------

        if (!hit) {
            return;
        }


        // ----------------------------------------------------
        // HIT
        //
        // 当前 Attempt 结束。
        // ----------------------------------------------------

        completeCurrentAttempt();


        // ----------------------------------------------------
        // Session Completion
        // ----------------------------------------------------

        if (
            currentSession.attempts.length
            >= currentSession.targetCount
        ) {
            // completeSession 内部自己处理异常，
            // 当前 event handler 不需要等待网络请求。
            void completeSession();

            return;
        }


        // ----------------------------------------------------
        // Session Continue
        // ----------------------------------------------------

        spawnTarget();
    }
);

document.addEventListener("mousemove", (event) => {

    // 没进入 Pointer Lock 时，不让鼠标控制虚拟准星。
    if (document.pointerLockElement !== trainerArea) {
        return;
    }


    // --------------------------
    // Raw Input
    // --------------------------

    const rawDX = event.movementX;
    const rawDY = event.movementY;

    // --------------------------
    // Trainer Sensitivity
    //
    // 这是我们第一版 sensitivity 的定义：
    //
    // virtual movement
    // =
    // raw movement × sensitivity
    // --------------------------

    const trainerDX = rawDX * trainerSensitivity;
    const trainerDY = rawDY * trainerSensitivity;


    // --------------------------
    // 更新 Trainer State
    // --------------------------

    crosshairX += trainerDX;
    crosshairY += trainerDY;


    // --------------------------
    // 限制准星不能离开训练区域。
    // --------------------------

    crosshairX = clamp(
        crosshairX,
        0,
        trainerArea.clientWidth
    );

    crosshairY = clamp(
        crosshairY,
        0,
        trainerArea.clientHeight
    );

    if (sessionActive && currentAttempt !== null) {
        currentAttempt.movements.push({
            time: performance.now(),
    
            rawDX,
            rawDY,
    
            x: crosshairX,
            y: crosshairY
        });
    }

    // --------------------------
    // State → UI
    // --------------------------

    renderCrosshair();
});


// ============================================================
// AI Experiment Planner
//
// Frontend Responsibility:
//
// User Goal
//      ↓
// POST /agent/plan
//      ↓
// FastAPI Backend
//      ↓
// Agent Runtime
//      ↓
// Render Result
//
// 前端不负责：
// - 分析 sensitivity
// - 调用 Tool
// - 做实验决策
//
// 这些职责全部留在 Backend。
// ============================================================


const agentGoalInput = document.getElementById(
    "agentGoal"
);

const generatePlanButton = document.getElementById(
    "generatePlanButton"
);

const agentStatus = document.getElementById(
    "agentStatus"
);

const agentResult = document.getElementById(
    "agentResult"
);

const agentAnswer = document.getElementById(
    "agentAnswer"
);

const agentTools = document.getElementById(
    "agentTools"
);


// Backend API 当前是本地 FastAPI。
const AGENT_PLAN_API =
    "/agent/plan";


async function generateExperimentPlan() {

    const goal = agentGoalInput.value.trim();


    // --------------------------------------------------------
    // Client-side Validation
    //
    // 空 Goal 没必要发送到 Backend。
    // --------------------------------------------------------

    if (!goal) {
        agentStatus.textContent =
            "请先输入训练目标。";

        return;
    }


    // Do not plan while training, saving, restoring or another plan is in progress.
    if (sessionActive || sessionSaving || experimentLoading || planBusy || hasUnconfirmedData()) {
        agentStatus.textContent =
            "请先完成训练并确认待上传数据保存。";

        return;
    }


    // --------------------------------------------------------
    // Loading State
    // --------------------------------------------------------

    planBusy = true;
    planFailureCode = null;
    planAbortController = new AbortController();
    document.getElementById("cancelPlanButton").disabled = false;
    generatePlanButton.disabled = true;

    agentStatus.textContent =
        "AI Aim Coach 正在分析现有实验数据...";

    agentResult.hidden = true;
    publishState();


    try {
        // Refresh the selected experiment before requesting evidence; no result cache is reused.
        await refreshExperiment();
        const requestedExperimentId = activeExperiment.experiment_id;
        const requestVersion = ++planRequestVersion;

        // ----------------------------------------------------
        // Browser
        //     ↓
        // HTTP POST
        //     ↓
        // FastAPI /agent/plan
        // ----------------------------------------------------

        const data = await fetchJson(
            AGENT_PLAN_API,
            {
                method: "POST",
                signal: planAbortController.signal,

                headers: {
                    "Content-Type":
                        "application/json",
                },

                body: JSON.stringify({
                    goal: goal,
                    experiment_id: requestedExperimentId,
                }),
            }, planTimeoutMs
        );
        if (data.status !== "ok") throw new Error("Agent 未返回已验证的成功状态。");
        // A second read detects uploads from other tabs while the model was running.
        const latestState = await fetchJson(
            `/experiments/${encodeURIComponent(requestedExperimentId)}`, {cache: "no-store"}
        );
        if (requestVersion !== planRequestVersion ||
            data.evidence.experiment_id !== requestedExperimentId ||
            activeExperiment.experiment_id !== requestedExperimentId ||
            JSON.stringify(latestState.completed_session_ids) !== JSON.stringify(data.evidence.session_ids)) {
            // A stale response must never restore an experiment the user has already left.
            if (activeExperiment.experiment_id === requestedExperimentId) {
                applyExperimentState(latestState);
            }
            agentStatus.textContent = "实验数据已更新，请重新生成建议。";
            agentResult.hidden = true;
            return;
        }
        // The snapshot may include a save committed just after the initial progress read.
        applyExperimentState(latestState);


        // ----------------------------------------------------
        // API Response
        //
        // {
        //     goal,
        //     answer,
        //     tools_used, evidence
        // }
        // ----------------------------------------------------

        agentAnswer.textContent =
            data.answer;

        agentTools.textContent =
            data.tools_used.join(" → ");

        recommendationEvidence = data.evidence;
        agentResult.hidden = false;

        agentStatus.textContent =
            `Experiment ${data.evidence.experiment_id} | ${data.evidence.session_count} Sessions` +
            ` | Snapshot ${data.evidence.snapshot_id.slice(0, 12)}` +
            (data.evidence.incomplete ? " | 实验尚未完成" : " | 实验完成");

    } catch (error) {

        console.error(
            "Failed to generate experiment plan:",
            error
        );
        planFailureCode = error.code || (error.cancelled ? "cancelled" : "request_failed");

        agentStatus.textContent =
            `无法生成实验建议：${error.message}`;

    } finally {

        planBusy = false;
        planAbortController = null;
        document.getElementById("cancelPlanButton").disabled = true;
        generatePlanButton.disabled = false;
        publishState();
    }
}


generatePlanButton.addEventListener(
    "click",
    generateExperimentPlan
);
// Restore server state after refresh; browser counters are not a source of truth.
async function restoreApplication() {
    const pending = pendingQueue.list();
    // Recover queued data before allowing a new experiment or a new recommendation.
    if (pending.sessions.length) {
        try {
            if (!sessionStorage.getItem("aimcoachSelectedExperimentId")) {
                sessionStorage.setItem("aimcoachSelectedExperimentId", pending.sessions[0].experimentId);
            }
        } catch (error) { /* Server state still owns progress if tab storage is unavailable. */ }
        setSaveStatus("发现待上传数据，正在恢复并确认保存...");
    }
    try { await refreshExperiment({initial: true}); }
    catch (error) { setSaveStatus(`无法同步服务器：${error.message} 待上传数据未删除。`); }
    if (pending.sessions.length || pending.errors.length) await retryPendingSessions();
}
// The shell awaits initial restoration before allowing a page transition.
export const ready = restoreApplication();

document.getElementById("retrySaveButton").addEventListener("click", () => { void retryPendingSessions(); });
document.getElementById("exportPendingButton").addEventListener("click", () => {
    try { exportPendingSessions(); }
    catch (error) { setSaveStatus("下载未成功触发；待上传数据仍保留，请保持页面打开。"); }
});
document.getElementById("cancelPlanButton").addEventListener("click", () => {
    invalidateRecommendation();
    planAbortController?.abort();
});
document.addEventListener("pointerlockerror", () => {
    // Some browsers emit the terminal error event without settling the lock Promise.
    pointerLockPending = false;
    experimentError = "鼠标锁定失败；本轮尚未开始，可检查浏览器权限后重试。";
    updateExperimentMessage();
});
document.addEventListener("keydown",event=>{
    if(!sessionActive||event.repeat||event.ctrlKey||event.metaKey||event.altKey)return;
    if(event.code==="KeyP"){event.preventDefault();if(paused)trainerArea.click();else pauseTraining();}
    if(event.code==="KeyE"){event.preventDefault();void finishIncomplete("user_ended");}
});
document.getElementById("pauseTraining").addEventListener("click",()=>{
    if(paused){trainerArea.click();}else pauseTraining();
});
document.getElementById("endTraining").addEventListener("click",()=>{void finishIncomplete("user_ended");});
window.addEventListener("beforeunload", (event) => {
    if(sessionActive && currentSession){
        // Only synchronous durable storage is reliable during page exit. Replay uses the same ID.
        const interrupted={...currentSession,status:"incomplete",interruptionReason:"page_exit",activeDurationMs:activeMilliseconds(),endTime:performance.now(),endedAt:new Date().toISOString(),interruptedAttempt:currentAttempt};
        pendingQueue.put(interrupted);
        // If the browser cancels navigation, the same ID must not continue mutating after queuing.
        sessionActive=false;paused=false;pauseRequested=false;currentAttempt=null;stopTimer();
        target.style.display="none";document.exitPointerLock();
        setSaveStatus("退出时的未完成记录已进入待上传队列；若继续留在此页，请重试确认保存。");
    }
    // Warn only when browser storage failed and unsaved data survives solely in memory.
    if (pendingQueue.memoryOnly && hasUnconfirmedData()) {
        event.preventDefault();
        event.returnValue = "";
    }
});

window.addEventListener("focus", () => {
    if (!sessionActive && !sessionSaving && !planBusy && !experimentLoading) {
        void refreshExperiment().catch(console.error);
    }
});

window.addEventListener("resize", () => {
    // A resized viewport would mix coordinate systems inside the same fixed-size protocol.
    if (sessionActive && (trainerArea.clientWidth !== currentSession.trainerWidth ||
                          trainerArea.clientHeight !== currentSession.trainerHeight)) {
        experimentError = "窗口尺寸已变化；本次未完成 Session 已中止，请恢复实验尺寸后重试。";
        document.exitPointerLock();
    }
});

window.addEventListener("storage", (event) => {
    if (event.key === "aimcoachEvidenceRevision") {
        // Cross-tab notifications only invalidate; evidence is always fetched from the server.
        invalidateRecommendation();
        if (!sessionActive && !sessionSaving && !planBusy && !experimentLoading) {
            void refreshExperiment().catch(console.error);
        }
    }
});

export async function endSelectedExperiment() {
    if(!canLeaveTrainingPage()||sessionSaving||planBusy||experimentLoading||hasUnconfirmedData()||!activeExperiment){
        lifecycleMessage("请先结束当前 Session、退出鼠标锁定并确认所有待上传数据。");return;
    }
    experimentLoading=true;updateExperimentMessage();
    try {
        const state=await fetchJson(`/experiments/${encodeURIComponent(activeExperiment.experiment_id)}/end`,{method:"POST"});
        applyExperimentState(state);invalidateRecommendation();
        lifecycleMessage("实验已结束，原始数据保留。点击训练区创建新设置实验，再次点击开始。");
    }catch(error){lifecycleMessage(`结束实验失败：${error.message}`);}
    finally{experimentLoading=false;updateExperimentMessage();}
}
document.getElementById("endExperiment").addEventListener("click",()=>{void endSelectedExperiment();});
