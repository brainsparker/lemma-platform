"use client";

import { useId, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { NOWHERE, writeAddress } from "@/shell/address";
import { ChannelIcon } from "@/shell/channels";
import { PlaceLink } from "@/shell/place-link";
import { usePanelFocus } from "@/ui/focus-trap";
import { CloseIcon } from "@/ui/icons";
import type { ChannelAbilities } from "./abilities";
import { AgentSwitch } from "./agent-switch";
import { channelApi, type ChannelChange, type MineChange } from "./api";
import { ContactControls } from "./contact-controls";
import { problemOf } from "./problem";
import {
    addable,
    initials,
    personName,
    sinceShort,
    whoAnswers,
    type AgentAnswers,
    type ChannelDetail,
    type MemberRole,
    type NotifyLevel,
    type SourcePlace,
} from "./model";
import { channelKey, keepChannel, refreshLists } from "./queries";
import { muteChoices, mutedSaid, NOTIFY_VIA, publicWarning } from "./settings-model";
import { useOnline } from "./use-presence";
import { SourceSettingsView } from "./source-settings";
import { isGroupPlatform, platformName } from "./sources";

const LEVELS: { level: NotifyLevel; label: string; note: string }[] = [
    { level: "ALL", label: "Every line", note: "Tell me about everything said here." },
    { level: "MENTIONS", label: "When I’m named", note: "Only lines that @mention me, and questions for me." },
    { level: "NONE", label: "Nothing", note: "I’ll look here when I want to." },
];

/** Everything about a channel beside its timeline: what it is, how you hear
 *  of it, who is in it -- people with their roles, agents with when they
 *  answer -- and the outside places it is fed by. What the reader may not
 *  change is shown, not offered; the API decides again, and a refusal is
 *  said where it happened. Beside the timeline, not over it: Tab moves on
 *  out of it, Escape closes it and gives focus back. */
export function ChannelPanel({ pod, channel, may, onClose, onLeft }: {
    pod: Pod;
    channel: ChannelDetail;
    may: ChannelAbilities;
    onClose: () => void;
    /** The reader left the channel, or deleted it. */
    onLeft: () => void;
}) {
    const panel = useRef<HTMLElement>(null);
    const keys = usePanelFocus(panel, onClose, { trap: false });
    return (
        <aside className="cside" aria-label="About this channel" ref={panel} tabIndex={-1} onKeyDown={keys}>
            <header className="cside__head">
                <h2>About this channel</h2>
                <button type="button" className="icon-button" aria-label="Close" onClick={onClose}><CloseIcon size={16} /></button>
            </header>
            <div className="cside__body">
                <About pod={pod} channel={channel} may={may} onGone={onLeft} />
                <Notify pod={pod} channel={channel} />
                <People pod={pod} channel={channel} may={may} onLeft={onLeft} />
                {channel.places.length > 0 && <Places pod={pod} channel={channel} />}
            </div>
        </aside>
    );
}

/* ── what it is ────────────────────────────────────────────────────── */

function About({ pod, channel, may, onGone }: { pod: Pod; channel: ChannelDetail; may: ChannelAbilities; onGone: () => void }) {
    const me = useMe();
    const cache = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [deleting, setDeleting] = useState(false);
    /* Making a private group public shows everything said in it to the
       whole pod, its history included: asked first. Private needs no ask. */
    const [publishing, setPublishing] = useState(false);
    const [title, setTitle] = useState(channel.title ?? "");
    const [topic, setTopic] = useState(channel.topic ?? "");
    const update = useMutation({
        mutationFn: (body: ChannelChange) => channelApi(pod.id).update(channel.id, body),
        onSuccess: (saved) => { keepChannel(cache, pod.id, saved); setEditing(false); setPublishing(false); },
    });
    const destroy = useMutation({
        mutationFn: () => channelApi(pod.id).destroy(channel.id),
        onSuccess: () => { refreshLists(cache, pod.id); cache.removeQueries({ queryKey: channelKey(pod.id, channel.id) }); onGone(); },
    });
    const refused = (error: unknown, what: string) => isForbidden(error) ? "Only who made this channel, or an editor of " + pod.name + ", can " + what + "." : problemOf(error, "Couldn’t " + what + ".");
    const save = (event: FormEvent) => {
        event.preventDefault();
        if (title.trim()) update.mutate({ title: title.trim(), topic: topic.trim() || null });
    };
    /* A group made in Lemma can go; one an outside place feeds is archived
       while the bot is still there. */
    const deletable = channel.places.length === 0;

    return (
        <section className="gside">
            <h3 className="gside__head">What it is</h3>
            {channel.kind === "DIRECT" && (
                <>
                    <p className="gside__text">Your own channel with {pod.name}. Nobody else in {pod.name} reads it.</p>
                    <p className="gside__fine">{whoAnswers(channel, pod.name, me)}</p>
                </>
            )}
            {channel.kind === "CONTACT" && (
                <>
                    <p className="gside__text">
                        {personName(channel.person, me)} writes to {pod.name} here, from outside it. Only its editors and admins read it.{" "}
                        <PlaceLink className="cside__link" href={writeAddress({ ...NOWHERE, podId: pod.id, tabId: "space:contacts" })}>See them in Contacts</PlaceLink>
                    </p>
                    {channel.attention && !channel.assignee && (
                        <p className="cside__ask" role="status">Asks for a person{channel.attention.reason ? ": " + channel.attention.reason : "."} <span className="cside__since">{sinceShort(channel.attention.since)}</span></p>
                    )}
                    <div className="gowner">
                        <span>{whoAnswers(channel, pod.name, me)}</span>
                        {may.contactOps && <AgentSwitch podId={pod.id} channel={channel} />}
                    </div>
                    {may.contactOps && <ContactControls pod={pod} channel={channel} />}
                    <p className="gside__fine">
                        Writing to them assigns them to you: the agent waits while you are answering, and answers again once you have been quiet for the hand-back time. Switch it off to answer them yourself until you switch it on again.
                    </p>
                </>
            )}
            {channel.kind === "GROUP" && (editing ? (
                <form className="cside__form" onSubmit={save}>
                    <label className="gsheet__field">
                        <span className="gsheet__label">Name</span>
                        <input value={title} maxLength={200} required onChange={(event) => setTitle(event.target.value)} />
                    </label>
                    <label className="gsheet__field">
                        <span className="gsheet__label">What it is for</span>
                        <input value={topic} maxLength={500} onChange={(event) => setTopic(event.target.value)} />
                    </label>
                    <div className="cline__edit-acts">
                        <button type="button" className="linkish" onClick={() => setEditing(false)}>Cancel</button>
                        <button type="submit" className="pill-button" disabled={update.isPending || !title.trim()}>{update.isPending ? "Saving…" : "Save"}</button>
                    </div>
                </form>
            ) : (
                <>
                    <p className="gside__text">{channel.topic ?? "No description yet."}</p>
                    <p className="gside__fine">
                        {channel.visibility === "PRIVATE"
                            ? "Private: only the people in it see it."
                            : "Public: everyone in " + pod.name + " can find it and read it."}{" "}
                        {whoAnswers(channel, pod.name, me)}
                    </p>
                    {may.manage && (
                        <div className="cside__acts">
                            <button type="button" className="gsheet__inline" onClick={() => { setTitle(channel.title ?? ""); setTopic(channel.topic ?? ""); setEditing(true); }}>Rename or describe</button>
                            <button type="button" className="gsheet__inline" disabled={update.isPending} aria-expanded={channel.visibility === "PRIVATE" ? publishing : undefined} onClick={() => channel.visibility === "PRIVATE" ? setPublishing(true) : update.mutate({ visibility: "PRIVATE" })}>
                                {channel.visibility === "PRIVATE" ? "Make it public" : "Make it private"}
                            </button>
                            <button type="button" className="gsheet__inline" disabled={update.isPending} onClick={() => update.mutate({ archived: !channel.archived })}>
                                {channel.archived ? "Bring back from the archive" : "Archive"}
                            </button>
                            {deletable && !deleting && <button type="button" className="gsheet__inline gsheet__inline--bad" onClick={() => setDeleting(true)}>Delete</button>}
                        </div>
                    )}
                    {publishing && channel.visibility === "PRIVATE" && (
                        <div className="cside__confirm" role="group" aria-label="Make this channel public">
                            <p className="gside__text">{publicWarning(channel.title, pod.name)}</p>
                            <div className="cline__edit-acts">
                                <button type="button" className="linkish" onClick={() => setPublishing(false)}>Keep it private</button>
                                <button type="button" className="pill-button" disabled={update.isPending} onClick={() => update.mutate({ visibility: "PUBLIC" })}>{update.isPending ? "Saving…" : "Make it public"}</button>
                            </div>
                        </div>
                    )}
                    {deleting && (
                        <div className="cside__confirm" role="group" aria-label="Delete this channel">
                            <p className="gside__text">Delete {channel.title ?? "this channel"} and everything said in it? This cannot be undone.</p>
                            <div className="cline__edit-acts">
                                <button type="button" className="linkish" onClick={() => setDeleting(false)}>Keep it</button>
                                <button type="button" className="pill-button pill-button--bad" disabled={destroy.isPending} onClick={() => destroy.mutate()}>{destroy.isPending ? "Deleting…" : "Delete"}</button>
                            </div>
                        </div>
                    )}
                </>
            ))}
            {update.isError && <p className="gside__problem" role="alert">{refused(update.error, "change it")}</p>}
            {destroy.isError && <p className="gside__problem" role="alert">{refused(destroy.error, "delete it")}</p>}
        </section>
    );
}

/* ── how you hear of it ────────────────────────────────────────────── */

function Notify({ pod, channel }: { pod: Pod; channel: ChannelDetail }) {
    const ids = useId();
    const cache = useQueryClient();
    const set = useMutation({
        mutationFn: (change: MineChange) => channelApi(pod.id).setMine(channel.id, change),
        onSuccess: (saved) => keepChannel(cache, pod.id, saved),
    });
    const current = set.isPending && set.variables?.notify ? set.variables.notify : channel.me.notify;
    const muted = mutedSaid(channel.me.mutedUntil);
    const via = channel.me.notifyVia?.[0] ?? "";
    if (!channel.me.joined) return null;
    return (
        <section className="gside">
            <h3 className="gside__head" id={ids + "-head"}>Tell me about it</h3>
            <div className="cnotify" role="radiogroup" aria-labelledby={ids + "-head"}>
                {LEVELS.map((choice) => (
                    <label key={choice.level} className="gswitch">
                        <input
                            type="radio"
                            name={ids + "-notify"}
                            checked={current === choice.level}
                            disabled={set.isPending}
                            onChange={() => set.mutate({ notify: choice.level })}
                        />
                        <span className="gswitch__text">
                            <span>{choice.label}</span>
                            <small>{choice.note}</small>
                        </span>
                    </label>
                ))}
            </div>
            <label className="gsheet__field">
                <span className="gside__label">Outside the app, on</span>
                <select value={via} disabled={set.isPending} onChange={(event) => set.mutate({ notifyVia: event.target.value ? [event.target.value] : null })}>
                    <option value="">Wherever {pod.name} reaches me</option>
                    {NOTIFY_VIA.map((one) => <option key={one.value} value={one.value}>{one.label}</option>)}
                </select>
            </label>
            <div className="cside__acts">
                {muted ? (
                    <>
                        <span className="gside__fine">{muted}</span>
                        <button type="button" className="gsheet__inline" disabled={set.isPending} onClick={() => set.mutate({ mutedUntil: null })}>Unmute</button>
                    </>
                ) : (
                    <label className="gsheet__field cside__mute">
                        <span className="sr-only">Mute it</span>
                        <select value="" disabled={set.isPending} onChange={(event) => { if (event.target.value) set.mutate({ mutedUntil: event.target.value }); }}>
                            <option value="">Mute…</option>
                            {muteChoices().map((one) => <option key={one.label} value={one.until}>{one.label}</option>)}
                        </select>
                    </label>
                )}
            </div>
            {set.isError && <p className="gside__problem" role="alert">{problemOf(set.error, "Couldn’t change that.")}</p>}
        </section>
    );
}

/* ── who is in it ──────────────────────────────────────────────────── */

const ROLES: { role: MemberRole; label: string }[] = [
    { role: "owner", label: "Owner" },
    { role: "member", label: "Member" },
];

const ANSWERS: { answers: AgentAnswers; label: string }[] = [
    { answers: "MENTIONED", label: "When named" },
    { answers: "EVERY_LINE", label: "Every line" },
];

function People({ pod, channel, may, onLeft }: { pod: Pod; channel: ChannelDetail; may: ChannelAbilities; onLeft: () => void }) {
    const me = useMe();
    const cache = useQueryClient();
    const [adding, setAdding] = useState("");
    const [addingAgent, setAddingAgent] = useState("");
    const group = channel.kind === "GROUP";
    const manage = group && may.manage;
    const agents = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000, enabled: manage });
    const candidates = manage ? addable(pod.members, channel.members) : [];
    const seated = new Set(channel.members.filter((one) => one.person.kind === "AGENT").map((one) => one.person.id));
    const agentCandidates = (agents.data ?? []).filter((agent) => agent.id && !seated.has(agent.id));
    const isYou = (member: { kind: string; id: string }) => member.kind === "MEMBER" && member.id === me;
    const reread = () => void cache.invalidateQueries({ queryKey: channelKey(pod.id, channel.id) });
    const add = useMutation({
        mutationFn: (body: { people?: string[]; agents?: { id: string; answers: AgentAnswers }[] }) => channelApi(pod.id).addMembers(channel.id, body),
        onSuccess: (saved) => { keepChannel(cache, pod.id, saved); setAdding(""); setAddingAgent(""); },
    });
    const change = useMutation({
        mutationFn: ({ member, body }: { member: { kind: "MEMBER" | "AGENT"; id: string }; body: { role?: MemberRole; answers?: AgentAnswers } }) =>
            channelApi(pod.id).updateMember(channel.id, member, body),
        onSuccess: reread,
    });
    const remove = useMutation({
        mutationFn: (member: { kind: "MEMBER" | "AGENT"; id: string }) => {
            const api = channelApi(pod.id);
            return isYou(member) ? api.leave(channel.id) : api.removeMember(channel.id, member);
        },
        onSuccess: (_done, member) => {
            if (isYou(member)) { refreshLists(cache, pod.id); onLeft(); return; }
            reread();
        },
    });
    const join = useMutation({
        mutationFn: () => channelApi(pod.id).join(channel.id),
        onSuccess: (saved) => keepChannel(cache, pod.id, saved),
    });
    const people = channel.members.filter((one) => one.person.kind !== "AGENT");
    const online = useOnline(pod.id);
    const seatedAgents = channel.members.filter((one) => one.person.kind === "AGENT");
    return (
        <section className="gside">
            <h3 className="gside__head">Who is in it</h3>
            {group && !channel.me.joined && (
                <p className="gside__text">
                    You are not in it.{" "}
                    <button type="button" className="gsheet__inline" disabled={join.isPending} onClick={() => join.mutate()}>{join.isPending ? "Joining…" : "Join"}</button>
                </p>
            )}
            <ul className="gpeople">
                {people.map((one) => {
                    const name = personName(one.person, me);
                    const you = one.person.kind === "MEMBER" && one.person.id === me;
                    const id = one.person.id;
                    const removable = group && one.person.kind === "MEMBER" && id && (you || manage);
                    return (
                        <li key={one.person.kind + (id ?? name)}>
                            <span className="gface" aria-hidden="true" data-online={(one.person.kind === "MEMBER" && id && online.has(id)) || undefined}>{initials(one.person.name ?? name)}</span>
                            <span className="gpeople__who">
                                <span>{name}{one.person.kind === "MEMBER" && id && online.has(id) && !you ? <span className="sr-only">, here now</span> : null}</span>
                                <small>{one.person.kind === "CONTACT" ? "Outside " + pod.name : (one.role === "owner" ? "Owner" : "Member") + " · in " + pod.name}</small>
                            </span>
                            {manage && one.person.kind === "MEMBER" && id && !you && (
                                <select
                                    aria-label={"Role of " + name}
                                    value={one.role === "owner" ? "owner" : "member"}
                                    disabled={change.isPending}
                                    onChange={(event) => change.mutate({ member: { kind: "MEMBER", id }, body: { role: event.target.value as MemberRole } })}
                                >
                                    {ROLES.map((choice) => <option key={choice.role} value={choice.role}>{choice.label}</option>)}
                                </select>
                            )}
                            {removable && (
                                <button
                                    type="button"
                                    className="ghost-pill gpeople__invite"
                                    disabled={remove.isPending}
                                    aria-label={you ? "Leave this channel" : "Take " + name + " out of this channel"}
                                    onClick={() => remove.mutate({ kind: "MEMBER", id })}
                                >
                                    {you ? "Leave" : "Remove"}
                                </button>
                            )}
                        </li>
                    );
                })}
            </ul>
            {(seatedAgents.length > 0 || manage) && (
                <>
                    <h4 className="gside__label">Agents</h4>
                    <ul className="gpeople">
                        {seatedAgents.map((one) => {
                            const name = one.person.name ?? "An agent";
                            const id = one.person.id;
                            return (
                                <li key={"agent" + (id ?? name)}>
                                    <span className="gface gface--agent" aria-hidden="true">{initials(name)}</span>
                                    <span className="gpeople__who">
                                        <span>{name}</span>
                                        <small>{one.answers === "EVERY_LINE" ? "Answers every line" : "Answers when named"}</small>
                                    </span>
                                    {manage && id && channel.kind === "GROUP" && (
                                        <>
                                            <select
                                                aria-label={"When " + name + " answers"}
                                                value={one.answers ?? "MENTIONED"}
                                                disabled={change.isPending}
                                                onChange={(event) => change.mutate({ member: { kind: "AGENT", id }, body: { answers: event.target.value as AgentAnswers } })}
                                            >
                                                {ANSWERS.map((choice) => <option key={choice.answers} value={choice.answers}>{choice.label}</option>)}
                                            </select>
                                            <button type="button" className="ghost-pill gpeople__invite" disabled={remove.isPending} aria-label={"Take " + name + " out of this channel"} onClick={() => remove.mutate({ kind: "AGENT", id })}>Remove</button>
                                        </>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                </>
            )}
            {(change.isError || remove.isError) && (
                <p className="gside__problem" role="alert">
                    {isForbidden(change.error ?? remove.error) ? "Only who made this channel, or an editor, can change who is in it." : problemOf(change.error ?? remove.error, "Couldn’t do that.")}
                </p>
            )}
            {manage && candidates.length > 0 && (
                <form className="cside__add" onSubmit={(event) => { event.preventDefault(); if (adding) add.mutate({ people: [adding] }); }}>
                    <label className="sr-only" htmlFor={channel.id + "-add"}>Add someone from {pod.name}</label>
                    <select id={channel.id + "-add"} value={adding} onChange={(event) => setAdding(event.target.value)}>
                        <option value="">Add someone from {pod.name}…</option>
                        {candidates.map((member) => <option key={member.userId} value={member.userId}>{member.name}</option>)}
                    </select>
                    <button type="submit" className="ghost-pill" disabled={!adding || add.isPending}>{add.isPending ? "Adding…" : "Add"}</button>
                </form>
            )}
            {manage && agentCandidates.length > 0 && (
                <form className="cside__add" onSubmit={(event) => { event.preventDefault(); if (addingAgent) add.mutate({ agents: [{ id: addingAgent, answers: "MENTIONED" }] }); }}>
                    <label className="sr-only" htmlFor={channel.id + "-add-agent"}>Add an agent</label>
                    <select id={channel.id + "-add-agent"} value={addingAgent} onChange={(event) => setAddingAgent(event.target.value)}>
                        <option value="">Add an agent…</option>
                        {agentCandidates.map((agent) => <option key={agent.id} value={agent.id}>{agent.label}</option>)}
                    </select>
                    <button type="submit" className="ghost-pill" disabled={!addingAgent || add.isPending}>Add</button>
                </form>
            )}
            {add.isError && <p className="gside__problem" role="alert">{problemOf(add.error, "Couldn’t add them.")}</p>}
            {join.isError && <p className="gside__problem" role="alert">{problemOf(join.error, "Couldn’t join it.")}</p>}
        </section>
    );
}

/* ── where it is fed from ──────────────────────────────────────────── */

function Places({ pod, channel }: { pod: Pod; channel: ChannelDetail }) {
    const [open, setOpen] = useState<string | null>(null);
    /* A group's sources have settings of their own; a person's chat or a
       contact's email thread is just where they write from. */
    const settled = (place: SourcePlace) => channel.kind === "GROUP" && isGroupPlatform(place.platform);
    return (
        <section className="gside">
            <h3 className="gside__head">Where it is</h3>
            <ul className="chplaces">
                {channel.places.map((place) => (
                    <li key={place.id} className="chplace">
                        <div className="chplace__row">
                            <ChannelIcon platform={place.platform} size={18} />
                            <span className="gpeople__who">
                                <span>{place.title ?? platformName(place.platform)}</span>
                                <small>
                                    {platformName(place.platform)}
                                    {place.pending ? " · being created" : place.lastInboundAt ? " · " + sinceShort(place.lastInboundAt) : ""}
                                </small>
                            </span>
                            {settled(place) && (
                                <button type="button" className="gsheet__inline" aria-expanded={open === place.id} onClick={() => setOpen((was) => (was === place.id ? null : place.id))}>
                                    {open === place.id ? "Hide settings" : "Settings"}
                                </button>
                            )}
                        </div>
                        {open === place.id && <SourceSettingsView pod={pod} channelId={channel.id} sourceId={place.id} />}
                    </li>
                ))}
            </ul>
        </section>
    );
}
