/** A pod's connectors, and the typed refusals they answer with.
 *
 *  A pod declares the connectors it works through, each addressed by a slug
 *  (`gmail`, `support-inbox`). The connector, not the caller, decides whose
 *  account a call runs as: a PER_MEMBER one uses the acting person's own, a
 *  SHARED one the account its owner shared with the pod. So nothing here picks
 *  an account any more. A schedule, a surface or a workflow names a slug, and
 *  when the connector cannot run for this person the server says why with a
 *  code and, where the person can fix it, a `connect_url`.
 *
 *  Pure, for the reason `accounts.ts` is: every field here is a wire field.
 */

export type PodConnectorMode = "PER_MEMBER" | "SHARED";
export type PodConnectorStatus = "READY" | "NEEDS_ACCOUNT" | "UNAVAILABLE";

export interface PodConnector {
    slug: string;
    /** The catalog connector behind it — `slack`, `github`. Empty when the
     *  caller may not read the installation. */
    connectorId: string;
    mode: PodConnectorMode;
    status: PodConnectorStatus;
    /** What the pod uses it for, in its editor's words. */
    purpose: string;
    /** For a SHARED connector: whose account it runs as, by name or email. */
    sharedAccount: string;
}

const MODES: PodConnectorMode[] = ["PER_MEMBER", "SHARED"];
const STATUSES: PodConnectorStatus[] = ["READY", "NEEDS_ACCOUNT", "UNAVAILABLE"];

function record(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

export function readPodConnector(raw: unknown): PodConnector | null {
    const row = record(raw);
    const slug = text(row["slug"]);
    if (!slug) return null;
    const mode = text(row["mode"]).toUpperCase() as PodConnectorMode;
    const status = text(row["status"]).toUpperCase() as PodConnectorStatus;
    const shared = record(row["shared_account"]);
    return {
        slug,
        connectorId: text(row["connector_id"]),
        mode: MODES.includes(mode) ? mode : "PER_MEMBER",
        /* A status this client does not know is not one to offer as ready. */
        status: STATUSES.includes(status) ? status : "UNAVAILABLE",
        purpose: text(row["purpose"]),
        sharedAccount: text(shared["display_name"]) || text(shared["email"]),
    };
}

export function readPodConnectors(raw: unknown): PodConnector[] {
    const items = Array.isArray(raw) ? raw : record(raw)["items"];
    return (Array.isArray(items) ? items : []).map(readPodConnector).filter((one): one is PodConnector => one !== null);
}

/** The pod's connectors for one catalog connector, the ready ones first. A
 *  surface runs as one account for everyone it answers, so it only ever takes
 *  a SHARED connector; pass `mode` to say so. */
export function connectorsFor(list: PodConnector[], connectorId: string, mode?: PodConnectorMode): PodConnector[] {
    return list
        .filter((one) => (!connectorId || one.connectorId === connectorId) && (!mode || one.mode === mode))
        .sort((a, b) => Number(b.status === "READY") - Number(a.status === "READY"));
}

/** Where a person connects, shares or reconnects an account for this pod's
 *  connectors — opened on one connector when there is one to name. It is the
 *  address the server's own `connect_url` points at. */
export function connectionsPath(podId: string, slug?: string): string {
    return "/pods/" + encodeURIComponent(podId) + "/connections" + (slug ? "?connector=" + encodeURIComponent(slug) : "");
}

/** A slug for a new connector, free in this pod: `base` made to read the same
 *  in a URL, a YAML key and a tool argument (the server's own pattern), then
 *  `-2`, `-3` while taken — what the server does when it shares a bot it made. */
export function freshSlug(base: string, taken: string[]): string {
    const root = base.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^[-_]+|-+$/g, "").slice(0, 56) || "account";
    const used = new Set(taken);
    let slug = root;
    for (let n = 2; used.has(slug); n += 1) slug = root + "-" + n;
    return slug;
}

/** A connector's state, said for a person. Empty when it is ready. */
export function statusNote(connector: Pick<PodConnector, "status">): string {
    if (connector.status === "UNAVAILABLE") return "Turned off for this organization";
    if (connector.status === "NEEDS_ACCOUNT") return "No account shared yet";
    return "";
}

/** One line naming the connector in a picker: its slug, and for a shared one
 *  whose account it runs as. */
export function connectorLabel(connector: PodConnector): string {
    const whose = connector.mode === "SHARED"
        ? (connector.sharedAccount ? " — runs as " + connector.sharedAccount : "")
        : " — each person's own account";
    const note = statusNote(connector);
    return connector.slug + whose + (note ? " (" + note.toLowerCase() + ")" : "");
}

/* ── the typed refusals ─────────────────────────────────────────────── */

/** Every code a pod connector refuses with. Each but the not-found carries the
 *  slug in `details.pod_connector`; CONNECTION_REQUIRED and REAUTH_REQUIRED
 *  carry a `connect_url` when the person reading can fix it. */
export const CONNECTION_CODES = [
    "CONNECTION_REQUIRED",
    "REAUTH_REQUIRED",
    "CONNECTOR_NEEDS_ACCOUNT",
    "CONNECTOR_UNAVAILABLE",
    "CONNECTION_REQUIRES_PERSON",
    "POD_CONNECTOR_NOT_FOUND",
] as const;

export type ConnectionCode = (typeof CONNECTION_CODES)[number];

export interface ConnectionProblem {
    code: ConnectionCode;
    message: string;
    /** Where to fix it, or empty when the person reading cannot. */
    connectUrl: string;
    /** The slug it was about, when the server named one. */
    podConnector: string;
}

/** The connector refusal inside a failed call, or null for any other failure.
 *  Read off what the SDK's errors carry: `code`, `message`, and the response's
 *  `details`. */
export function connectionProblem(error: unknown): ConnectionProblem | null {
    if (!error || typeof error !== "object") return null;
    const said = error as { code?: unknown; message?: unknown; details?: unknown };
    const code = text(said.code) as ConnectionCode;
    if (!CONNECTION_CODES.includes(code)) return null;
    const details = record(said.details);
    return {
        code,
        message: text(said.message),
        connectUrl: text(details["connect_url"]),
        podConnector: text(details["pod_connector"]),
    };
}

/** What the connect link says: reconnecting an expired sign-in is a different
 *  act from connecting an account for the first time. */
export function connectLinkLabel(code: string): string {
    if (code === "REAUTH_REQUIRED") return "Reconnect";
    // Nobody's account is shared yet: the fix is someone sharing one, which
    // need not be the person reading this.
    if (code === "CONNECTOR_NEEDS_ACCOUNT") return "Open connections";
    return "Connect your account";
}

/** A failed call's sentence, and the link that fixes it when there is one. */
export function failureOf(error: unknown, fallback: string): { message: string; connectUrl: string; code: string } {
    const typed = connectionProblem(error);
    if (typed) return { message: typed.message || fallback, connectUrl: typed.connectUrl, code: typed.code };
    const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
    return { message: message || fallback, connectUrl: "", code: "" };
}
