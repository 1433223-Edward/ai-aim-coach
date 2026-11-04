import {bindSettings} from "./settings.js";
import {createNavigation} from "./navigation.js";
import {bindTrainingUI} from "./training_ui.js";
import {bindRecommendationUI} from "./recommendation_ui.js";

let trainer = null;
let trainerPromise = null;
let drawer = null;
bindSettings(() => !!trainer && trainer.canLeaveTrainingPage());
const navigationStatus = document.getElementById("navigationStatus");
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
        document.getElementById("testPage").hidden = page !== "test";
        for (const link of document.querySelectorAll("[data-page]")) {
            if (link.dataset.page === page) link.setAttribute("aria-current", "page");
            else link.removeAttribute("aria-current");
        }
        navigationStatus.textContent = "";
        window.scrollTo?.({top: 0, behavior: "instant"});
        if (page === "test") await initializeTrainer();
    },
});
void navigation.start().catch(console.error);
