import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { live } from "@/usage/queries";
import { useEffect, useRef } from "react";
import { startupProgress } from "./startup";
import { MAX_READ_BYTES, MAX_TEXT_BYTES, viewerFor, type BrowserState, type Listing } from "./machine";
import { wholeFileFrom, type Slice } from "./whole-file";
import { openExternalWhenReady } from "@/desktop/open-external";

/** Whose machine a call is about: one person has one per pod and agent —
 *  the pod assistant's (`agentId` null) or a named agent's — and one with no
 *  pod at all, which is what every route answers when it is not told. Null
 *  while that is still being worked out, which asks nothing: a pane that
 *  guessed would show the assistant's machine for a named agent's chat. */
export interface WorkspaceScope {
    podId: string;
    agentId: string | null;
}

/** The query parameters every `/workspace/*` and `/web-logins` route takes. */
export function scopeParams(scope: WorkspaceScope): { pod_id: string; agent?: string } {
    return { pod_id: scope.podId, ...(scope.agentId ? { agent: scope.agentId } : {}) };
}

/** A query key's part for a scope, so two machines never share a cache. */
function scopeKey(scope: WorkspaceScope | null): string[] {
    return scope ? [scope.podId, scope.agentId ?? ""] : ["", ""];
}

export function useWorkspaceStatus(scope: WorkspaceScope | null, enabled: boolean) {
    const cache = useQueryClient();
    const result = useQuery({
        queryKey: ["computer", "status", ...scopeKey(scope)],
        queryFn: () => lemma().request<Awaited<ReturnType<ReturnType<typeof lemma>["workspace"]["status"]>>>(
            "GET", "/workspace/status", { params: scopeParams(scope!) },
        ),
        enabled: live() && enabled && scope !== null,
        refetchInterval: (query) => startupProgress(query.state.data) ? 3_000 : 15_000,
        refetchOnWindowFocus: true,
        retry: false,
    });
    const previous = useRef<string | undefined>(undefined);
    const state = result.data?.state;
    useEffect(() => {
        if (state === "ready" && previous.current && previous.current !== "ready") {
            void cache.invalidateQueries({ queryKey: ["computer", "files"] });
            void cache.invalidateQueries({ queryKey: ["computer", "browser"] });
        }
        previous.current = state;
    }, [state, cache]);
    return result;
}

/** Reading the computer without starting it.
 *
 *  The installed SDK has no namespace for these, so the paths are written out
 *  once here rather than at each call site — the same arrangement the usage
 *  calls are in, for the same reason.
 *
 *  One rule runs through the whole file and it is a cost rule, not a
 *  correctness one: **looking must not start a machine.** A paused sandbox is
 *  released after fifteen idle minutes, so a pane that woke one on render
 *  would hold compute for as long as somebody left the tab open. The listing
 *  route takes `wake` and defaults it off, answering `sleeping: true` instead;
 *  the browser status route never provisions at all. Everything that does cost
 *  something — reading a file, opening the browser — is behind a click.
 */

/** One directory of your own sandbox, ambient by default.
 *
 *  `path` is nullable, and null is the important case: it means *the server's
 *  own default*, which is the only way to open this pane without first
 *  guessing a root. The reply carries `home_root` and `workspace_root`, so one
 *  request both lists a directory and says where the machine keeps things —
 *  and a client that asks for nothing cannot ask for the wrong thing.
 *
 *  `wake` is a one-way door within a session: once somebody has asked for the
 *  machine to start, asking again with it off would answer `sleeping` about a
 *  machine that is plainly running, so the caller keeps it on.
 *
 *  Paged, because a workspace holding a `node_modules` is the ordinary case
 *  and the route stops at a thousand entries. Unpaged, a directory with more
 *  than that is a dead end: the rest can be counted and never reached.
 */
export function useFiles(scope: WorkspaceScope | null, path: string | null, wake: boolean, enabled = true) {
    return useInfiniteQuery({
        queryKey: ["computer", "files", ...scopeKey(scope), path ?? "", wake],
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => lemma().request<Listing>("GET", "/workspace/files", {
            params: { ...scopeParams(scope!), path: path ?? undefined, wake: wake || undefined, after: pageParam },
        }),
        /* The server's cursor, in the server's order. The view sorts folders
           to the top for reading and must not sort this. */
        getNextPageParam: (page) => page.next_after || undefined,
        enabled: live() && enabled && scope !== null,
        /* An agent writing into this directory changes it under the reader,
           but a listing costs the server a round trip into the sandbox, so
           this refreshes when somebody comes back to it rather than on a
           timer nobody is watching. */
        staleTime: 5_000,
        refetchOnWindowFocus: true,
        retry: false,
    });
}

/** Whether there is a browser to watch. Never starts anything. */
export function useBrowser(scope: WorkspaceScope | null, enabled: boolean) {
    return useQuery({
        queryKey: ["computer", "browser", ...scopeKey(scope)],
        queryFn: async () => {
            const found = await lemma().request<{ state?: string; detail?: string | null }>(
                "GET", "/workspace/browser/status", { params: scopeParams(scope!) },
            );
            return (found?.state ?? "unavailable") as BrowserState;
        },
        enabled: live() && enabled && scope !== null,
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: false,
    });
}

export interface FileBody {
    blob: Blob;
    /** Decoded, or null when this is an image or past the reading ceiling. */
    text: string | null;
    tooLarge: boolean;
    sizeBytes: number;
}

/** One slice of a file, or all of it the server will give at once.
 *
 *  Through `streamResponse` rather than `request`, and that is not a style choice: the
 *  route answers `application/octet-stream`, which `request` hands back as
 *  `response.text()` — a UTF-8 decode that silently replaces every byte a PNG
 *  is made of. The stream gives a real body, so one path serves text, images
 *  and the download alike, and no part of this has to touch a credential to do
 *  it.
 */
async function slice(scope: WorkspaceScope, path: string, options: { length?: number }): Promise<Blob> {
    return (await sliceWithStatus(scope, path, options)).blob;
}

/** The same read, with the status it came back with — which is how a ranged
 *  read knows it got the slice it asked for. See `whole-file.ts`. */
async function sliceWithStatus(scope: WorkspaceScope, path: string, options: { length?: number; range?: { start: number; end: number } }): Promise<Slice> {
    const response = await lemma().streamResponse("/workspace/files:content", {
        method: "GET",
        params: { ...scopeParams(scope), path, length: options.length },
        headers: options.range ? { Range: `bytes=${options.range.start}-${options.range.end}` } : undefined,
    });
    return {
        blob: await response.blob(),
        status: response.status,
        contentRange: response.headers.get("Content-Range"),
    };
}

export function useFileBody(scope: WorkspaceScope | null, path: string | null) {
    const viewer = viewerFor(path?.split("/").pop() ?? "");
    return useQuery({
        queryKey: ["computer", "file", ...scopeKey(scope), path ?? ""],
        queryFn: async (): Promise<FileBody> => {
            if (viewer === "image") {
                /* Exactly the ceiling, not one past it: the route validates
                   `length` against its own maximum and answers 422 for asking
                   over. So a full slice is the signal there may be more, and
                   an image that lands exactly on it is offered rather than
                   drawn — a rare file shown as a download beats a common one
                   shown torn. */
                const whole = await slice(scope!, path!, { length: MAX_READ_BYTES });
                const cut = whole.size >= MAX_READ_BYTES;
                return { blob: whole, text: null, tooLarge: cut, sizeBytes: whole.size };
            }
            const blob = await slice(scope!, path!, { length: MAX_TEXT_BYTES + 1 });
            if (blob.size > MAX_TEXT_BYTES) return { blob, text: null, tooLarge: true, sizeBytes: blob.size };
            return { blob, text: await blob.text(), tooLarge: false, sizeBytes: blob.size };
        },
        enabled: live() && Boolean(path) && scope !== null,
        staleTime: 30_000,
        retry: false,
    });
}

/** One file's own line from the listing, asked for directly.
 *
 *  What this is for is the *size*, and the size matters because the body this
 *  pane holds is capped: a 200 MB log and a 1 MB one both come back as a
 *  megabyte of blob, so the blob cannot say how big the file is. It said so
 *  anyway, and reported every large file as exactly the size of the ceiling.
 *
 *  A stat rather than the listing entry that led here, because the listing is
 *  a snapshot and the interesting files are the ones an agent is still
 *  writing.
 */
export function useFileStat(scope: WorkspaceScope | null, path: string | null) {
    return useQuery({
        queryKey: ["computer", "stat", ...scopeKey(scope), path ?? ""],
        queryFn: () => lemma().request<{ size_bytes: number }>(
            "GET", "/workspace/files:stat", { params: { ...scopeParams(scope!), path: path! } },
        ),
        enabled: live() && Boolean(path) && scope !== null,
        staleTime: 30_000,
        retry: false,
    });
}

export function wholeFile(scope: WorkspaceScope, path: string, sizeBytes = 0): Promise<Blob> {
    return wholeFileFrom(range => sliceWithStatus(scope, path, { range }), sizeBytes);
}

/** A signed, expiring URL onto the sandbox's own browser.
 *
 *  It opens in a tab rather than a frame because the proxy sends
 *  `frame-ancestors` naming the platform's own origins and not this app's — a
 *  leaked signed link must not be drivable from somebody else's page, and this
 *  app is on the wrong side of that line. A refused frame would have looked
 *  like a broken pane.
 *
 *  Unlike everything else here it provisions: asking for the browser is asking
 *  for the machine.
 */
export function useBrowserAccess(scope: WorkspaceScope | null) {
    return useMutation({
        mutationFn: () => lemma().request<{ url: string; expires_at: string }>(
            "POST", "/workspace/apps/browser/access", { body: { ttl_seconds: 1800 }, params: scope ? scopeParams(scope) : undefined },
        ),
    });
}

/** The same browser, in a tab, for where this pane cannot carry it.
 *
 *  A top-level page is not a framed one, so the proxy's `frame-ancestors` has
 *  nothing to say about it — which makes this the one route to the display
 *  that still works when the socket is refused.
 *
 *  The grant takes a round trip and a provision to come back, by which time
 *  the click is long over — so the tab has to be claimed inside the click and
 *  pointed later, which `openExternalWhenReady` does (and does differently in
 *  the desktop app, which refuses a blank window).
 */
export function useOpenBrowserTab(scope: WorkspaceScope | null) {
    const access = useBrowserAccess(scope);
    const open = () => {
        /* Failure is already on `access`, which is what `failed` reads. */
        void openExternalWhenReady(access.mutateAsync().then((grant) => grant.url), "the browser").catch(() => undefined);
    };
    return { open, busy: access.isPending, failed: access.isError };
}

/** Fit the sandbox display to the pane showing it.
 *
 *  The pane is a box of an arbitrary shape and the display is a real screen
 *  with a fixed size, so one of them has to move. Scaling the picture is what
 *  made the browser a small letterboxed rectangle ringed with dead space;
 *  resizing the display means the pixels sent are the pixels shown — and a
 *  narrow pane gets a narrow *viewport*, so a site serves its mobile layout to
 *  somebody signing in on a phone.
 *
 *  A failure here is not a failure for the person: they keep the picture they
 *  had. The route says so itself by answering `{size: null}` rather than a
 *  status code, so there is nothing to catch and nothing to show.
 */
export function useBrowserResize(scope: WorkspaceScope | null) {
    return useMutation({
        mutationFn: (size: { width: number; height: number }) =>
            lemma().request<{ size: string | null }>("POST", "/workspace/browser/display-size", { body: size, params: scope ? scopeParams(scope) : undefined }),
    });
}

/** Where the browser signing in to `origin` actually is.
 *
 *  Polled, because VNC is pixels rather than events: there is nothing on the
 *  wire to react to the way a navigation message would be. It is the only
 *  thing that can answer "which site am I about to type my password into"
 *  while a sign-in redirects through an identity provider and back.
 */
export function useCurrentPage(scope: WorkspaceScope | null, origin: string | null, enabled: boolean) {
    return useQuery({
        queryKey: ["computer", "page", ...scopeKey(scope), origin ?? ""],
        queryFn: async () => {
            const found = await lemma().request<{ url: string | null }>(
                "GET", "/workspace/browser/current-page-url", { params: { ...scopeParams(scope!), origin: origin! } },
            );
            return found?.url ?? null;
        },
        /* Never unscoped: a page asked without its pod is the assistant's
           browser, which is not the one being signed in. */
        enabled: live() && enabled && Boolean(origin) && scope !== null,
        refetchInterval: 1_500,
        staleTime: 0,
        retry: false,
    });
}

/** What a sign-in link is asking for, addressed by the pause it is for.
 *
 *  The conversation and tool call are a lookup, not a credential: the server
 *  resolves both against the caller's own session, so a forwarded link answers
 *  exactly as an invented one does.
 */
export interface PendingSignIn {
    tool_call_id: string;
    origin: string;
    /** What the agent is doing, in its own words, to show the person. */
    reason: string;
    /** Whose browser: the conversation's pod, and its agent when it is a
     *  named one. Absent from a server older than the fields. */
    pod_id?: string | null;
    agent_id?: string | null;
}

export function usePendingSignIn(conversationId: string | null, toolCallId: string | null) {
    return useQuery({
        queryKey: ["computer", "sign-in", conversationId ?? "", toolCallId ?? ""],
        queryFn: () => lemma().request<PendingSignIn>(
            "GET",
            "/web-logins/sign-ins/" + encodeURIComponent(conversationId!) + "/" + encodeURIComponent(toolCallId!),
        ),
        enabled: live() && Boolean(conversationId && toolCallId),
        retry: false,
    });
}

export interface SignInOutcome {
    origin: string;
    signed_in: boolean;
    /** Whether the site stopped asking for a login straight afterwards.
     *  Reported, not enforced — the check is a guess, and the person has
     *  already done what was asked. */
    working: boolean;
}

/** Say whether you signed in, so the waiting run can carry on.
 *
 *  **This is the only route back to the paused run.** Nothing detects a
 *  completed login on its own, so a surface that shows the browser without
 *  this lets somebody sign in and leaves the agent waiting for ever. One call
 *  for both answers because it is one answer, and nothing is stored: the
 *  browser holds the session, so finishing is the person finishing.
 */
export function useAnswerSignIn(conversationId: string | null, toolCallId: string | null) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (signedIn: boolean) => lemma().request<SignInOutcome>(
            "POST",
            "/web-logins/sign-ins/" + encodeURIComponent(conversationId!) + "/"
                + encodeURIComponent(toolCallId!) + "/answer",
            { body: { signed_in: signedIn } },
        ),
        /* The transcript is where the answered card lives, and the list of
           sites has just gained one. Neither refetches on its own. */
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["computer", "logins"] });
            void cache.invalidateQueries({ queryKey: ["conversation"] });
        },
    });
}

/** One site the sandbox's browser is signed in to. */
export interface WebLogin {
    /** The site, as a person would name it — grouped by registrable domain, so
     *  a host and its API are one login rather than two. */
    site: string;
    cookie_count: number;
    /** When the soonest of them lapses. Null when they are all session
     *  cookies, which is the closest a browser gets to "no idea". */
    expires: string | null;
    /** Whether somebody answered "yes, I signed in" for this site. `false`
     *  means "nobody said so", not "no session". */
    signed_in: boolean;
}

/** The sites your sandbox's browser is signed in to.
 *
 *  Read from the browser every time rather than from a table: it keeps its own
 *  profile, so what it holds is the only true answer. Nothing here returns a
 *  secret, and that is structural rather than a promise — cookie values never
 *  leave the sandbox.
 *
 *  Not paged: this is what one browser is holding, not a table that grows.
 *  `wake` is off until somebody asks, because opening a pane should not be
 *  what starts a computer.
 */
export function useWebLogins(scope: WorkspaceScope | null, wake: boolean, enabled: boolean) {
    return useQuery({
        queryKey: ["computer", "logins", ...scopeKey(scope), wake],
        queryFn: () => lemma().request<{ items?: WebLogin[]; sleeping?: boolean }>(
            "GET", "/web-logins", { params: { ...scopeParams(scope!), wake: wake || undefined } },
        ),
        enabled: live() && enabled && scope !== null,
        staleTime: 10_000,
        retry: false,
    });
}

/** Sign the browser out of a site.
 *
 *  Really signs it out, which is why it needs the machine running and says so
 *  rather than reporting a success it did not achieve.
 */
export function useForgetWebLogin(scope: WorkspaceScope | null) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (origin: string) => lemma().request<{ site: string; forgotten: boolean }>(
            "DELETE", "/web-logins", { params: { ...(scope ? scopeParams(scope) : {}), origin } },
        ),
        /* Both keys: forgetting is done from the woken list, and the sleeping
           one is what renders on the next visit. */
        onSuccess: () => { void cache.invalidateQueries({ queryKey: ["computer", "logins"] }); },
    });
}

/** Where a conversation's agent works: its directory in the sandbox, and
 *  which agent it is — whose machine that sandbox is. */
export interface ConversationWorkspace {
    cwd: string | null;
    agentId: string | null;
}

function useConversationWorkspaceQuery<T>(podId: string, conversationId: string | null, select: (found: ConversationWorkspace) => T) {
    return useQuery({
        queryKey: ["computer", "cwd", podId, conversationId ?? ""],
        queryFn: async (): Promise<ConversationWorkspace> => {
            const found = await lemma(podId).conversations.get(conversationId!, { pod_id: podId });
            const said = (found ?? {}) as { workspace_cwd?: unknown; agent_id?: unknown };
            return {
                cwd: typeof said.workspace_cwd === "string" && said.workspace_cwd.startsWith("/") ? said.workspace_cwd : null,
                agentId: typeof said.agent_id === "string" && said.agent_id ? said.agent_id : null,
            };
        },
        select,
        enabled: live() && Boolean(conversationId),
        staleTime: 5 * 60_000,
        retry: false,
    });
}

export function useConversationDirectory(podId: string, conversationId: string | null) {
    return useConversationWorkspaceQuery(podId, conversationId, (found) => found.cwd);
}

/** The machine a pane opened over a conversation shows: the pod's, as that
 *  conversation's agent — each agent has its own. Without a conversation it
 *  is the pod assistant's. Null until the conversation has said, so nothing
 *  is asked of the wrong machine meanwhile — and null for good if it cannot
 *  be read, rather than falling back to a machine that is not this one. */
export function useWorkspaceScope(podId: string | null, conversationId: string | null): WorkspaceScope | null {
    const found = useConversationWorkspaceQuery(podId ?? "", podId ? conversationId : null, (one) => one.agentId);
    if (!podId) return null;
    if (!conversationId) return { podId, agentId: null };
    if (!found.isSuccess) return found.isError ? { podId, agentId: null } : null;
    return { podId, agentId: found.data };
}
