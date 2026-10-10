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
// 暂时不处理 Target、Session、Attempt、数据保存。
// ============================================================


// ------------------------------------------------------------
// 1. 获取页面元素
// ------------------------------------------------------------

const trainerArea = document.getElementById("trainer-area");
const crosshair = document.getElementById("crosshair");
const target = document.getElementById("target");
const startMessage = document.getElementById("start-message");

// ============================================================
// Experiment ID v0.1
//
// 当前阶段暂时由浏览器维护 Experiment 编号。
//
// localStorage 的作用：
// - 页面刷新后编号不会消失
// - 浏览器重新打开后仍然保留
//
// 未来如果加入账号 / Backend Experiment State，
// Experiment ID 应该改由后端生成。
// ============================================================


const EXPERIMENT_COUNTER_KEY =
    "aimcoachExperimentCounter";

// 如果浏览器从来没有保存过编号，
// 默认从 Experiment 1 开始。
function getExperimentNumber() {
    const storedValue = localStorage.getItem(
        EXPERIMENT_COUNTER_KEY
    );

    if (storedValue === null) {
        return 1;
    }

    return Number(storedValue);
}


// 将数字 4 转换成：
//
// exp_004
//
// padStart 保证编号长度统一，
// 以后文件、数据库和日志里更容易阅读。
function getCurrentExperimentId() {
    const experimentNumber =
        getExperimentNumber();

    return (
        "exp_"
        + String(experimentNumber).padStart(
            3,
            "0"
        )
    );
}


// 只有“一整个 Experiment 完成”时调用。
//
// 注意：
// 不是每完成一个 Session 就 +1。
function advanceExperimentId() {
    const nextExperimentNumber =
        getExperimentNumber() + 1;

    localStorage.setItem(
        EXPERIMENT_COUNTER_KEY,
        String(nextExperimentNumber)
    );
}


// ------------------------------------------------------------
// 2. Trainer State
//
// 这些变量代表当前训练器最小状态。
// ------------------------------------------------------------

const EXPERIMENT_SENSITIVITIES = [
    0.8,
    1.0,
    1.2
];

const SESSIONS_PER_SENSITIVITY = 3;

let trainerSensitivity = 1.0;

const targetCount = 3;


// ------------------------------------------------------------
// Experiment Controller
//
// 生成：
//
// 0.8 × 3
// 1.0 × 3
// 1.2 × 3
//
// 然后随机打乱。
// ------------------------------------------------------------

function shuffle(array) {
    const result = [...array];

    // Fisher-Yates Shuffle
    for (let i = result.length - 1; i > 0; i--) {
        const j = Math.floor(
            Math.random() * (i + 1)
        );

        [result[i], result[j]] = [
            result[j],
            result[i]
        ];
    }

    return result;
}


function createExperimentSchedule() {
    const conditions = [];

    for (const sensitivity of EXPERIMENT_SENSITIVITIES) {
        for (
            let repeat = 1;
            repeat <= SESSIONS_PER_SENSITIVITY;
            repeat++
        ) {
            conditions.push({
                sensitivity,
                repeat
            });
        }
    }

    return shuffle(conditions);
}


const experimentSchedule =
    createExperimentSchedule();

let currentExperimentIndex = 0;

let experimentFinished = false;


console.log(
    "Experiment Schedule:",
    experimentSchedule
);

function updateExperimentMessage() {

    if (experimentFinished) {
        startMessage.textContent =
            "实验完成：所有 Session 已采集";

        return;
    }

    const condition =
        experimentSchedule[currentExperimentIndex];

    startMessage.textContent =
        `Session ${currentExperimentIndex + 1}` +
        ` / ${experimentSchedule.length}` +
        ` | Sensitivity ${condition.sensitivity}` +
        ` | 点击开始`;
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

    // 所有实验完成以后，不允许再启动新的 Session。
    if (experimentFinished) {
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

    currentSession = {
        /*
         * 数据结构版本。
         *
         * 后面 Attempt / Session 字段一定还会调整。
         * schemaVersion 可以让未来的 Python 分析程序知道：
         * “这份 JSON 是哪个版本产生的？”
         */
        schemaVersion: "0.1",

        experimentId:
        getCurrentExperimentId(),
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

        targetCount,

        attempts: []
    };

    sessionActive = true;

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
    const response = await fetch(
        SESSION_API,
        {
            method: "POST",

            headers: {
                "Content-Type": "application/json",
            },

            // currentSession 本身就是 Raw Session Schema，
            // 不在 Frontend 重新构造另一份数据。
            body: JSON.stringify(session),
        }
    );


    if (!response.ok) {
        let errorDetail = "";

        try {
            const errorBody =
                await response.json();
        
            const detail =
                errorBody.detail;
        
            if (typeof detail === "string") {
                errorDetail = detail;
            } else {
                errorDetail =
                    JSON.stringify(detail);
            }
        
        } catch {
            // Backend 未返回 JSON 时保持空错误信息。
        }

        throw new Error(
            `Failed to save session: `
            + `${response.status} `
            + `${errorDetail}`
        );
    }


    return await response.json();
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

async function persistCompletedSession(session) {
    try {
        const result =
            await saveSessionToBackend(session);

        console.log(
            "[Session Saved]",
            result.session_id
        );

    } catch (error) {
        console.error(
            "[Session Save Failed]",
            error
        );

        console.warn(
            "Fallback to local JSON download."
        );

        downloadSessionAsJson(session);
    }
}

// ============================================================
// Session Completion
//
// 负责：
// 1. Finalize Session
// 2. 推进 Experiment
// 3. 退出训练状态
// 4. 持久化 Session
//
// 数据分析和 Agent 决策不属于这里。
// ============================================================

async function completeSession() {

    // --------------------------------------------------------
    // 1. Finalize Session Data
    // --------------------------------------------------------

    currentSession.endTime =
        performance.now();

    currentSession.endedAt =
        new Date().toISOString();


    // 保存这一轮已经完成的数据引用。
    //
    // 后面 currentSession 可以被下一轮覆盖，
    // completedSession 仍然指向刚完成的 Session。
    const completedSession =
        currentSession;


    // --------------------------------------------------------
    // 2. Exit Current Training State
    // --------------------------------------------------------

    sessionActive = false;

    currentAttempt = null;

    target.style.display = "none";


    console.log(
        "Training Session completed:",
        completedSession
    );


    // --------------------------------------------------------
    // 3. Advance Experiment
    // --------------------------------------------------------

    currentExperimentIndex++;


    if (
        currentExperimentIndex
        >= experimentSchedule.length
    ) {
        experimentFinished = true;

        console.log(
            "Experiment completed:",
            completedSession.experimentId
        );

        // 整个 Experiment 完成后，
        // 下一轮 Experiment 才使用新的 ID。
        advanceExperimentId();
    }


    // --------------------------------------------------------
    // 4. Leave Pointer Lock
    //
    // 不需要等网络请求完成才让玩家退出训练状态。
    // --------------------------------------------------------

    document.exitPointerLock();


    // --------------------------------------------------------
    // 5. Persist Raw Session
    //
    // 现在才进入 Backend / SQLite。
    // 即使失败，也会自动 fallback 到 JSON。
    // --------------------------------------------------------

    await persistCompletedSession(
        completedSession
    );
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
    if (document.pointerLockElement === trainerArea) {
        return;
    }

    try {
        /*
         * 请求不经过操作系统鼠标加速度处理的输入。
         *
         * 对 Aim Trainer 很重要：
         * 我们希望相同物理鼠标位移尽可能产生相同 raw delta，
         * 而不是因为移动速度不同，被系统额外放大。
         */
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

        trainerArea.requestPointerLock();
    }
});


// ------------------------------------------------------------
// 6. 监听 Pointer Lock 状态变化
//
// document.pointerLockElement 表示：
// 当前究竟是哪一个 DOM 元素拥有 Pointer Lock。
// ------------------------------------------------------------

document.addEventListener("pointerlockchange", () => {
    const isLocked = document.pointerLockElement === trainerArea;

    if (isLocked) {

        // 实验已经结束，不再启动 Session。
        if (experimentFinished) {
            document.exitPointerLock();
            return;
        }

        startMessage.style.display =
            "none";

        startSession();

    } else {

        target.style.display =
            "none";


        // ------------------------------------------------
        // 如果 Session 还没完成用户就按 Esc，
        // 当前 Session 直接作废。
        //
        // 不增加 experiment index，
        // 重新进入时仍然打同一个 condition。
        // ------------------------------------------------

        if (sessionActive) {
            console.warn(
                "Session aborted. " +
                "Current data discarded."
            );

            sessionActive = false;

            currentSession = null;
            currentAttempt = null;
        }


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
    () => {

        if (
            document.pointerLockElement
            !== trainerArea
        ) {
            return;
        }

        if (
            !sessionActive
            || currentAttempt === null
        ) {
            return;
        }


        // ----------------------------------------------------
        // Record Click
        // ----------------------------------------------------

        const hit =
            isTargetHit();

        currentAttempt.clicks.push({
            time: performance.now(),

            x: crosshairX,
            y: crosshairY,

            hit
        });


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


    // 如果当前仍然在训练，
    // 不应该同时进入实验规划流程。
    if (sessionActive) {
        agentStatus.textContent =
            "请先完成当前训练 Session。";

        return;
    }


    // --------------------------------------------------------
    // Loading State
    // --------------------------------------------------------

    generatePlanButton.disabled = true;

    agentStatus.textContent =
        "AI Aim Coach 正在分析现有实验数据...";

    agentResult.hidden = true;


    try {

        // ----------------------------------------------------
        // Browser
        //     ↓
        // HTTP POST
        //     ↓
        // FastAPI /agent/plan
        // ----------------------------------------------------

        const response = await fetch(
            AGENT_PLAN_API,
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json",
                },

                body: JSON.stringify({
                    goal: goal,
                }),
            }
        );


        // HTTP 4xx / 5xx 不会自动让 fetch 抛异常，
        // 所以这里必须主动检查。
        if (!response.ok) {

            throw new Error(
                `Agent API failed: `
                + `${response.status}`
            );
        }


        const data = await response.json();


        // ----------------------------------------------------
        // API Response
        //
        // {
        //     goal,
        //     answer,
        //     tools_used
        // }
        // ----------------------------------------------------

        agentAnswer.textContent =
            data.answer;

        agentTools.textContent =
            data.tools_used.join(" → ");

        agentResult.hidden = false;

        agentStatus.textContent =
            "Experiment plan generated.";

    } catch (error) {

        console.error(
            "Failed to generate experiment plan:",
            error
        );

        agentStatus.textContent =
            "无法生成实验建议，请检查 Backend 是否正在运行。";

    } finally {

        generatePlanButton.disabled = false;
    }
}


generatePlanButton.addEventListener(
    "click",
    generateExperimentPlan
);