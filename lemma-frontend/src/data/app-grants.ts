import type { AgentGrant } from "./agents";

/** What a pod app may do for whoever views it, and what to say when it may not.
 *
 *  An app's page acts as its viewer, intersected with the app's own grants —
 *  the same resource grants an agent or a function holds, so the same shape
 *  (`AgentGrant`) and the same body (`grantsBody`) carry them. Kept apart from
 *  the panel so the reading of the server's refusals is tested, not trusted. */

export type Grant = AgentGrant;

/** Changing an app's grants is asked of the app as `app.update`
 *  (`PUT /pods/{pod}/apps/{app}/permissions`). Held by role, so the pod's own
 *  `permissions/me` answers for every app in it. */
export const APP_EDIT = "app.update";

/** The code an app's call is refused with outside its grants. */
export const APP_NOT_GRANTED = "APP_NOT_GRANTED";
/** The viewer themselves lacks it: no grant on the app can fix that. */
export const DELEGATION_EXCEEDS_INVOKER = "DELEGATION_EXCEEDS_INVOKER";
/** The person setting a grant does not hold it themselves. */
export const CONFERRAL_EXCEEDS_HOLDER = "CONFERRAL_EXCEEDS_HOLDER";

/** The wire name the SDK posts to the page framing an app (lemma-typescript
 *  `LEMMA_APP_NOT_GRANTED_MESSAGE_TYPE`). */
export const APP_NOT_GRANTED_MESSAGE = "lemma-app-not-granted";

export interface GrantKind {
    type: string;
    label: string;
    /** What the name field holds; null where there is only one (the pod). */
    placeholder: string | null;
    /** The permissions this kind of resource takes, as the server checks them
     *  (`_applicable_permission_ids`). Anything else is refused there. */
    permissions: string[];
}

/** The resource types an app's grant can name, in the order a builder reaches
 *  for them. `pod` is the pod itself: listing its tables, talking to its
 *  agents, running its functions — never administering it. */
export const GRANT_KINDS: GrantKind[] = [
    {
        type: "datastore_table", label: "Table", placeholder: "deals",
        permissions: ["datastore.table.read", "datastore.record.read", "datastore.record.write", "datastore.record.delete", "datastore.table.update", "datastore.table.delete"],
    },
    { type: "folder", label: "Folder", placeholder: "/docs", permissions: ["folder.read", "folder.write", "folder.delete"] },
    { type: "document", label: "File", placeholder: "/docs/brief.md", permissions: ["folder.read", "folder.write", "folder.delete"] },
    { type: "connector", label: "Connector", placeholder: "gmail", permissions: ["connector.use"] },
    { type: "agent", label: "Agent", placeholder: "researcher", permissions: ["agent.read", "agent.execute", "agent.update", "agent.delete"] },
    { type: "function", label: "Function", placeholder: "score-lead", permissions: ["function.read", "function.execute", "function.update", "function.delete"] },
    { type: "workflow", label: "Workflow", placeholder: "intake", permissions: ["workflow.read", "workflow.execute", "workflow.update", "workflow.delete"] },
    { type: "schedule", label: "Schedule", placeholder: "nightly", permissions: ["schedule.read", "schedule.update", "schedule.delete"] },
    { type: "app", label: "App", placeholder: "dashboard", permissions: ["app.read", "app.update", "app.publish", "app.delete"] },
    {
        type: "pod", label: "The pod itself", placeholder: null,
        permissions: [
            "pod.read", "datastore.table.read", "conversation.read", "conversation.write", "agent.read", "agent.execute",
            "function.read", "function.execute", "workflow.read", "workflow.execute", "app.read", "channel.read", "channel.write", "contact.read",
        ],
    },
];

/** The name a grant on the pod itself goes by. */
export const POD_RESOURCE_NAME = "pod";

export function grantKind(type: string): GrantKind | null {
    return GRANT_KINDS.find((kind) => kind.type === type.toLowerCase()) ?? null;
}

/** `datastore_table` → "table"; an unknown type, its own words. */
export function sayResourceType(type: string): string {
    const kind = grantKind(type);
    if (kind?.type === "pod") return "the pod";
    return (kind?.label ?? type.replace(/_/g, " ")).toLowerCase();
}

/* Asked of the pod, a permission reaches everything of its kind in it. */
const ON_THE_POD: Record<string, string> = {
    "pod.read": "see the pod",
    "datastore.table.read": "list tables",
    "conversation.read": "read conversations",
    "conversation.write": "start conversations",
    "agent.read": "see agents",
    "agent.execute": "talk to agents",
    "function.read": "see functions",
    "function.execute": "run functions",
    "workflow.read": "see workflows",
    "workflow.execute": "run workflows",
    "app.read": "see apps",
    "channel.read": "read channels",
    "channel.write": "post in channels",
    "contact.read": "read contacts",
};

const ON_A_RESOURCE: Record<string, string> = {
    "datastore.record.read": "read rows",
    "datastore.record.write": "write rows",
    "datastore.record.delete": "delete rows",
    "datastore.table.update": "change the table",
    "datastore.table.delete": "delete the table",
};

const VERBS: Record<string, string> = { execute: "run", update: "change" };

/** `datastore.record.read` → "read rows"; on the pod, `agent.execute` →
 *  "talk to agents". */
export function sayPermission(id: string, resourceType = ""): string {
    const key = id.toLowerCase();
    if (resourceType.toLowerCase() === "pod" && ON_THE_POD[key]) return ON_THE_POD[key];
    if (ON_A_RESOURCE[key]) return ON_A_RESOURCE[key];
    const verb = (key.split(".").pop() ?? key).replace(/_/g, " ");
    return VERBS[verb] ?? verb;
}

/** "read and write rows" style: each permission said, joined. */
export function sayPermissions(ids: string[], resourceType = ""): string {
    const words = [...new Set(ids.map((id) => sayPermission(id, resourceType)))];
    if (words.length === 0) return "do nothing";
    return words.length === 1 ? words[0] : words.slice(0, -1).join(", ") + " and " + words[words.length - 1];
}

/** "table “deals”", "the pod", "connector “gmail”". */
export function sayResource(type: string, name: string | null): string {
    if (type.toLowerCase() === "pod") return "the pod";
    return sayResourceType(type) + (name ? " “" + name + "”" : "");
}

/** One refusal for want of a grant, as the server named it. */
export interface MissingGrant {
    resourceType: string;
    resourceName: string | null;
    permissionIds: string[];
}

function codeOf(error: unknown): string | null {
    if (!error || typeof error !== "object") return null;
    const code = (error as { code?: unknown }).code;
    return typeof code === "string" ? code : null;
}

function detailsOf(error: unknown): Record<string, unknown> {
    if (!error || typeof error !== "object") return {};
    const details = (error as { details?: unknown }).details;
    return details && typeof details === "object" ? details as Record<string, unknown> : {};
}

function strings(value: unknown): string[] {
    return Array.isArray(value) ? value.filter((one): one is string => typeof one === "string" && one.length > 0) : [];
}

function missingFrom(fields: Record<string, unknown>): MissingGrant | null {
    const type = typeof fields.resource_type === "string" ? fields.resource_type : "";
    if (!type) return null;
    const name = typeof fields.resource_name === "string" && fields.resource_name ? fields.resource_name : null;
    return { resourceType: type, resourceName: type === "pod" ? POD_RESOURCE_NAME : name, permissionIds: strings(fields.permission_ids) };
}

/** The grant an app was refused for, from the SDK's `AppNotGrantedError` or
 *  any error carrying the code; null for every other failure. */
export function readMissingGrant(error: unknown): MissingGrant | null {
    if (codeOf(error) !== APP_NOT_GRANTED) return null;
    return missingFrom(detailsOf(error));
}

/** What a framed app posted about a refusal; null when it is anything else. */
export function readMissingGrantMessage(data: unknown): MissingGrant | null {
    if (!data || typeof data !== "object") return null;
    const fields = data as Record<string, unknown>;
    if (fields.type !== APP_NOT_GRANTED_MESSAGE) return null;
    return missingFrom(fields);
}

/** The viewer's own access fell short. Not the app's, and no grant helps. */
export function isViewerShort(error: unknown): boolean {
    return codeOf(error) === DELEGATION_EXCEEDS_INVOKER;
}

/** The permissions a save was refused for because the saver does not hold
 *  them; null when the refusal was anything else. */
export function conferralRefusal(error: unknown): string[] | null {
    if (codeOf(error) !== CONFERRAL_EXCEEDS_HOLDER) return null;
    return strings(detailsOf(error).permission_ids);
}

/** "This app isn't set up to read records on table “deals”." */
export function sayMissingGrant(missing: MissingGrant): string {
    const what = missing.permissionIds.length ? sayPermissions(missing.permissionIds, missing.resourceType) : "do that";
    return "This app isn’t set up to " + what + " on " + sayResource(missing.resourceType, missing.resourceName) + ".";
}

function sameResource(a: Grant, type: string, name: string): boolean {
    return a.resource.toLowerCase() === type.toLowerCase() && a.name === name;
}

/** `grants` with `add` merged in: onto the grant on the same resource when
 *  there is one, as a new grant when there is not. The server replaces the
 *  whole set, so every other grant goes back exactly as it was. */
export function withGrant(grants: Grant[], add: { resource: string; name: string; permissions: string[] }): Grant[] {
    const type = add.resource.toLowerCase();
    const name = type === "pod" ? POD_RESOURCE_NAME : add.name.trim();
    if (!name || add.permissions.length === 0) return grants;
    const existing = grants.find((grant) => sameResource(grant, type, name));
    if (!existing) return [...grants, { resource: type, name, permissions: [...new Set(add.permissions)].sort() }];
    return grants.map((grant) => grant === existing
        ? { ...grant, permissions: [...new Set([...grant.permissions, ...add.permissions])].sort() }
        : grant);
}

/** The missing grant, as one to add. */
export function grantFor(missing: MissingGrant): Grant {
    return { resource: missing.resourceType.toLowerCase(), name: missing.resourceName ?? "", permissions: missing.permissionIds };
}

/** Whether `grants` already allow everything `missing` asks for. */
export function covers(grants: Grant[], missing: MissingGrant): boolean {
    const name = missing.resourceType === "pod" ? POD_RESOURCE_NAME : missing.resourceName ?? "";
    const grant = grants.find((one) => sameResource(one, missing.resourceType, name));
    return Boolean(grant) && missing.permissionIds.every((id) => grant!.permissions.includes(id));
}

/** Two sets of grants that would save the same. */
export function sameGrants(a: Grant[], b: Grant[]): boolean {
    const key = (grants: Grant[]) => grants
        .filter((grant) => grant.permissions.length > 0)
        .map((grant) => grant.resource.toLowerCase() + "\u0000" + grant.name + "\u0000" + [...grant.permissions].sort().join(","))
        .sort()
        .join("\n");
    return key(a) === key(b);
}

/** `AppPermissionsResponse` → grants. */
export function readAppGrants(response: unknown): Grant[] {
    const grants = (response as { grants?: unknown } | null)?.grants;
    if (!Array.isArray(grants)) return [];
    return grants.flatMap((raw) => {
        const grant = raw as { resource_type?: unknown; resource_name?: unknown; permission_ids?: unknown };
        if (typeof grant.resource_type !== "string" || typeof grant.resource_name !== "string") return [];
        return [{ resource: grant.resource_type.toLowerCase(), name: grant.resource_name, permissions: strings(grant.permission_ids) }];
    });
}
