import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { JSDOM } from "jsdom";

import type { CaptureResult, PostHog } from "posthog-js";

import { analyticsOptions } from "../src/site/analytics/client.ts";

/** The export boundary, held against the real SDK.
 *
 *  `/privacy` says a page address leaves this browser as its route pattern and
 *  that the names of things people make reach analytics only as identifiers.
 *  posthog-js decides most of what an event carries, and it attaches the
 *  address under keys of its own choosing: `$session_entry_url` on every event,
 *  `$prev_pageview_pathname` and `title` on pageviews. A test against a
 *  hand-written event only proves the keys somebody thought of. This one starts
 *  posthog-js with the options the app starts it with, walks it through a
 *  visit whose every address, query and title is private, and reads what comes
 *  out of our `before_send` chain — the last thing before the network.
 *
 *  Nothing is sent: a recorder at the end of the chain keeps each event and
 *  returns null, and the network is stubbed besides. */

const POD_IN_ADDRESS = "pod-in-the-address";
const ENTRY =
    "https://lemma.work/s/contact/ada-lovelace?n=Ada%20Lovelace&em=ada%40example.test&wa=%2B15551234567";
const REFERRER = "https://mail.example.test/inbox/thread-42?q=quarterly+layoffs";

/** Every string that must never leave. The referrer's host is not here: another
 *  site's origin is where somebody came from, and is kept on purpose. */
const PRIVATE = [
    "ada", "Ada", "15551234567", "example.test/inbox", "thread-42", "quarterly",
    POD_IN_ADDRESS, "q3-layoffs", "private",
];

const sent: CaptureResult[] = [];
const restore = new Map<string, PropertyDescriptor | undefined>();
let dom: JSDOM;
let posthog: PostHog;

before(async () => {
    dom = new JSDOM("<!doctype html><title>Ada Lovelace | Lemma</title>", {
        url: ENTRY,
        referrer: REFERRER,
        pretendToBeVisual: true,
    });
    const offline = () => Promise.reject(new Error("offline in tests"));
    dom.window.fetch = offline as typeof fetch;
    for (const [key, value] of Object.entries({
        window: dom.window,
        document: dom.window.document,
        navigator: dom.window.navigator,
        location: dom.window.location,
        localStorage: dom.window.localStorage,
        sessionStorage: dom.window.sessionStorage,
        fetch: offline,
    })) {
        restore.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    /* posthog-js schedules its flushes and session checks on the global timers
       and never stops them, so this file's process would never exit. Every
       timer is unreferenced instead: it still fires while the tests run, and
       holds nothing open once they are done. Not restored afterwards, because a
       timer a late callback sets is exactly the one that would hang the run. */
    for (const name of ["setTimeout", "setInterval"] as const) {
        const original = globalThis[name];
        const unreferenced = (...args: Parameters<typeof original>) => {
            const handle = original(...args);
            handle.unref();
            return handle;
        };
        Object.defineProperty(globalThis, name, { value: unreferenced, configurable: true, writable: true });
    }

    /* posthog-js reads `window` once, when it is first imported, so it is
       imported only now that there is one. Under Node it loads as CommonJS,
       whose `default` is the module object rather than the client. */
    const loaded = (await import("posthog-js")) as unknown as { default: { default?: PostHog } & PostHog };
    posthog = loaded.default.default ?? loaded.default;

    const options = analyticsOptions();
    posthog.init("phc_test_not_a_key", {
        ...options,
        api_host: "https://ingest.invalid",
        advanced_disable_flags: true,
        before_send: [
            ...options.before_send,
            (event) => {
                if (event) sent.push(structuredClone(event));
                return null;
            },
        ],
    });

    posthog.capture("$pageview");
    dom.window.history.pushState(null, "", `/t/${POD_IN_ADDRESS}/file/private/q3-layoffs.md?view=raw`);
    dom.window.document.title = "q3-layoffs | Lemma";
    posthog.capture("$pageview");
    posthog.identify("user-1");
    posthog.group("pod", "pod-1");
    posthog.capture("share_link.viewed", { kind: "contact", viewer_is_member: false });
    posthog.capture("$pageleave");
});

after(() => {
    for (const [key, descriptor] of restore) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else Reflect.deleteProperty(globalThis, key);
    }
    dom.window.close();
});

test("the SDK was really driven: every kind of event came out of the chain", () => {
    const kinds = new Set(sent.map((event) => event.event));
    for (const kind of ["$pageview", "$identify", "$groupidentify", "share_link.viewed", "$pageleave"])
        assert.ok(kinds.has(kind), `${kind} never reached the end of before_send`);
});

test("no address, query, title or name leaves through any property posthog-js sets", () => {
    for (const event of sent) {
        const payload = JSON.stringify(event);
        for (const secret of PRIVATE)
            assert.ok(!payload.includes(secret), `${event.event} carried "${secret}": ${payload}`);
        assert.equal(event.properties.title, undefined, `${event.event} carried the page title`);
    }
});

test("what is kept is still worth having: route templates, and where people came from", () => {
    const [entry, file] = sent.filter((event) => event.event === "$pageview");
    assert.equal(entry.properties.$current_url, "https://lemma.work/s/[kind]/[...path]");
    assert.equal(entry.properties.$session_entry_url, "https://lemma.work/s/[kind]/[...path]");
    assert.equal(entry.properties.$referrer, "https://mail.example.test");
    assert.equal(entry.properties.$referring_domain, "mail.example.test");
    assert.equal(file.properties.$pathname, "/t/[[...podId]]");
    assert.equal(file.properties.$prev_pageview_pathname, "/s/[kind]/[...path]");

    const identify = sent.find((event) => event.event === "$identify");
    assert.equal(identify?.$set_once?.$initial_current_url, "https://lemma.work/s/[kind]/[...path]");
});

test("a visit with no referrer says so, and an unreadable address is dropped", () => {
    const [, scrub] = analyticsOptions().before_send;
    const event = scrub({
        uuid: "u",
        event: "$pageview",
        properties: { $referrer: "$direct", $session_entry_url: "not a url", $pathname: "/t/x/table/payroll" },
    } as CaptureResult);
    assert.equal(event?.properties.$referrer, "$direct");
    assert.equal("$session_entry_url" in (event?.properties ?? {}), false);
    assert.equal(event?.properties.$pathname, "/t/[[...podId]]");
});
