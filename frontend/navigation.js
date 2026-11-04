// Hash routing works with the existing static server, including direct reloads.
export function createNavigation({window, showPage, canNavigate, onBlocked}) {
    let current = null;
    let transitioning = false;
    const readPage = () => window.location.hash === "#/test" ? "test" : "home";
    async function route() {
        const requested = readPage();
        if (current === requested) return;
        // Never hide the trainer during initialization or an active Pointer Lock lifecycle.
        if (transitioning || (current === "test" && !canNavigate())) {
            window.history.replaceState(null, "", `#/${current || "home"}`);
            onBlocked();
            return;
        }
        transitioning = true;
        current = requested;
        try {
            await showPage(requested);
        } catch (error) {
            // The shell already selected this page; keep its URL while showing a recovery message.
            window.history.replaceState(null, "", `#/${current}`);
            throw error;
        } finally {
            transitioning = false;
        }
    }
    const onHash = () => { void route().catch(onBlocked); };
    window.addEventListener("hashchange", onHash);
    return {start: route, destroy: () => window.removeEventListener("hashchange", onHash)};
}
