import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
    askedLine, bannerLine, connectionsPlace, connectorChanges, connectorCreateBody, connectorPatchBody, coverageLine, missingForMe, modeBadge, myStatus,
    readAskOutcome, readPodConnectors, slugProblem, waitingLine, type MyAccount,
} from "../src/data/pod-connectors.ts";
import {
    accountPatchBody, impactLines, ownedBy, readAccountUsage, readImpact, readPodAccounts, readShareRoles, shareAsPlan, shareableFor, suggestedSlug,
    usageLine, visibilityWord,
} from "../src/data/pod-accounts.ts";
import { INSTALLATION_MANAGE, readOrgActions } from "../src/data/org-actions.ts";
import { signInScope } from "../src/computer/sign-in.ts";
import { CONNECTOR_USE, connectorGrantSlugs, grantsBody, withConnectorGrants } from "../src/data/agents.ts";
import { connectionLine, connectionTrouble, needsRebind, readConnection, rebindChoices } from "../src/data/surface-settings.ts";
import { readAddress, writeAddress } from "../src/shell/address.ts";
import { legacyAddress } from "../src/site/legacy-address.ts";
import { scopeParams } from "../src/computer/queries.ts";
import { socketUrl } from "../src/computer/live.ts";
import { liveSource } from "../src/data/live.ts";
import { disconnect } from "../src/session/client.ts";

/** A teammate's Connections: the pod's own connectors and whose account each
 *  runs as, my standing on each, my accounts in it, and every place that
 *  used to pick an account by id now picking a connector by slug. */

registerHooks({ load(url, context, nextLoad) {
    /* The page reads who is signed in from the session; here it is "me". */
    if (url.endsWith("/src/session/use-me.ts")) {
        return { format: "module", shortCircuit: true, source: 'export const useMe = () => "me";' };
    }
    if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "" };
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

const CONNECTORS = readPodConnectors({ items: [
    { id: "c1", slug: "support-inbox", connector_id: "gmail", auth_config_id: "ac-gmail", mode: "SHARED", status: "READY", purpose: "The support mailbox",
        shared_pod_account_id: "pa-alice", shared_account: { display_name: "support@acme.com", owner_user_id: "alice", pod_account_id: "pa-alice" } },
    { id: "c2", slug: "gmail", connector_id: "gmail", auth_config_id: "ac-gmail", mode: "PER_MEMBER", status: "READY", required: true, purpose: "Each person's own inbox",
        member_coverage: { eligible: 7, connected: 5, truncated: false, waiting_on: [
            { user_id: "bob", pod_member_id: "m-bob", name: "Bob", email: "bob@acme.com", standing: "MISSING" },
            { user_id: "carol", pod_member_id: "m-carol", name: null, email: "carol@acme.com", standing: "REAUTH_REQUIRED" },
        ] } },
    { id: "c3", slug: "notion", connector_id: "notion", auth_config_id: "ac-notion", mode: "PER_MEMBER", status: "READY", required: true },
    { id: "c4", slug: "teams-ops", connector_id: "teams", auth_config_id: "ac-teams", mode: "SHARED", status: "NEEDS_ACCOUNT" },
    { id: "c5", slug: "github", connector_id: "github", auth_config_id: "ac-github", mode: "PER_MEMBER", status: "UNAVAILABLE", required: true },
] });

const MINE: MyAccount[] = [
    { id: "a-notion", authConfigId: "ac-notion", connectorId: "notion", label: "me@notion", status: "REAUTH_REQUIRED", usable: false, isDefault: true },
    { id: "a-slack", authConfigId: "ac-slack", connectorId: "slack", label: "", status: "CONNECTED", usable: true, isDefault: true },
];

const ACCOUNTS = readPodAccounts({ items: [
    { id: "pa-alice", account_id: "acc-alice", auth_config_id: "ac-gmail", connector_id: "gmail", owner_user_id: "alice", visibility: "POD", is_member_default: true, source: "MANUAL", display_name: "support@acme.com", used_by: ["support-inbox"] },
    { id: "pa-mine", account_id: "acc-mine", auth_config_id: "ac-gmail", connector_id: "gmail", owner_user_id: "me", visibility: "PERSONAL", is_member_default: true, source: "AUTO", email: "me@acme.com", allow_sandbox_use: false },
    { id: "pa-bob", account_id: "acc-bob", auth_config_id: "ac-gmail", connector_id: "gmail", owner_user_id: "bob", visibility: "RESTRICTED", is_member_default: false, source: "MANUAL" },
    { id: "", account_id: "x" },
] });

test("a pod connector reads its installation, its account and whether everyone is asked", () => {
    assert.equal(CONNECTORS.length, 5);
    assert.equal(CONNECTORS[0].authConfigId, "ac-gmail");
    assert.equal(CONNECTORS[0].sharedPodAccountId, "pa-alice");
    assert.equal(CONNECTORS[0].sharedOwnerId, "alice");
    assert.equal(CONNECTORS[1].required, true);
    assert.equal(CONNECTORS[0].visibility, "POD");
});

test("an editor reads who a required connector is waiting on, by name and never by account", () => {
    const gmail = CONNECTORS[1];
    assert.ok(gmail.coverage);
    assert.equal(coverageLine(gmail.coverage), "5/7 connected");
    assert.deepEqual(gmail.coverage.waitingOn.map((one) => [one.userId, one.name, one.standing]),
        [["bob", "Bob", "MISSING"], ["carol", "carol@acme.com", "REAUTH_REQUIRED"]], "a member with no name is said by their email");
    assert.equal(waitingLine(gmail.coverage), "Waiting on Bob, and carol@acme.com to reconnect");
    assert.equal(CONNECTORS[0].coverage, null, "a member, or a connector nobody is asked about, carries none");
    assert.equal(coverageLine({ eligible: 3, connected: 3, waitingOn: [], truncated: false }), "Everyone connected");
});

test("asking members to connect says who was asked and who was asked already today", () => {
    const said = readAskOutcome({ connect_url: "https://app/pods/p1/connections?connector=gmail",
        asked: [{ user_id: "bob", pod_member_id: "m-bob", name: "Bob", standing: "MISSING" }],
        asked_recently: [{ user_id: "carol", pod_member_id: "m-carol", email: "carol@acme.com", standing: "REAUTH_REQUIRED" }] });
    assert.deepEqual(said.asked.map((one) => one.name), ["Bob"]);
    assert.equal(askedLine(said), "Asked Bob. carol@acme.com was asked in the last day.");
    assert.equal(askedLine(readAskOutcome({ asked: [], asked_recently: [] })), "Nobody to ask.");
    // The ledger could not be read: nobody was asked, and the line says so
    // rather than claiming they were asked already.
    const unreached = readAskOutcome({ asked: [], asked_recently: [], not_asked: [{ user_id: "u3", name: "Dan" }] });
    assert.equal(askedLine(unreached), "Couldn’t ask Dan just now — try again shortly.");
});

test("the badge says whose account a call runs as", () => {
    assert.equal(modeBadge(CONNECTORS[1]), "Your account");
    assert.equal(modeBadge(CONNECTORS[0], "Alice"), "Shared · support@acme.com · by Alice");
    assert.equal(modeBadge(CONNECTORS[0]), "Shared · support@acme.com");
    assert.equal(modeBadge(CONNECTORS[3]), "Shared · no account yet");
});

test("my standing on each connector comes from my own accounts on its installation", () => {
    assert.equal(myStatus(CONNECTORS[1], MINE, "me").standing, "connect", "no gmail account of mine");
    assert.equal(myStatus(CONNECTORS[2], MINE, "me").standing, "reconnect", "my notion account needs signing in");
    assert.equal(myStatus(CONNECTORS[0], MINE, "me").standing, "connected", "a shared one runs whoever I am");
    assert.equal(myStatus(CONNECTORS[3], MINE, "me").standing, "waiting");
    assert.equal(myStatus(CONNECTORS[4], MINE, "me").standing, "off");
    const connected = myStatus(CONNECTORS[1], [...MINE, { id: "a-g", authConfigId: "ac-gmail", connectorId: "gmail", label: "me@acme.com", status: "CONNECTED", usable: true, isDefault: false }], "me");
    assert.equal(connected.standing, "connected");
    assert.equal(connected.note, "As me@acme.com");
    const owner = myStatus(CONNECTORS[0], [{ id: "acc-alice", authConfigId: "ac-gmail", connectorId: "gmail", label: "", status: "REAUTH_REQUIRED", usable: false, isDefault: true }], "alice");
    assert.equal(owner.standing, "reconnect", "the shared account's owner is the one asked to reconnect it");
});

test("the banner asks only for required per-member connectors I cannot use", () => {
    const missing = missingForMe(CONNECTORS, MINE, "me");
    assert.deepEqual(missing.map((one) => one.connector.slug), ["gmail", "notion"], "not the turned-off github, not a shared one");
    const names = (id: string) => ({ gmail: "Gmail", notion: "Notion" } as Record<string, string>)[id] ?? "";
    assert.equal(bannerLine(missing, names, "Kit"), "Kit uses Gmail and Notion — connect yours");
    assert.equal(bannerLine(missing.slice(1), names, "Kit"), "Kit uses Notion — sign in again");
    assert.equal(bannerLine([], names, "Kit"), "");
});

test("a slug reads the same in a URL, a YAML key and a tool argument, and is free", () => {
    assert.equal(slugProblem("support-inbox", []), "");
    assert.match(slugProblem("Support Inbox", []), /Lowercase/);
    assert.match(slugProblem("gmail", ["gmail"]), /already has/);
    assert.match(slugProblem("", []), /name/);
});

test("declaring and changing a connector sends only what it means", () => {
    assert.deepEqual(connectorCreateBody({ slug: "gmail", authConfigId: "ac", mode: "PER_MEMBER", sharedPodAccountId: "pa", required: true, purpose: "  inbox " }),
        { slug: "gmail", auth_config_id: "ac", mode: "PER_MEMBER", required: true, purpose: "inbox" }, "no shared account on a per-member one");
    assert.deepEqual(connectorCreateBody({ slug: "s", authConfigId: "ac", mode: "SHARED", sharedPodAccountId: "pa", required: true, purpose: "" }),
        { slug: "s", auth_config_id: "ac", mode: "SHARED", shared_pod_account_id: "pa" }, "nobody is asked to connect a shared one");
    assert.deepEqual(connectorPatchBody({ sharedPodAccountId: "" }), { shared_pod_account_id: null });
    assert.deepEqual(connectorChanges(CONNECTORS[0], { mode: "SHARED", purpose: "The support mailbox", sharedPodAccountId: "pa-alice" }), {}, "nothing touched, nothing sent");
    assert.deepEqual(connectorChanges(CONNECTORS[0], { mode: "PER_MEMBER" }), { mode: "PER_MEMBER", sharedPodAccountId: "" });
});

test("pod accounts read their owner and visibility, and an editor picks only shared ones", () => {
    assert.equal(ACCOUNTS.length, 3, "a row with no id is not one");
    assert.equal(ACCOUNTS[1].label, "me@acme.com");
    assert.equal(ACCOUNTS[1].sandboxes, false);
    assert.equal(ACCOUNTS[0].sandboxes, null, "a server without the setting says nothing about it");
    assert.deepEqual(shareableFor(ACCOUNTS, "ac-gmail").map((one) => one.id), ["pa-alice", "pa-bob"], "never one still personal to its owner");
    assert.deepEqual(ownedBy(ACCOUNTS, "me").map((one) => one.id), ["pa-mine"]);
    assert.equal(visibilityWord("RESTRICTED"), "Shared with some people here");
    assert.deepEqual(accountPatchBody({ visibility: "RESTRICTED", shareWith: [{ type: "POD_MEMBER", id: "m1" }], sandboxes: true }),
        { visibility: "RESTRICTED", share_with: [{ grantee_type: "POD_MEMBER", grantee_id: "m1" }], allow_sandbox_use: true });
});

test("share as <slug> widens first, then points or declares — and never repurposes a per-member one", () => {
    const mine = ACCOUNTS[1];
    assert.deepEqual(shareAsPlan(mine, "team-gmail", CONNECTORS), [
        { kind: "widen", podAccountId: "pa-mine" },
        { kind: "declare", slug: "team-gmail", authConfigId: "ac-gmail", podAccountId: "pa-mine", purpose: "" },
    ]);
    assert.deepEqual(shareAsPlan(mine, "support-inbox", CONNECTORS), [
        { kind: "widen", podAccountId: "pa-mine" },
        { kind: "point", slug: "support-inbox", podAccountId: "pa-mine" },
    ]);
    assert.deepEqual(shareAsPlan(ACCOUNTS[0], "support-inbox", CONNECTORS), [], "already shared and already pointed at");
    assert.match(String(shareAsPlan(mine, "gmail", CONNECTORS)), /each person's own/);
    assert.match(String(shareAsPlan(mine, "teams-ops", CONNECTORS)), /different installation/);
    assert.equal(suggestedSlug(mine, CONNECTORS), "gmail-shared");
});

test("where an account is used, and what removing an installation stops, are said by pod name", () => {
    const usage = readAccountUsage({ account_id: "a", items: [
        { pod_id: "p1", pod_account_id: "pa1", visibility: "POD", source: "MANUAL", is_member_default: true, used_by: ["support-inbox"] },
        { pod_id: "p2", pod_account_id: "pa2", visibility: "PERSONAL", source: "AUTO", is_member_default: true },
    ] });
    const names = (id: string) => ({ p1: "Kit" } as Record<string, string>)[id] ?? "";
    assert.equal(usageLine(usage[0], names), "Kit · shared with everyone · runs support-inbox");
    assert.equal(usageLine(usage[1], names), "A teammate you cannot open · your own calls");
    const impact = readImpact({ auth_config_id: "ac", account_count: 3, pod_account_count: 2,
        pod_connectors: [{ pod_id: "p1", slug: "gmail", mode: "PER_MEMBER" }, { pod_id: "p1", slug: "support-inbox", mode: "SHARED" }] });
    assert.deepEqual(impactLines(impact, names), ["3 connected accounts are removed with it.", "Kit loses gmail, support-inbox."]);
    assert.deepEqual(impactLines(readImpact({}), names), ["Nothing uses it yet. Nobody loses anything."]);
});

test("the impact preview names the surfaces, schedules and subscriptions that stop, by pod where I can open it", () => {
    const impact = readImpact({ auth_config_id: "ac", account_count: 1, pod_account_count: 1,
        pod_connectors: [{ pod_id: "p1", slug: "crm", mode: "SHARED" }, { pod_id: "p9", slug: "crm", mode: "PER_MEMBER" }],
        surface_count: 1, schedule_count: 3, event_subscription_count: 1,
        pods: [{ pod_id: "p1", name: "Kit", pod_connectors: ["crm"], surface_count: 1, schedule_count: 2, event_subscription_count: 1 }] });
    assert.equal(impact.surfaceCount, 1);
    assert.equal(impact.pods[0].name, "Kit");
    assert.deepEqual(impactLines(impact, () => ""), [
        "1 connected account is removed with it.",
        "1 surface stops answering, 3 schedules stop firing and 1 MCP event subscription ends.",
        "Kit loses crm: 1 surface, 2 schedules, 1 subscription.",
        "A teammate you cannot open loses crm.",
    ], "the pod's name comes with the preview; one the reader cannot open stays unnamed");
});

test("I may install as the server says, not as a copy of its role table says", () => {
    assert.deepEqual(readOrgActions({ organization_id: "o1", actions: ["org.read", INSTALLATION_MANAGE, 3] }), ["org.read", INSTALLATION_MANAGE]);
    assert.deepEqual(readOrgActions(null), [], "nothing readable holds nothing");
});

test("a sign-in link opened on its own names the browser the pause is for", () => {
    const pending = { tool_call_id: "t", origin: "https://x.example", reason: "", pod_id: "p1", agent_id: "ag1" };
    assert.deepEqual(signInScope(null, pending), { podId: "p1", agentId: "ag1" }, "standalone: the pause says");
    assert.deepEqual(signInScope({ podId: "p1", agentId: null }, { ...pending, agent_id: null }), { podId: "p1", agentId: null });
    assert.deepEqual(signInScope({ podId: "p1", agentId: "ag1" }, { ...pending, pod_id: null, agent_id: null }), { podId: "p1", agentId: "ag1" }, "an older server: the host's");
    assert.equal(signInScope(null, null), null, "nothing named, nothing asked");
    const url = new URL(socketUrl("https://api.example.invalid", { mode: "control", origin: pending.origin, podId: "p1", agentId: "ag1" }));
    assert.equal(url.searchParams.get("pod_id"), "p1");
    assert.equal(url.searchParams.get("agent"), "ag1");
});

test("an owner can share with some of the pod's roles, not only some members", () => {
    const roles = readShareRoles({ items: [
        { id: "r-admin", name: "POD_ADMIN", is_system: true },
        { id: "r-user", name: "POD_USER", is_system: true },
        { id: "r-qa", name: "QA_REVIEWERS", is_system: false },
        { name: "NO_ID" },
    ] });
    assert.deepEqual(roles.map((one) => [one.id, one.label]), [["r-admin", "Admins"], ["r-user", "Users"], ["r-qa", "Qa reviewers"]]);
    assert.deepEqual(accountPatchBody({ visibility: "RESTRICTED", shareWith: [{ type: "ROLE", id: "r-qa" }, { type: "POD_MEMBER", id: "m2" }] }).share_with,
        [{ grantee_type: "ROLE", grantee_id: "r-qa" }, { grantee_type: "POD_MEMBER", grantee_id: "m2" }]);
});

test("an agent is granted pod connectors by slug, and every other grant goes back untouched", () => {
    const grants = [
        { resource: "DATASTORE_TABLE", name: "campaigns", permissions: ["record.read"] },
        { resource: "connector", name: "old", permissions: [CONNECTOR_USE] },
    ];
    assert.deepEqual(connectorGrantSlugs(grants), ["old"]);
    const next = withConnectorGrants(grants, ["support-inbox", "gmail", "gmail"]);
    assert.deepEqual(next.map((one) => one.name), ["campaigns", "support-inbox", "gmail"]);
    assert.deepEqual(grantsBody(next).grants[0], { resource_type: "datastore_table", resource_name: "campaigns", permission_ids: ["record.read"] });
    assert.deepEqual(grantsBody(next).grants[1], { resource_type: "connector", resource_name: "support-inbox", permission_ids: [CONNECTOR_USE] });
});

test("a surface says which connector it runs as, and offers another shared one when its own runs as nobody", () => {
    const view = readConnection({ pod_connector: "support-inbox", connection: {
        pod_connector: "support-inbox", connector_id: "gmail", display_name: "support@acme.com", status: "MISSING" as never,
        connected_by: { user_id: "alice", name: "Alice", is_you: false, is_pod_member: true },
    } })!;
    assert.equal(connectionLine(view), "Runs as support-inbox · support@acme.com · by Alice");
    const list = [
        { slug: "support-inbox", connectorId: "gmail", mode: "SHARED", status: "NEEDS_ACCOUNT" },
        { slug: "support-2", connectorId: "gmail", mode: "SHARED", status: "READY" },
        { slug: "gmail", connectorId: "gmail", mode: "PER_MEMBER", status: "READY" },
        { slug: "slack", connectorId: "slack", mode: "SHARED", status: "READY" },
    ];
    assert.ok(needsRebind(view, list[0]));
    assert.match(connectionTrouble(view, list[0]), /no account shared/);
    assert.deepEqual(rebindChoices(view, list).map((one) => one.slug), ["support-2"]);
    assert.ok(needsRebind({ ...view, status: "CONNECTED" }, undefined), "a connector that is gone is repaired too");
    assert.ok(!needsRebind({ ...view, status: "CONNECTED" }, { status: "READY" }));
    assert.equal(readConnection({ pod_connector: null, connection: null }), null);
});

test("the server's connect_url lands on the teammate's Connections, opened on the connector", () => {
    assert.equal(legacyAddress(["pods", "p1", "connections"], new URLSearchParams({ connector: "gmail" })), "/t/p1/about/connections?connector=gmail");
    assert.equal(legacyAddress(["pods", "p1", "connections"], new URLSearchParams()), "/t/p1/about/connections");
    assert.equal(legacyAddress(["pod", "p1", "connectors"], new URLSearchParams()), "/t/p1/about/connections");
    assert.equal(connectionsPlace("p 1", "a b"), "/t/p%201/about/connections?connector=a%20b");
    const read = readAddress("/t/p1/about/connections");
    assert.equal(read.section, "connections");
    assert.equal(writeAddress(read), "/t/p1/about/connections");
    assert.equal(readAddress("/t/p1/about/connectors").section, "connections", "an old link to the part still opens it");
});

test("the computer pane names its pod and agent on every workspace call and on the view socket", () => {
    assert.deepEqual(scopeParams({ podId: "p1", agentId: null }), { pod_id: "p1" }, "absent agent is the pod assistant's machine");
    assert.deepEqual(scopeParams({ podId: "p1", agentId: "ag1" }), { pod_id: "p1", agent: "ag1" });
    const url = new URL(socketUrl("https://api.example.invalid", { mode: "control", conversationId: "c1", podId: "p1", agentId: "ag1" }));
    assert.equal(url.protocol, "wss:");
    assert.equal(url.searchParams.get("pod_id"), "p1");
    assert.equal(url.searchParams.get("agent"), "ag1");
    assert.equal(url.searchParams.get("conversation"), "c1");
    const bare = new URL(socketUrl("https://api.example.invalid", { mode: "view", origin: "https://x.example" }));
    assert.equal(bare.searchParams.get("pod_id"), null);
});

/* ── the live source, against a recorded API ───────────────────────── */

async function recorded(
    context: { mock: { method: (target: object, name: string, impl: (...args: never[]) => unknown) => unknown } },
    answer: (path: string, method: string, body: Record<string, unknown> | null) => { status?: number; body: unknown },
) {
    process.env.NEXT_PUBLIC_API_URL = "https://pod-connections.example.invalid";
    disconnect();
    const requests: { path: string; method: string; search: string; body: Record<string, unknown> | null }[] = [];
    context.mock.method(globalThis, "fetch", async (input: string | URL | Request, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        const raw = typeof init?.body === "string" ? init.body : input instanceof Request ? await input.text() : "";
        const body = raw ? JSON.parse(raw) as Record<string, unknown> : null;
        requests.push({ path: url.pathname, method, search: url.search, body });
        const said = answer(url.pathname, method, body);
        return new Response(said.status === 204 ? null : JSON.stringify(said.body), { status: said.status ?? 200, headers: { "content-type": "application/json" } });
    });
    return requests;
}

async function withApi(run: () => Promise<void>) {
    const original = process.env.NEXT_PUBLIC_API_URL;
    try { await run(); } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
}

test("share as <slug> runs its steps against the pod's own routes, in order", (context) => withApi(async () => {
    const requests = await recorded(context, (path, method, body) => {
        if (path.endsWith("/connector-accounts") && method === "GET") return { body: { items: [{ id: "pa-mine", account_id: "acc", auth_config_id: "ac-gmail", connector_id: "gmail", owner_user_id: "me", visibility: "PERSONAL", is_member_default: true, source: "AUTO", created_at: "" }] } };
        if (path.endsWith("/connectors") && method === "GET") return { body: { items: [] } };
        return { body: { ...body, id: "x", pod_id: "p1", slug: "team-gmail", mode: "SHARED", status: "READY", required: false, visibility: "POD", created_at: "", updated_at: "",
            account_id: "acc", auth_config_id: "ac-gmail", owner_user_id: "me", is_member_default: true, source: "AUTO" } };
    });
    assert.equal(await liveSource.shareAccountAs("p1", "pa-mine", "team-gmail", "The team inbox"), "team-gmail");
    assert.deepEqual(requests.filter((one) => one.method !== "GET").map((one) => [one.method, one.path, one.body]), [
        ["PATCH", "/pods/p1/connector-accounts/pa-mine", { visibility: "POD" }],
        ["POST", "/pods/p1/connectors", { slug: "team-gmail", auth_config_id: "ac-gmail", mode: "SHARED", shared_pod_account_id: "pa-mine", purpose: "The team inbox" }],
    ]);
}));

test("editors declare, change and remove connectors; owners share and withdraw accounts", (context) => withApi(async () => {
    const requests = await recorded(context, (path, method, body) => method === "DELETE"
        ? { status: 204, body: null }
        : { body: { ...body, id: "x", pod_id: "p1", slug: "gmail", mode: "PER_MEMBER", status: "READY", required: true, visibility: "POD", created_at: "", updated_at: "",
            account_id: "acc", auth_config_id: "ac", owner_user_id: "me", is_member_default: true, source: "MANUAL" } });
    const made = await liveSource.addPodConnector("p1", { slug: "gmail", authConfigId: "ac", mode: "PER_MEMBER", sharedPodAccountId: "", required: true, purpose: "" });
    assert.equal(made.slug, "gmail");
    await liveSource.changePodConnector("p1", "gmail", { required: false });
    await liveSource.removePodConnector("p1", "gmail");
    await liveSource.changePodAccount("p1", "pa1", { visibility: "RESTRICTED", shareWith: [{ type: "POD_MEMBER", id: "m2" }] });
    await liveSource.removePodAccount("p1", "pa1");
    assert.deepEqual(requests.map((one) => [one.method, one.path, one.body]), [
        ["POST", "/pods/p1/connectors", { slug: "gmail", auth_config_id: "ac", mode: "PER_MEMBER", required: true }],
        ["PATCH", "/pods/p1/connectors/gmail", { required: false }],
        ["DELETE", "/pods/p1/connectors/gmail", null],
        ["PATCH", "/pods/p1/connector-accounts/pa1", { visibility: "RESTRICTED", share_with: [{ grantee_type: "POD_MEMBER", grantee_id: "m2" }] }],
        ["DELETE", "/pods/p1/connector-accounts/pa1", null],
    ]);
}));

test("where-used, the impact preview, my org permissions and agent grants each ask their own route", (context) => withApi(async () => {
    const requests = await recorded(context, (path) => {
        if (path.endsWith("/pods")) return { body: { account_id: "acc", items: [{ pod_id: "p1", pod_account_id: "pa1", visibility: "POD", source: "MANUAL", is_member_default: true, used_by: ["s"] }] } };
        if (path.endsWith("/impact")) return { body: { auth_config_id: "ac", account_count: 1, pod_account_count: 0, pod_connectors: [] } };
        if (path.endsWith("/permissions/me")) return { body: { organization_id: "o1", actions: ["org.read"] } };
        if (path.endsWith("/ask-members")) return { body: { asked: [{ user_id: "bob", pod_member_id: "m-bob", name: "Bob", standing: "MISSING" }], asked_recently: [], connect_url: "u" } };
        if (path.endsWith("/roles")) return { body: { items: [{ id: "r1", name: "POD_EDITOR", is_system: true }] } };
        return { body: { agent_id: "ag", agent_name: "researcher", grants: [{ resource_type: "connector", resource_name: "gmail", permission_ids: [CONNECTOR_USE] }] } };
    });
    assert.equal((await liveSource.accountUsage("acc"))[0].usedBy[0], "s");
    assert.equal((await liveSource.installationImpact("o1", "ac")).accountCount, 1);
    assert.ok(!(await liveSource.orgActions("o1")).includes(INSTALLATION_MANAGE), "a member may not change installations");
    const grants = await liveSource.setAgentGrants("p1", "researcher", withConnectorGrants([], ["gmail"]));
    assert.deepEqual(connectorGrantSlugs(grants), ["gmail"]);
    assert.equal((await liveSource.askMembersToConnect("p1", "gmail")).asked[0].name, "Bob");
    assert.equal((await liveSource.listShareRoles("p1"))[0].label, "Editors");
    assert.deepEqual(requests.map((one) => [one.method, one.path]), [
        ["GET", "/me/connectors/accounts/acc/pods"],
        ["GET", "/organizations/o1/installations/ac/impact"],
        ["GET", "/organizations/o1/permissions/me"],
        ["PUT", "/pods/p1/agents/researcher/permissions"],
        ["POST", "/pods/p1/connectors/gmail/ask-members"],
        ["GET", "/pods/p1/roles"],
    ]);
    assert.deepEqual(requests[3].body, { grants: [{ resource_type: "connector", resource_name: "gmail", permission_ids: [CONNECTOR_USE] }] });
}));

/* ── drawn ─────────────────────────────────────────────────────────── */

/* Every window and cache a drawing made, closed once all have run: an open
   one keeps the process alive past the last test. */
const opened: { close(): void }[] = [];
after(() => { for (const one of opened) one.close(); });

function dom() {
    const window = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true, url: "https://app.example.invalid/t/p1/about/connections?connector=support-inbox" }).window;
    for (const [key, value] of Object.entries({
        window, document: window.document, navigator: window.navigator, HTMLElement: window.HTMLElement, Node: window.Node,
        IS_REACT_ACT_ENVIRONMENT: true, localStorage: window.localStorage,
    })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    window.HTMLElement.prototype.scrollIntoView = () => {};
    opened.push(window);
    return window;
}

const POD = {
    id: "p1", orgId: "o1", name: "Kit", iconUrl: null, teammate: { name: "Kit", initials: "K", iconUrl: null }, subtitle: "", waiting: "",
    members: [
        { id: "m-me", userId: "me", name: "Me", initials: "ME", kind: "person" as const, role: "Editor", can: "" },
        { id: "m-alice", userId: "alice", name: "Alice", initials: "AL", kind: "person" as const, role: "Member", can: "" },
    ],
};

function seeded(actions: string[]) {
    const cache = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
    cache.setQueryData(["pod-connectors", "p1"], CONNECTORS);
    cache.setQueryData(["accounts", "o1"], MINE.map((one) => ({ ...one, ref: "", installState: "READY", createdAt: "" })));
    cache.setQueryData(["connectors"], [
        { id: "gmail", title: "Gmail", description: "", icon: "", active: true, kinds: [] },
        { id: "notion", title: "Notion", description: "", icon: "", active: true, kinds: [] },
    ]);
    cache.setQueryData(["installs", "o1"], [{ id: "ac-gmail", connector_id: "gmail", kind: "composio", name: "gmail", status: "ACTIVE" }]);
    cache.setQueryData(["pod-accounts", "p1"], ACCOUNTS);
    cache.setQueryData(["pod-actions", "p1"], { actions });
    cache.setQueryData(["share-roles", "p1"], [{ id: "r-editor", name: "POD_EDITOR", label: "Editors" }, { id: "r-qa", name: "QA_REVIEWERS", label: "Qa reviewers" }]);
    opened.push({ close: () => cache.clear() });
    return cache;
}

test("the Connections page draws each connector's badge and my standing, and editing only for editors", async () => {
    const window = dom();
    const { PodConnections } = await import("../src/connect/pod-connections.tsx");
    const { createRoot } = await import("react-dom/client");
    const draw = async (actions: string[]) => {
        const holder = window.document.createElement("div");
        window.document.body.append(holder);
        const root = createRoot(holder);
        await act(async () => {
            root.render(createElement(QueryClientProvider, { client: seeded(actions) }, createElement(PodConnections, { pod: POD, focus: "support-inbox" })));
        });
        return { holder, root };
    };
    const editor = await draw(["pod.read", "pod.connector.manage"]);
    const text = editor.holder.textContent ?? "";
    assert.match(text, /Shared · support@acme\.com · by Alice/);
    assert.match(text, /Your account/);
    assert.match(text, /Everyone connects/);
    assert.match(text, /Nobody has shared an account for it yet/);
    const buttons = [...editor.holder.querySelectorAll("button")].map((one) => one.textContent);
    assert.ok(buttons.includes("Connect"), "gmail: no account of mine");
    assert.ok(buttons.includes("Reconnect"), "notion: mine needs signing in");
    assert.ok(buttons.some((one) => one?.includes("Add a connector")));
    assert.ok(buttons.includes("Change"));
    assert.match(text, /5\/7 connected/);
    assert.match(text, /Waiting on Bob, and carol@acme\.com to reconnect/);
    assert.ok(buttons.includes("Ask members to connect"));
    assert.equal(editor.holder.querySelector("[data-focused] b")?.textContent ?? "", "support-inbox", "the linked connector is the one picked out");
    /* My accounts here: only mine, with the owner's acts. */
    assert.match(text, /Your accounts in Kit/);
    assert.match(text, /me@acme\.com/);
    assert.ok(buttons.includes("Share with everyone"));
    assert.ok(buttons.includes("Share as…"));
    assert.ok(!text.includes("acc-bob"));
    assert.match(text, /Allow use inside sandboxes/, "shown because this account's server says it has the setting");
    const some = [...editor.holder.querySelectorAll("button")].find((one) => one.textContent === "Share with some people");
    await act(async () => { some?.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
    const picker = editor.holder.querySelector(".podconn__people")?.textContent ?? "";
    assert.match(picker, /Alice/, "members");
    assert.match(picker, /Editors/, "and the pod's roles");
    assert.match(picker, /Qa reviewers/);
    await act(async () => editor.root.unmount());

    const member = await draw(["pod.read"]);
    const plain = [...member.holder.querySelectorAll("button")].map((one) => one.textContent);
    assert.ok(!plain.some((one) => one?.includes("Add a connector")), "a member cannot add one");
    assert.ok(!plain.includes("Change"));
    assert.ok(!plain.includes("Ask members to connect"));
    assert.ok(plain.includes("Connect"), "but still connects their own");
    await act(async () => member.root.unmount());
});

test("the banner asks for the required connectors I have not connected, and nothing when I have", async () => {
    const window = dom();
    const { ConnectBanner } = await import("../src/connect/pod-connect.tsx");
    const { createRoot } = await import("react-dom/client");
    const holder = window.document.createElement("div");
    window.document.body.append(holder);
    const root = createRoot(holder);
    const cache = seeded([]);
    await act(async () => { root.render(createElement(QueryClientProvider, { client: cache }, createElement(ConnectBanner, { pod: POD }))); });
    assert.match(holder.textContent ?? "", /Kit uses Gmail and Notion — connect yours/);
    assert.deepEqual([...holder.querySelectorAll(".connectbanner__acts button")].map((one) => one.textContent), ["Connect Gmail", "Reconnect Notion"]);
    await act(async () => {
        cache.setQueryData(["pod-connectors", "p1"], CONNECTORS.filter((one) => !one.required));
        /* The cache tells its observers on the next tick. */
        await new Promise((done) => setTimeout(done, 20));
    });
    assert.equal(holder.querySelector(".connectbanner") === null, true, "nothing required is missing, so nothing is asked");
    await act(async () => root.unmount());
});
