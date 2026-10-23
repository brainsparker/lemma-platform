import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppNotGrantedError, ApiError, apiErrorFromStatus } from "lemma-sdk";
import {
    APP_NOT_GRANTED_MESSAGE, GRANT_KINDS, conferralRefusal, covers, grantFor, isViewerShort, readAppGrants, readMissingGrant,
    readMissingGrantMessage, sameGrants, sayMissingGrant, sayPermission, withGrant, type Grant,
} from "../src/data/app-grants.ts";
import { liveSource } from "../src/data/live.ts";
import { disconnect } from "../src/session/client.ts";

/** A pod app acts as its viewer ∩ its own grants. Its Grants panel lists and
 *  edits them; a call outside them is said over the app as "this app isn't set
 *  up to …", with a one-click fix for an editor — and never confused with the
 *  viewer's own access falling short, which no grant on the app can fix. */

registerHooks({ load(url, context, nextLoad) {
    if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "" };
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

const REFUSED = { resource_type: "datastore_table", resource_name: "deals", permission_ids: ["datastore.record.read"] };

/* ── what the server said ─────────────────────────────────────────────── */

test("an APP_NOT_GRANTED refusal reads as the grant to add, from the SDK's class or the bare code", () => {
    const typed = apiErrorFromStatus(403, "no", "APP_NOT_GRANTED", REFUSED);
    assert.ok(typed instanceof AppNotGrantedError, "the SDK this app builds against throws the typed class");
    assert.deepEqual(readMissingGrant(typed), { resourceType: "datastore_table", resourceName: "deals", permissionIds: ["datastore.record.read"] });
    assert.deepEqual(readMissingGrant({ code: "APP_NOT_GRANTED", details: { resource_type: "pod", permission_ids: ["agent.execute"] } }),
        { resourceType: "pod", resourceName: "pod", permissionIds: ["agent.execute"] }, "the pod is always named `pod`");
    assert.equal(readMissingGrant(new ApiError(403, "no", "FORBIDDEN", REFUSED)), null);
    assert.equal(readMissingGrant({ code: "APP_NOT_GRANTED", details: {} }), null, "nothing named, nothing to offer");
});

test("the viewer's own refusal is not the app's: no grant is offered for it", () => {
    const viewer = apiErrorFromStatus(403, "no", "DELEGATION_EXCEEDS_INVOKER", REFUSED);
    assert.equal(isViewerShort(viewer), true);
    assert.equal(readMissingGrant(viewer), null);
    assert.equal(isViewerShort(apiErrorFromStatus(403, "no", "APP_NOT_GRANTED", REFUSED)), false);
});

test("it says what the app is not set up to do, in words, on what", () => {
    assert.equal(sayMissingGrant({ resourceType: "datastore_table", resourceName: "deals", permissionIds: ["datastore.record.read"] }),
        "This app isn’t set up to read rows on table “deals”.");
    assert.equal(sayMissingGrant({ resourceType: "pod", resourceName: "pod", permissionIds: ["agent.execute", "datastore.table.read"] }),
        "This app isn’t set up to talk to agents and list tables on the pod.");
    assert.equal(sayMissingGrant({ resourceType: "connector", resourceName: "gmail", permissionIds: ["connector.use"] }),
        "This app isn’t set up to use on connector “gmail”.");
    assert.equal(sayPermission("function.execute"), "run");
    assert.equal(sayPermission("function.execute", "pod"), "run functions");
});

test("a frame's message is heard only when it is the SDK's, and read the same way", () => {
    assert.deepEqual(readMissingGrantMessage({ type: APP_NOT_GRANTED_MESSAGE, ...REFUSED, message: "x" }),
        { resourceType: "datastore_table", resourceName: "deals", permissionIds: ["datastore.record.read"] });
    assert.equal(readMissingGrantMessage({ type: "lemma-compose", ...REFUSED }), null);
    assert.equal(readMissingGrantMessage("lemma-app-not-granted"), null);
});

test("a conferral refusal names what the saver does not hold; anything else is not one", () => {
    const refused = apiErrorFromStatus(403, "You may not grant…", "CONFERRAL_EXCEEDS_HOLDER", { permission_ids: ["datastore.record.write"] });
    assert.deepEqual(conferralRefusal(refused), ["datastore.record.write"]);
    assert.equal(conferralRefusal(apiErrorFromStatus(403, "no", "FORBIDDEN")), null);
});

/* ── the grants themselves ────────────────────────────────────────────── */

test("the pod is a grant target, with only what a workload may be granted on it", () => {
    const pod = GRANT_KINDS.find((kind) => kind.type === "pod");
    assert.ok(pod);
    assert.equal(pod.placeholder, null, "one pod, named `pod`");
    assert.ok(pod.permissions.includes("agent.execute"));
    assert.ok(!pod.permissions.some((id) => id.endsWith(".delete") || id === "pod.update"), "never administering it");
});

test("adding merges onto the same resource and keeps every other grant as it was", () => {
    const saved: Grant[] = [
        { resource: "datastore_table", name: "deals", permissions: ["datastore.table.read"] },
        { resource: "connector", name: "gmail", permissions: ["connector.use"] },
    ];
    const missing = { resourceType: "datastore_table", resourceName: "deals", permissionIds: ["datastore.record.read"] };
    assert.equal(covers(saved, missing), false);
    const next = withGrant(saved, grantFor(missing));
    assert.deepEqual(next, [
        { resource: "datastore_table", name: "deals", permissions: ["datastore.record.read", "datastore.table.read"] },
        { resource: "connector", name: "gmail", permissions: ["connector.use"] },
    ]);
    assert.equal(covers(next, missing), true);
    const onPod = withGrant(next, { resource: "pod", name: "anything", permissions: ["agent.execute"] });
    assert.deepEqual(onPod.at(-1), { resource: "pod", name: "pod", permissions: ["agent.execute"] }, "the pod's name is not the builder's to type");
    assert.equal(withGrant(next, { resource: "folder", name: " ", permissions: ["folder.read"] }), next, "no name, nothing added");
    assert.equal(sameGrants(next, [...next].reverse()), true);
    assert.equal(sameGrants(next, saved), false);
});

test("the live source reads and replaces an app's grants on its own routes", async (context) => {
    const original = process.env.NEXT_PUBLIC_API_URL;
    process.env.NEXT_PUBLIC_API_URL = "https://app-grants.example.invalid";
    disconnect();
    const requests: [string, string, unknown][] = [];
    context.mock.method(globalThis, "fetch", async (input: string | URL | Request, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        const raw = typeof init?.body === "string" ? init.body : input instanceof Request ? await input.text() : "";
        requests.push([method, url.pathname, raw ? JSON.parse(raw) : null]);
        const grants = method === "PUT" ? (JSON.parse(raw) as { grants: unknown[] }).grants : [{ resource_type: "pod", resource_name: "pod", permission_ids: ["agent.execute"] }];
        return new Response(JSON.stringify({ app_id: "a1", app_name: "deals-board", grants }), { status: 200, headers: { "content-type": "application/json" } });
    });
    try {
        assert.deepEqual(await liveSource.appGrants("p1", "deals-board"), [{ resource: "pod", name: "pod", permissions: ["agent.execute"] }]);
        const saved = await liveSource.setAppGrants("p1", "deals-board", [{ resource: "datastore_table", name: "deals", permissions: ["datastore.record.read"] }]);
        assert.deepEqual(saved, [{ resource: "datastore_table", name: "deals", permissions: ["datastore.record.read"] }]);
        assert.deepEqual(requests, [
            ["GET", "/pods/p1/apps/deals-board/permissions", null],
            ["PUT", "/pods/p1/apps/deals-board/permissions", { grants: [{ resource_type: "datastore_table", resource_name: "deals", permission_ids: ["datastore.record.read"] }] }],
        ]);
        assert.deepEqual(readAppGrants({ grants: [{ resource_type: "FOLDER", resource_name: "/docs", permission_ids: ["folder.read", 3] }, { resource_type: 1 }] }),
            [{ resource: "folder", name: "/docs", permissions: ["folder.read"] }]);
    } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
});

/* ── drawn ────────────────────────────────────────────────────────────── */

const opened: { close(): void }[] = [];
after(() => { for (const one of opened) one.close(); });

function dom() {
    const window = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true, url: "https://app.example.invalid/t/p1" }).window;
    for (const [key, value] of Object.entries({
        window, document: window.document, navigator: window.navigator, HTMLElement: window.HTMLElement, Node: window.Node,
        IS_REACT_ACT_ENVIRONMENT: true, localStorage: window.localStorage,
    })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    window.HTMLElement.prototype.scrollIntoView = () => {};
    opened.push(window);
    return window;
}

const SAVED: Grant[] = [{ resource: "datastore_table", name: "deals", permissions: ["datastore.table.read"] }];

function seeded(actions: string[]) {
    const cache = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false }, mutations: { retry: false } } });
    cache.setQueryData(["app-grants", "p1", "deals-board"], SAVED);
    cache.setQueryData(["pod-actions", "p1"], { actions });
    opened.push({ close: () => cache.clear() });
    return cache;
}

async function draw(window: ReturnType<typeof dom>, element: ReturnType<typeof createElement>, cache: QueryClient) {
    const { createRoot } = await import("react-dom/client");
    const holder = window.document.createElement("div");
    window.document.body.append(holder);
    const root = createRoot(holder);
    await act(async () => { root.render(createElement(QueryClientProvider, { client: cache }, element)); });
    return { holder, root };
}

const click = async (window: ReturnType<typeof dom>, button: Element | undefined) => {
    assert.ok(button, "the button is there");
    await act(async () => { button.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); await new Promise((done) => setTimeout(done, 10)); });
};

const PANEL = { podId: "p1", podName: "Kit", appName: "deals-board", appLabel: "Deals board" };

test("the panel lists an app's grants to everyone, and offers changes only to an editor", async () => {
    const window = dom();
    const { AppGrantsPanel } = await import("../src/stage/app-grants.tsx");
    const member = await draw(window, createElement(AppGrantsPanel, PANEL), seeded(["pod.read", "app.read"]));
    assert.match(member.holder.textContent ?? "", /table “deals”/);
    assert.match(member.holder.textContent ?? "", /Only an editor of Kit can change what Deals board may do/);
    assert.equal(member.holder.querySelector(".appgrants__add"), null);
    assert.ok(![...member.holder.querySelectorAll("button")].some((one) => one.textContent === "Remove"));
    await act(async () => member.root.unmount());

    const editor = await draw(window, createElement(AppGrantsPanel, PANEL), seeded(["pod.read", "app.read", "app.update"]));
    assert.ok(editor.holder.querySelector(".appgrants__add"), "an editor gets the form");
    const kinds = [...editor.holder.querySelectorAll(".appgrants__fields select option")].map((one) => one.getAttribute("value"));
    assert.ok(kinds.includes("pod"), "the pod is one of the kinds");
    const save = [...editor.holder.querySelectorAll("button")].find((one) => one.textContent === "Save grants");
    assert.equal(save?.hasAttribute("disabled"), true, "nothing changed, nothing to save");
    await act(async () => editor.root.unmount());
});

test("\"Add this grant\" lands in the draft marked new, and a grant the saver lacks is refused in plain words", async (context) => {
    const window = dom();
    const { AppGrantsPanel } = await import("../src/stage/app-grants.tsx");
    /* The wire is the live-source test's; here, what the panel sends and how
       it says the refusal. */
    const puts: Grant[][] = [];
    context.mock.method(liveSource, "setAppGrants", async (_pod: string, _app: string, grants: Grant[]) => {
        puts.push(grants);
        throw apiErrorFromStatus(403, "You may not grant an app permissions you could not use: datastore.record.read on datastore_table.",
            "CONFERRAL_EXCEEDS_HOLDER", { permission_ids: ["datastore.record.read"] });
    });
    const prefill = { resourceType: "datastore_table", resourceName: "deals", permissionIds: ["datastore.record.read"] };
    const { holder, root } = await draw(window, createElement(AppGrantsPanel, { ...PANEL, prefill }), seeded(["app.update"]));
    await act(async () => { await new Promise((done) => setTimeout(done, 10)); });
    const row = holder.querySelector(".appgrants__list li[data-new]");
    assert.ok(row, "the refused grant is in the draft, marked");
    assert.match(row.textContent ?? "", /read rows/);
    await click(window, [...holder.querySelectorAll("button")].find((one) => one.textContent === "Save grants"));
    await act(async () => { for (let tries = 0; tries < 50 && !holder.querySelector(".appgrants__problem"); tries += 1) await new Promise((done) => setTimeout(done, 20)); });
    assert.deepEqual(puts, [[{ resource: "datastore_table", name: "deals", permissions: ["datastore.record.read", "datastore.table.read"] }]],
        "the whole set goes, the saved grant with the new permission merged in");
    const problem = holder.querySelector(".appgrants__problem")?.textContent ?? "";
    assert.match(problem, /You can’t grant what you don’t have/);
    assert.match(problem, /datastore\.record\.read/);
    await act(async () => root.unmount());
});

test("the notice over the app says what it isn't set up to do; only an editor gets the fix", async () => {
    const window = dom();
    const { AppNotGrantedNotice } = await import("../src/stage/app-grants.tsx");
    const missing = { resourceType: "pod", resourceName: "pod", permissionIds: ["agent.execute"] };
    const props = { ...PANEL, missing, onDismiss: () => {} };
    const member = await draw(window, createElement(AppNotGrantedNotice, props), seeded(["pod.read"]));
    assert.match(member.holder.textContent ?? "", /This app isn’t set up to talk to agents on the pod\./);
    assert.match(member.holder.textContent ?? "", /An editor of Kit can add it/);
    assert.ok(![...member.holder.querySelectorAll("button")].some((one) => one.textContent === "Add this grant"));
    assert.ok(!/access/i.test(member.holder.textContent ?? ""), "never \"you don't have access\"");
    await act(async () => member.root.unmount());

    const editor = await draw(window, createElement(AppNotGrantedNotice, props), seeded(["app.update"]));
    await click(window, [...editor.holder.querySelectorAll("button")].find((one) => one.textContent === "Add this grant"));
    const dialog = window.document.body.querySelector("[role=dialog]");
    assert.ok(dialog, "the Grants panel opens");
    assert.match(dialog.textContent ?? "", /What Deals board may do/);
    assert.ok(dialog.querySelector(".appgrants__list li[data-new]"), "with the grant already in the draft");
    await act(async () => editor.root.unmount());
});
