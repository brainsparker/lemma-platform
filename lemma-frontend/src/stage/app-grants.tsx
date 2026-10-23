import { useEffect, useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import {
    APP_EDIT, GRANT_KINDS, POD_RESOURCE_NAME, conferralRefusal, covers, grantFor, grantKind, sameGrants,
    sayMissingGrant, sayPermission, sayPermissions, sayResource, withGrant, type Grant, type MissingGrant,
} from "@/data/app-grants";
import { Modal } from "@/shell/modal";

/** The app's grants, the pod's answer to "may I change them", both cached
 *  under keys the notice and the panel share. */
export function useAppGrants(podId: string, appName: string) {
    return useQuery({ queryKey: ["app-grants", podId, appName], queryFn: () => source.appGrants(podId, appName), staleTime: 30_000 });
}

export function useMayEditApps(podId: string): boolean {
    /* The same key and shape the Connections pages use, so one answer serves
       both. */
    const actions = useQuery({ queryKey: ["pod-actions", podId], queryFn: async () => ({ actions: await source.podActions(podId) }), staleTime: 60_000 });
    return Boolean(actions.data?.actions.includes(APP_EDIT));
}

/** Why a save was refused, said the way the person can act on it. A grant the
 *  saver does not hold is the common one, and the fix is somebody else. */
function SaveProblem({ error }: { error: unknown }) {
    const refused = conferralRefusal(error);
    if (refused) {
        return (
            <p className="appgrants__problem" role="alert">
                <b>You can’t grant what you don’t have.</b>{" "}
                {refused.length > 0 ? "You don’t hold " + refused.map((id) => "“" + sayPermission(id) + "” (" + id + ")").join(", ") + " yourself. " : ""}
                Ask someone who does to add it, or leave it out.
            </p>
        );
    }
    const message = error instanceof Error && error.message ? error.message : "Those grants could not be saved.";
    return <p className="appgrants__problem" role="alert">{message}</p>;
}

/** What an app may do for whoever views it, and — for an editor — the place
 *  to change it. Saves the whole set at once: the server replaces it, and a
 *  grant left out of the draft is a grant taken away. `prefill` arrives from
 *  "Add this grant" and lands in the draft, marked new, for one Save. */
export function AppGrantsPanel({ podId, podName, appName, appLabel, prefill }: {
    podId: string;
    podName: string;
    appName: string;
    appLabel: string;
    prefill?: MissingGrant | null;
}) {
    const cache = useQueryClient();
    const grants = useAppGrants(podId, appName);
    const editable = useMayEditApps(podId);
    const [draft, setDraft] = useState<Grant[] | null>(null);
    const [kind, setKind] = useState(prefill?.resourceType ?? GRANT_KINDS[0].type);
    const [name, setName] = useState(prefill?.resourceName ?? "");
    const [picked, setPicked] = useState<string[]>(prefill?.permissionIds ?? []);
    const formId = useId();

    /* The prefilled grant goes into the draft once the saved set is known, so
       Save shows exactly what changes. Already granted, it adds nothing. */
    useEffect(() => {
        if (!grants.data || draft !== null || !prefill || !editable) return;
        if (!covers(grants.data, prefill)) setDraft(withGrant(grants.data, grantFor(prefill)));
    }, [grants.data, draft, prefill, editable]);

    const saved = grants.data ?? [];
    const shown = draft ?? saved;
    const dirty = draft !== null && !sameGrants(draft, saved);
    const save = useMutation({
        mutationFn: (next: Grant[]) => source.setAppGrants(podId, appName, next.filter((grant) => grant.permissions.length > 0)),
        onSuccess: (next) => {
            cache.setQueryData(["app-grants", podId, appName], next);
            setDraft(null);
        },
    });

    const isNew = (grant: Grant) => !saved.some((one) => one.resource === grant.resource && one.name === grant.name
        && grant.permissions.every((id) => one.permissions.includes(id)));
    const current = grantKind(kind) ?? GRANT_KINDS[0];
    const onPod = current.type === "pod";
    const add = () => {
        const next = withGrant(shown, { resource: current.type, name: onPod ? POD_RESOURCE_NAME : name, permissions: picked });
        setDraft(next);
        setName("");
        setPicked([]);
    };
    const remove = (grant: Grant) => setDraft(shown.filter((one) => one !== grant));

    if (grants.isPending) return <p className="appgrants__quiet" role="status">Reading what {appLabel} may do…</p>;
    if (grants.isError) {
        return (
            <p className="appgrants__problem" role="alert">
                Couldn’t read {appLabel}’s grants. <button className="linkish" onClick={() => void grants.refetch()}>Try again</button>
            </p>
        );
    }
    return (
        <div className="appgrants">
            <p className="appgrants__lede">
                {appLabel} acts as whoever opens it, and only within these grants — never more than that person could do.
                A call outside them is refused, and named, so you can add it here.
            </p>
            {prefill && draft === null && covers(saved, prefill) && (
                <p className="appgrants__quiet" role="status">{appLabel} has this grant now. Reload it to try again.</p>
            )}
            {shown.length === 0
                ? <p className="appgrants__quiet">No grants. {appLabel} can’t read or change anything in {podName}.</p>
                : (
                    <ul className="appgrants__list" aria-label={appLabel + "’s grants"}>
                        {shown.map((grant) => (
                            <li key={grant.resource + "\u0000" + grant.name} data-new={draft !== null && isNew(grant) || undefined}>
                                <span className="appgrants__what">
                                    <b>{sayResource(grant.resource, grant.resource === "pod" ? null : grant.name)}</b>
                                    <small>can {sayPermissions(grant.permissions, grant.resource)}</small>
                                    <code>{grant.permissions.join(", ")}</code>
                                </span>
                                {draft !== null && isNew(grant) && <span className="appgrants__badge">New</span>}
                                {editable && (
                                    <button className="linkish" disabled={save.isPending} onClick={() => remove(grant)}
                                        aria-label={"Remove " + sayResource(grant.resource, grant.name) + " from " + appLabel + "’s grants"}>Remove</button>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            {editable ? (
                <>
                    <form className="appgrants__add" aria-labelledby={formId} onSubmit={(event) => { event.preventDefault(); add(); }}>
                        <h4 id={formId}>Add a grant</h4>
                        <div className="appgrants__fields">
                            <label>
                                <span>On</span>
                                <select value={current.type} onChange={(event) => { setKind(event.target.value); setPicked([]); }}>
                                    {GRANT_KINDS.map((one) => <option key={one.type} value={one.type}>{one.label}</option>)}
                                </select>
                            </label>
                            {!onPod && (
                                <label>
                                    <span>Name</span>
                                    <input value={name} placeholder={current.placeholder ?? ""} onChange={(event) => setName(event.target.value)} />
                                </label>
                            )}
                        </div>
                        <fieldset className="appgrants__perms">
                            <legend>It may</legend>
                            {current.permissions.map((id) => (
                                <label key={id}>
                                    <input type="checkbox" checked={picked.includes(id)}
                                        onChange={(event) => setPicked((now) => event.target.checked ? [...now, id] : now.filter((one) => one !== id))} />
                                    <span>{sayPermission(id, current.type)}</span>
                                    <code>{id}</code>
                                </label>
                            ))}
                        </fieldset>
                        <button className="btn" type="submit" disabled={picked.length === 0 || (!onPod && !name.trim())}>Add to the list</button>
                    </form>
                    <div className="appgrants__actions">
                        {dirty && <button className="btn" type="button" disabled={save.isPending} onClick={() => { setDraft(null); save.reset(); }}>Discard</button>}
                        <button className="btn btn--primary" type="button" disabled={!dirty || save.isPending} onClick={() => draft && save.mutate(draft)}>
                            {save.isPending ? "Saving…" : "Save grants"}
                        </button>
                    </div>
                    {save.isError && <SaveProblem error={save.error} />}
                </>
            ) : (
                <p className="appgrants__quiet">Only an editor of {podName} can change what {appLabel} may do.</p>
            )}
        </div>
    );
}

export function AppGrantsDialog({ podId, podName, appName, appLabel, prefill, onClose }: {
    podId: string; podName: string; appName: string; appLabel: string;
    prefill?: MissingGrant | null;
    onClose: () => void;
}) {
    return (
        <Modal title={"What " + appLabel + " may do"} subtitle="Grants" onClose={onClose}>
            <AppGrantsPanel podId={podId} podName={podName} appName={appName} appLabel={appLabel} prefill={prefill} />
        </Modal>
    );
}

/** An app's call refused for want of a grant: said over the app, with the fix
 *  for someone who can make it. The viewer is not the problem here — they may
 *  well hold it — so this never reads as "you don't have access". */
export function AppNotGrantedNotice({ podId, podName, appName, appLabel, missing, hidden, onDismiss }: {
    podId: string; podName: string; appName: string; appLabel: string;
    missing: MissingGrant;
    hidden?: boolean;
    onDismiss: () => void;
}) {
    const editable = useMayEditApps(podId);
    const [open, setOpen] = useState(false);
    return (
        <div className="appgrant-notice" role="alert" hidden={hidden}>
            <p>
                <b>{sayMissingGrant(missing)}</b>
                <small>
                    {editable
                        ? "Add the grant and reload the app."
                        : "An editor of " + podName + " can add it to the app’s grants."}
                    {missing.permissionIds.length > 0 && <> <code>{missing.permissionIds.join(", ")}</code></>}
                </small>
            </p>
            {editable && <button className="btn btn--primary" onClick={() => setOpen(true)}>Add this grant</button>}
            <button className="linkish" onClick={onDismiss} aria-label="Dismiss">Dismiss</button>
            {open && (
                <AppGrantsDialog podId={podId} podName={podName} appName={appName} appLabel={appLabel} prefill={missing}
                    onClose={() => setOpen(false)} />
            )}
        </div>
    );
}
