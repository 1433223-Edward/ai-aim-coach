import {bindScenarioLibrary} from "./scenario_ui.js";
import {bindTrainingPlans} from "./training_plans.js";
import {bindSettings} from "./settings.js";
// The router must match the route names in this shell, even with older assets cached.
import {createNavigation} from "./navigation.js?v=phase4-routes-1";
import {bindTrainingUI} from "./training_ui.js";
import {bindRecommendationUI} from "./recommendation_ui.js";
import {createWorkspace} from "./workspace.js";

let trainer = null;
let trainerPromise = null;
let drawer = null;
const navigationStatus = document.getElementById("navigationStatus");
let workspace;
try {
    workspace=createWorkspace(document,window);
    bindSettings(() => workspace?workspace.canOpenSettings():!!trainer&&trainer.canLeaveTrainingPage());
} catch (error) {
    // Expose bootstrap failures instead of leaving working-looking links on an inert shell.
    navigationStatus.textContent = `页面初始化失败：${error.message}。请刷新重试。`;
    throw error;
}
document.getElementById("skipContent").addEventListener("click", (event) => {
    // A content anchor must not replace the Home/Test routing hash.
    event.preventDefault();
    document.getElementById("pageContent").focus();
});

async function initializeTrainer() {
    // Import and bind exactly once, after Test is visible and has measurable viewport geometry.
    trainerPromise ||= import("./trainer.js").then(async (module) => {
        await module.ready;
        trainer = module;
        bindTrainingUI(trainer, document);
        const scenarios=bindScenarioLibrary(trainer,document);
        workspace?.bind(trainer,scenarios);
        bindTrainingPlans(trainer,document,plan=>workspace?.setPlan(plan));
        drawer = bindRecommendationUI(trainer, document);
        return trainer;
    }).catch((error) => {
        navigationStatus.textContent = `训练模块未能载入，请刷新重试：${error.message}`;
        throw error;
    });
    return trainerPromise;
}

const navigation = createNavigation({
    window,
    canNavigate: () => !trainer || trainer.canLeaveTrainingPage(),
    onBlocked: (error) => { navigationStatus.textContent = error?.message
        ? `页面载入失败，请刷新重试：${error.message}` : "请先按 Esc 退出训练，或等待实验恢复完成后切换页面。"; },
    showPage: async (page) => {
        drawer?.close();
        document.getElementById("homePage").hidden = page !== "home";
        if(!workspace)document.getElementById("testPage").hidden = page !== "test";
        for(const key of ["hub","coach","history"])document.getElementById(`${key}Page`).hidden=page!==key;
        workspace?.setPage(page);
        for (const link of document.querySelectorAll("[data-page]")) {
            if (link.dataset.page === page) link.setAttribute("aria-current", "page");
            else link.removeAttribute("aria-current");
        }
        navigationStatus.textContent = "";
        window.scrollTo?.({top: 0, behavior: "instant"});
        if (page !== "home") await initializeTrainer();
    },
});
void navigation.start().catch(console.error);
