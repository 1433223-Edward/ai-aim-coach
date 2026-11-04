const {test} = require("node:test");
const assert = require("node:assert/strict");
let PendingSessionQueue;
require("node:test").before(async () => { ({PendingSessionQueue} = await import("../frontend/pending_sessions.js")); });

function storage() {
    const data = new Map();
    return {data, getItem: (key) => data.get(key) || null, setItem: (key, value) => data.set(key, value),
            removeItem: (key) => data.delete(key), key: (index) => [...data.keys()][index],
            get length() { return data.size; }};
}
const session = (id, order = 1) => ({sessionId: id, experimentId: "experiment", experimentOrder: order, attempts: []});

test("per-session keys survive reload and independent tab writes", () => {
    const disk = storage();
    const first = new PendingSessionQueue(disk);
    const second = new PendingSessionQueue(disk);
    assert.equal(first.put(session("one")), true);
    assert.equal(second.put(session("two", 2)), true);
    assert.equal(new PendingSessionQueue(disk).list().sessions.length, 2);
    assert.equal(first.acknowledge("one"), true);
    assert.deepEqual(second.list().sessions.map((s) => s.sessionId), ["two"]);
});

test("corrupt and conflicting records remain exportable instead of being deleted", () => {
    const disk = storage();
    disk.setItem("aimcoachPending:broken", "{invalid");
    const queue = new PendingSessionQueue(disk);
    assert.deepEqual(queue.list().errors, ["broken"]);
    assert.equal(queue.rawEntries().get("broken"), "{invalid");
    assert.equal(queue.put(session("one")), true);
    assert.equal(queue.put({...session("one"), attempts: ["different"]}), false);
    assert.ok(queue.list().errors.includes("one:memory"));
    assert.equal(queue.rawEntries().size, 3);
});

test("unavailable storage retains memory evidence and warns before closing", () => {
    const queue = new PendingSessionQueue(null);
    assert.equal(queue.put(session("one")), false);
    assert.equal(queue.memoryOnly, true);
    assert.equal(queue.list().sessions.length, 1);
    assert.equal(queue.acknowledge("one"), true);
    assert.equal(queue.list().sessions.length, 0);
});

test("failed durable cleanup remains idempotently retryable", () => {
    const disk = storage();
    const queue = new PendingSessionQueue(disk);
    queue.put(session("one"));
    disk.removeItem = () => { throw new Error("blocked"); };
    assert.equal(queue.acknowledge("one"), false);
    assert.equal(queue.list().sessions.length, 1);
});
