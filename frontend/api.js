// Shared transport owns cancellation and response parsing, never experiment or UI state.
export async function readResponse(response) {
    let body;
    try { body = await response.json(); }
    catch {
        const failure = new Error("服务器返回了无法解析的响应。");
        failure.retryable = response.status >= 500;
        throw failure;
    }
    if (!response.ok) {
        const detail = body.detail;
        const message = typeof detail === "string" ? detail : (detail?.message || "请求校验失败。");
        const error = new Error(message + (detail?.request_id ? ` (Request ${detail.request_id})` : ""));
        error.status = response.status;
        error.code = detail?.code;
        error.retryable = response.status >= 500;
        throw error;
    }
    return body;
}

export async function requestJson(url, options = {}, timeoutMs = 15000) {
    const controller = new AbortController();
    const external = options.signal;
    let timer;
    let cancel;
    const deadline = new Promise((resolve, reject) => {
        cancel = () => {
            controller.abort();
            const error = new Error("请求已取消，未确认的数据仍保留。");
            error.cancelled = true;
            reject(error);
        };
        if (external?.aborted) cancel();
        else external?.addEventListener("abort", cancel, {once: true});
        timer = setTimeout(() => {
            controller.abort();
            const error = new Error("请求超时；服务器可能已保存，重试将使用相同 Session ID。");
            error.retryable = true;
            reject(error);
        }, timeoutMs);
    });
    try {
        // Include body parsing in the deadline, not only receipt of headers.
        return await Promise.race([
            fetch(url, {...options, signal: controller.signal}).then(readResponse), deadline,
        ]);
    } catch (error) {
        if (error instanceof TypeError || error?.name === "TypeError") error.retryable = true;
        throw error;
    } finally {
        clearTimeout(timer);
        external?.removeEventListener("abort", cancel);
    }
}
