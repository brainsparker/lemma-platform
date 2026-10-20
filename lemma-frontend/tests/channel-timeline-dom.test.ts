import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/** The channel timeline, its thread panel, a shared file and the composer,
 *  drawn in a page: opened at a line it goes there once and lets go; a long
 *  thread is read on; an image drawn again is never a revoked one; a draft
 *  keeps who was picked; files pasted go up in the line's own folder. */

type Api = Record<string, (...args: never[]) => unknown>;
const api: Api = {};
(globalThis as { __channelApi?: unknown }).__channelApi = api;

registerHooks({ load(url, context, nextLoad) {
    if (url.endsWith("/src/channels/api.ts")) {
        return { format: "module", shortCircuit: true, source: "export const channelApi = () => globalThis.__channelApi;" };
    }
    if (url.endsWith("/src/session/use-me.ts")) {
        return { format: "module", shortCircuit: true, source: 'export const useMe = () => "me";' };
    }
    if (url.endsWith("/src/thread/markdown.tsx")) {
        return { format: "module", shortCircuit: true, source: 'import { createElement } from "react"; export const Prose = ({ text }) => createElement("p", null, text);' };
    }
    if (url.endsWith("/src/channels/waiting-panel.tsx")) {
        return { format: "module", shortCircuit: true, source: "export const WaitingRow = () => null; export const useInbox = () => ({ data: undefined });" };
    }
    if (url.endsWith("/src/shell/address.ts")) {
        return { format: "module", shortCircuit: true, source: 'export const NOWHERE = {}; export const writeAddress = (a) => "/t/" + a.podId + "/" + a.tabId;' };
    }
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

async function page(options: { touch?: boolean } = {}) {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
    const scrolled: string[] = [];
    dom.window.HTMLElement.prototype.scrollIntoView = function (this: HTMLElement) { scrolled.push(this.id); };
    dom.window.matchMedia = ((query: string) => ({ matches: Boolean(options.touch), media: query, addEventListener() {}, removeEventListener() {} })) as never;
    const urls = { made: [] as string[], revoked: [] as string[] };
    const saved = new Map<string, PropertyDescriptor | undefined>();
    for (const [key, value] of Object.entries({
        window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, CSS: { escape: (value: string) => value },
        requestAnimationFrame: (run: () => void) => setTimeout(run, 0),
        IS_REACT_ACT_ENVIRONMENT: true,
    })) {
        saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    const createObjectURL = URL.createObjectURL;
    const revokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = () => { const url = "blob:" + (urls.made.length + 1); urls.made.push(url); return url; };
    URL.revokeObjectURL = (url: string) => { urls.revoked.push(url); };
    const { createRoot } = await import("react-dom/client");
    const root = createRoot(dom.window.document.getElementById("root")!);
    const cache = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const draw = async (element: unknown) => {
        await act(async () => { root.render(createElement(QueryClientProvider, { client: cache }, element as never)); });
    };
    const flush = async () => { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)); }); };
    const close = async () => {
        await act(async () => { root.unmount(); });
        cache.clear();
        URL.createObjectURL = createObjectURL;
        URL.revokeObjectURL = revokeObjectURL;
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete (globalThis as Record<string, unknown>)[key];
        }
        dom.window.close();
    };
    return { dom, root, cache, draw, flush, close, scrolled, urls, body: () => dom.window.document.body.textContent ?? "" };
}

const channel = {
    id: "c1", kind: "GROUP", title: "launch", topic: null, person: null, visibility: "PUBLIC", sources: [], assignee: null,
    agentsAnswer: true, handBackAfterHours: 12, status: null, attention: null, archived: false, joined: true, lastLine: null,
    lastMessageAt: null, lastSeq: 8, createdAt: "", unread: { lines: 0, mentions: 0, waiting: 0, readSeq: 8 },
    places: [], members: [], me: { joined: true, notify: "ALL", notifyVia: null, mutedUntil: null, readSeq: 8, role: "owner", canManage: true },
};
const priya = { kind: "MEMBER", id: "user-priya", name: "Priya" };
const line = (seq: number, extra: Record<string, unknown> = {}) => ({
    id: "l" + seq, seq, sender: priya, onBehalfOf: null, text: "line " + seq, createdAt: "2026-10-19T10:00:00Z", editedAt: null,
    deleted: false, withheld: false, membersOnly: false, sourceId: null, threadRootId: null, replyCount: 0, interaction: null,
    parts: [], clientMessageId: null, delivered: null, ...extra,
});

function timelineProps(lines: unknown[], extra: Record<string, unknown> = {}) {
    return {
        podId: "pod", channel, lines, loading: false, failed: false, hasOlder: false, loadingOlder: false,
        onOlder: () => {}, onRetry: () => {}, pin: 0, visible: true, mayWrite: true, answering: [],
        agentName: () => "Lem", highlight: null, announced: "", onProgress: () => {}, ...extra,
    };
}

test("opened at a line, the timeline goes there once -- new lines never pull the reader back to it", async () => {
    const { Timeline } = await import("../src/channels/timeline.tsx");
    const view = await page();
    try {
        await view.draw(createElement(Timeline, timelineProps([line(1), line(2), line(3)], { highlight: "l2" }) as never));
        assert.deepEqual(view.scrolled, ["line-l2"]);
        await view.draw(createElement(Timeline, timelineProps([line(1), line(2), line(3), line(4)], { highlight: "l2" }) as never));
        await view.draw(createElement(Timeline, timelineProps([line(1), line(2), line(3), line(4), line(5)], { highlight: "l2" }) as never));
        assert.deepEqual(view.scrolled, ["line-l2"], "scrolled to once, not on every new line");
    } finally {
        await view.close();
    }
});

test("a reader who scrolls before the line asked for has loaded is not yanked to it when it arrives", async () => {
    const { Timeline } = await import("../src/channels/timeline.tsx");
    const view = await page();
    try {
        await view.draw(createElement(Timeline, timelineProps([line(5), line(6)], { highlight: "l2", hasOlder: true }) as never));
        const box = view.dom.window.document.querySelector(".ctimeline")!;
        await act(async () => { box.dispatchEvent(new view.dom.window.WheelEvent("wheel", { bubbles: true, deltaY: -40 })); });
        await view.draw(createElement(Timeline, timelineProps([line(1), line(2), line(5), line(6)], { highlight: "l2" }) as never));
        assert.deepEqual(view.scrolled, [], "the reader moved first; the highlight let go");
    } finally {
        await view.close();
    }
});

test("where the unread lines began is marked, before the first line from somebody else past the read position", async () => {
    const { Timeline } = await import("../src/channels/timeline.tsx");
    const view = await page();
    try {
        const mine = { kind: "MEMBER", id: "me", name: "Dana" };
        await view.draw(createElement(Timeline, timelineProps([line(7), line(8), line(9, { sender: mine }), line(10)], { unreadFrom: 8, me: "me" }) as never));
        const items = [...view.dom.window.document.querySelectorAll(".cday__lines > li")].map((one) => one.id || one.className);
        assert.deepEqual(items, ["line-l7", "line-l8", "line-l9", "ctimeline__unread", "line-l10"], "the reader's own line is not news");
        await view.draw(createElement(Timeline, timelineProps([line(7), line(8)], { unreadFrom: 8, me: "me" }) as never));
        assert.equal(view.dom.window.document.querySelector(".ctimeline__unread"), null, "nothing unread, no mark");
    } finally {
        await view.close();
    }
});

test("a streaming answer is not a live region: only its start and its line are announced", async () => {
    const { LineView } = await import("../src/channels/line.tsx");
    const view = await page();
    try {
        await view.draw(createElement("ol", null, createElement(LineView, {
            podId: "pod", channel: channel as never, line: line(4) as never,
            answering: [{ messageId: "l4", agentId: "lem", text: "Thursday, after" }], agentName: () => "Lem",
        })));
        const answer = view.dom.window.document.querySelector(".cline__answering")!;
        assert.ok(answer.textContent?.includes("Thursday, after"));
        assert.equal(answer.closest("[role=status], [aria-live]"), null, "no live region around the words being written");
    } finally {
        await view.close();
    }
});

test("a thread past its first page is read on, oldest first, after its last reply", async () => {
    const { ThreadPanel } = await import("../src/channels/thread-panel.tsx");
    const view = await page();
    const asked: (number | undefined)[] = [];
    api.thread = (async (_channelId: string, _rootId: string, afterSeq?: number) => {
        asked.push(afterSeq);
        return afterSeq === undefined
            ? { items: [line(11, { threadRootId: "l1" }), line(12, { threadRootId: "l1" })], nextAfter: 12 }
            : { items: [line(13, { threadRootId: "l1" })], nextAfter: null };
    }) as never;
    try {
        await view.draw(createElement(ThreadPanel, {
            podId: "pod", channel: channel as never, root: line(1, { replyCount: 3 }) as never, people: [], visible: true, mayWrite: true,
            writeRefused: null, answering: [], agentName: () => "Lem", highlight: null, onTyping: () => {}, onClose: () => {},
        }));
        await view.flush();
        const more = [...view.dom.window.document.querySelectorAll("button")].find((button) => button.textContent === "Show later replies");
        assert.ok(more, "a thread with more replies than a page offers the rest");
        await act(async () => { more!.click(); });
        await view.flush();
        const shown = [...view.dom.window.document.querySelectorAll(".chthread__body .cline")].map((one) => one.id);
        assert.deepEqual(shown, ["line-l1", "line-l11", "line-l12", "line-l13"]);
        assert.deepEqual(asked, [undefined, 12]);
        assert.equal([...view.dom.window.document.querySelectorAll("button")].some((button) => button.textContent === "Show later replies"), false);
        assert.match(view.body(), /3 replies/);
    } finally {
        delete api.thread;
        await view.close();
    }
});

test("an image drawn again shows a URL of its own, never the one revoked when it was last taken down", async () => {
    const { FileChip } = await import("../src/channels/parts.tsx");
    const view = await page();
    let reads = 0;
    api.file = (async () => { reads += 1; return new Blob(["png"], { type: "image/png" }); }) as never;
    const chip = createElement(FileChip, { podId: "pod", channelId: "c1", lineId: "l1", file: { type: "file", path: "/me/a.png", name: "a.png", mimeType: "image/png", size: 3, fileId: "f1" } });
    try {
        await view.draw(chip);
        await view.flush();
        const first = view.dom.window.document.querySelector("img")!.getAttribute("src");
        assert.equal(first, "blob:1");
        await view.draw(createElement("p", null, "elsewhere"));
        assert.deepEqual(view.urls.revoked, ["blob:1"], "let go when it is taken down");
        await view.draw(chip);
        await view.flush();
        const again = view.dom.window.document.querySelector("img")!.getAttribute("src");
        assert.equal(again, "blob:2", "drawn again from the cached bytes, with a URL of its own");
        assert.ok(!view.urls.revoked.includes(again!));
        assert.equal(reads, 1, "the bytes are read once");
    } finally {
        delete api.file;
        await view.close();
    }
});

async function typeInto(view: Awaited<ReturnType<typeof page>>, text: string, caret = text.length) {
    const box = view.dom.window.document.querySelector("textarea")!;
    await act(async () => {
        Object.getOwnPropertyDescriptor(view.dom.window.HTMLTextAreaElement.prototype, "value")!.set!.call(box, text);
        box.setSelectionRange(caret, caret);
        box.dispatchEvent(new view.dom.window.Event("input", { bubbles: true }));
    });
}

test("a draft keeps who was picked: come back to it and the @name still names them", async () => {
    const { Composer } = await import("../src/channels/composer.tsx");
    const view = await page();
    const posts: { mentions?: unknown[] }[] = [];
    api.post = (async (_channelId: string, body: { mentions?: unknown[]; text: string }) => { posts.push(body); return line(20, { text: body.text }); }) as never;
    const sam = { kind: "MEMBER", id: "user-sam", name: "Sam" };
    const composer = (key: string) => createElement(Composer, { key, podId: "pod", channel: channel as never, people: [sam] as never, threadRootId: "root-draft", placeholder: "Reply", onPosted: () => {} });
    try {
        await view.draw(composer("a"));
        await typeInto(view, "@Sa");
        const option = view.dom.window.document.querySelector("[role=option]")!;
        await act(async () => { option.dispatchEvent(new view.dom.window.MouseEvent("mousedown", { bubbles: true })); });
        await view.flush();
        /* Away and back: a new composer, reading the draft. */
        await view.draw(createElement("p", null, "elsewhere"));
        await view.draw(composer("b"));
        const box = view.dom.window.document.querySelector("textarea")!;
        assert.equal(box.value, "@Sam ");
        assert.match(view.body(), /@Sam/, "the chip is back with the words");
        await typeInto(view, "@Sam can you look");
        await act(async () => { view.dom.window.document.querySelector("form")!.requestSubmit(); });
        await view.flush();
        assert.deepEqual(posts[0].mentions, [{ kind: "MEMBER", id: "user-sam" }]);
    } finally {
        delete api.post;
        await view.close();
    }
});

test("files pasted in go up into the line's own folder, two of one name kept apart, saying how they are getting on", async () => {
    const { Composer } = await import("../src/channels/composer.tsx");
    const view = await page();
    const attached: { name: string; clientMessageId: string }[] = [];
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const posts: { clientMessageId?: string | null; files?: unknown[] }[] = [];
    api.attach = (async (_channelId: string, file: File, at: { clientMessageId: string; name: string }) => {
        attached.push(at);
        if (attached.length === 1) await gate;
        return { type: "file", path: "/me/channels/c1/" + at.clientMessageId + "/" + at.name, name: at.name, mimeType: file.type, size: file.size, fileId: null };
    }) as never;
    api.post = (async (_channelId: string, body: { clientMessageId?: string | null; files?: unknown[] }) => { posts.push(body); return line(21); }) as never;
    try {
        await view.draw(createElement(Composer, { podId: "pod", channel: channel as never, people: [], placeholder: "Write", onPosted: () => {} }));
        const box = view.dom.window.document.querySelector("textarea")!;
        const paste = new view.dom.window.Event("paste", { bubbles: true, cancelable: true });
        const shot = () => new view.dom.window.File(["x"], "image.png", { type: "image/png" });
        Object.defineProperty(paste, "clipboardData", { value: { files: [shot(), shot()] } });
        await act(async () => { box.dispatchEvent(paste); });
        assert.equal(paste.defaultPrevented, true);
        assert.equal(view.dom.window.document.querySelectorAll(".chchip--file").length, 2);
        await act(async () => { view.dom.window.document.querySelector("form")!.requestSubmit(); });
        await view.flush();
        assert.match(view.body(), /Uploading 1 of 2…/);
        assert.match(view.body(), /image\.png — uploading…/);
        await act(async () => { release(); });
        await view.flush();
        assert.deepEqual(attached.map((one) => one.name), ["image.png", "image (2).png"]);
        assert.equal(attached[0].clientMessageId, attached[1].clientMessageId);
        assert.equal(posts[0].clientMessageId, attached[0].clientMessageId, "the folder is the line's own");
        assert.equal(posts[0].files?.length, 2);
    } finally {
        delete api.attach;
        delete api.post;
        await view.close();
    }
});

test("on a touch screen Enter breaks the line and the button sends; elsewhere Enter sends", async () => {
    const { Composer } = await import("../src/channels/composer.tsx");
    for (const touch of [true, false]) {
        const view = await page({ touch });
        const posts: unknown[] = [];
        api.post = (async (_channelId: string, body: unknown) => { posts.push(body); return line(22); }) as never;
        try {
            await view.draw(createElement(Composer, { key: String(touch), podId: "pod", channel: channel as never, people: [], threadRootId: "enter-" + touch, placeholder: "Write", onPosted: () => {} }));
            await typeInto(view, "first line");
            const box = view.dom.window.document.querySelector("textarea")!;
            const enter = new view.dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
            await act(async () => { box.dispatchEvent(enter); });
            await view.flush();
            assert.equal(posts.length, touch ? 0 : 1, touch ? "Enter is a new line on touch" : "Enter sends");
            assert.equal(enter.defaultPrevented, !touch);
        } finally {
            delete api.post;
            await view.close();
        }
    }
});
