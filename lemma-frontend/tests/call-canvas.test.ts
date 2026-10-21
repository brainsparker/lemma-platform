import test from "node:test";
import assert from "node:assert/strict";
import { canvasDeck, canvasItemFrom } from "../src/call/canvas.ts";
import { callSocketUrl } from "../src/call/call-socket.ts";
import { parseDisplayResource } from "../src/thread/display-resource.ts";

const frame = (id: string, args: Record<string, unknown>, result: unknown = { success: true }) => ({
    conversation_id: "child-conversation",
    tool_call_id: id,
    tool_name: "show_on_canvas",
    args,
    result,
});

test("a drawn canvas item is a widget served by the tool call that drew it", () => {
    const item = canvasItemFrom(frame("t1", { kind: "diagram", title: "Plan", caption: "the three phases", mermaid: "flowchart LR; A-->B" }));
    assert.ok(item);
    assert.equal(item.id, "t1");
    assert.equal(item.conversationId, "child-conversation");
    assert.deepEqual(item.resource, { type: "WIDGET", name: "Plan", caption: "the three phases", drawn: true });
    assert.equal(item.label, "Plan");
});

test("a pod file goes on the canvas as itself and a refused item does not at all", () => {
    assert.deepEqual(canvasItemFrom(frame("t2", { kind: "file", title: "Brochure", path: "/me/brochure.pdf" }))?.resource, {
        type: "FILE", path: "/me/brochure.pdf", name: "Brochure", caption: undefined,
    });
    assert.equal(canvasItemFrom(frame("t3", { kind: "chart", title: "x" }, { success: false, error: "needs rows" })), null);
    assert.equal(canvasItemFrom(frame("t4", { kind: "sculpture", title: "x" })), null);
});

test("display_resource from anyone in the call lands on the canvas too", () => {
    const item = canvasItemFrom({ ...frame("t5", { type: "TABLE", name: "venues" }), tool_name: "display_resource" });
    assert.deepEqual(item?.resource.type, "TABLE");
});

test("a redrawn item takes the place of the one it replaces, even down a chain", () => {
    const a = canvasItemFrom(frame("a", { kind: "note", title: "v1", markdown: "x" }))!;
    const b = canvasItemFrom(frame("b", { kind: "chart", title: "other", chart: {} }))!;
    const a2 = canvasItemFrom(frame("a2", { kind: "note", title: "v2", markdown: "y", replaces: "a" }))!;
    const a3 = canvasItemFrom(frame("a3", { kind: "note", title: "v3", markdown: "z", replaces: "a2" }))!;
    assert.deepEqual(canvasDeck([a, b, a2, a3]).map((item) => item.id), ["a3", "b"]);
});

test("the transcript renders a canvas item from its stored tool call", () => {
    assert.deepEqual(parseDisplayResource({ kind: "image", title: "Beach", url: "https://example.com/b.jpg" }, { success: true }), {
        type: "WIDGET", name: "Beach", caption: undefined, drawn: true,
    });
});

test("the call socket rides the API origin, with the token when there is one", () => {
    assert.equal(callSocketUrl("https://api.example.test/", "p 1", "c1", "tok"), "wss://api.example.test/pods/p%201/calls/c1/socket?access_token=tok");
    assert.equal(callSocketUrl("http://localhost:8710", "p", "c", null), "ws://localhost:8710/pods/p/calls/c/socket");
});
