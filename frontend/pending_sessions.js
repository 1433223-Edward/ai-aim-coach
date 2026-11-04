// Each immutable payload uses its own key so independent tabs do not overwrite a shared array.
export class PendingSessionQueue {
    constructor(storage) {
        this.storage = storage;
        this.memory = new Map();
        this.prefix = "aimcoachPending:";
        this.memoryOnly = false;
    }

    put(session) {
        const record = JSON.stringify({version: 1, session});
        this.memory.set(session.sessionId, record);
        try {
            const existing = this.storage.getItem(this.prefix + session.sessionId);
            if (existing && existing !== record) throw new Error("Pending ID has conflicting content.");
            this.storage.setItem(this.prefix + session.sessionId, record);
            return true;
        } catch (error) {
            // Keep the new payload in memory and preserve any existing durable record.
            this.memoryOnly = true;
            return false;
        }
    }

    rawEntries() {
        const entries = new Map(this.memory);
        try {
            for (let index = 0; index < this.storage.length; index++) {
                const key = this.storage.key(index);
                if (key && key.startsWith(this.prefix)) {
                    const id = key.slice(this.prefix.length);
                    const raw = this.storage.getItem(key);
                    // Export both versions if a corrupt or conflicting entry shares an ID.
                    if (entries.has(id) && entries.get(id) !== raw) entries.set(id + ":memory", entries.get(id));
                    entries.set(id, raw);
                }
            }
        } catch (error) {
            this.memoryOnly = this.memory.size > 0;
        }
        return entries;
    }

    list() {
        const sessions = [];
        const errors = [];
        for (const [id, raw] of this.rawEntries()) {
            try {
                const record = JSON.parse(raw);
                const session = record.session;
                if (record.version !== 1 || !session || session.sessionId !== id ||
                    typeof session.experimentId !== "string" || !Array.isArray(session.attempts)) {
                    throw new Error("Invalid pending record.");
                }
                sessions.push(session);
            } catch (error) {
                // Never delete or silently repair a record we cannot interpret safely.
                errors.push(id);
            }
        }
        sessions.sort((a, b) => a.experimentOrder - b.experimentOrder);
        return {sessions, errors};
    }

    acknowledge(id) {
        try {
            if (this.storage) this.storage.removeItem(this.prefix + id);
        } catch (error) {
            // Leave the entry retryable if durable cleanup fails after server acknowledgement.
            return false;
        }
        this.memory.delete(id);
        this.memoryOnly = this.memory.size > 0;
        return true;
    }
}

