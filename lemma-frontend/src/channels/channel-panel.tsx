"use client";

import { useId, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { NOWHERE, writeAddress } from "@/shell/address";
import { ChannelIcon } from "@/shell/channels";
import { PlaceLink } from "@/shell/place-link";
import { CloseIcon } from "@/ui/icons";
import { AgentSwitch } from "./agent-switch";
import { channelApi } from "./api";
import { problemOf } from "./line";
import {
    addable,
    initials,
    personName,
    sinceShort,
    whoAnswers,
    type ChannelDetail,
    type NotifyLevel,
    type SourcePlace,
} from "./model";
import { channelKey, keepChannel, refreshLists } from "./queries";
import { SourceSettingsView } from "./source-settings";
import { isGroupPlatform, platformName } from "./sources";

const LEVELS: { level: NotifyLevel; label: string; note: string }[] = [
    { level: "ALL", label: "Every line", note: "Tell me about everything said here." },
    { level: "MENTIONS", label: "When I’m named", note: "Only lines that @mention me, and questions for me." },
    { level: "NONE", label: "Nothing", note: "I’ll look here when I want to." },
];

/** Everything about a channel beside its timeline: what it is, how you hear
 *  of it, who is in it, and the outside places it is fed by. The API decides
 *  who may change what; a refusal is said where it happened. */
export function ChannelPanel({ pod, channel, onClose, onLeft }: {
    pod: Pod;
    channel: ChannelDetail;
    onClose: () => void;
    /** The reader left the channel. */
    onLeft: () => void;
}) {
    return (
        <aside className="cside" aria-label="About this channel">
            <header className="cside__head">
                <h2>About this channel</h2>
                <button type="button" className="icon-button" aria-label="Close" onClick={onClose}><CloseIcon size={16} /></button>
            </header>
            <div className="cside__body">
                <About pod={pod} channel={channel} />
                <Notify pod={pod} channel={channel} />
                <People pod={pod} channel={channel} onLeft={onLeft} />
                {channel.places.length > 0 && <Places pod={pod} channel={channel} />}
            </div>
        </aside>
    );
}

/* ── what it is ────────────────────────────────────────────────────── */

function About({ pod, channel }: { pod: Pod; channel: ChannelDetail }) {
    const me = useMe();
    const cache = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [title, setTitle] = useState(channel.title ?? "");
    const [topic, setTopic] = useState(channel.topic ?? "");
    const update = useMutation({
        mutationFn: (body: { title?: string | null; topic?: string | null; archived?: boolean }) => channelApi(pod.id).update(channel.id, body),
        onSuccess: (saved) => { keepChannel(cache, pod.id, saved); setEditing(false); },
    });
    const refused = (error: unknown, what: string) => isForbidden(error) ? "Only who made this channel, or an admin of " + pod.name + ", can " + what + "." : problemOf(error, "Couldn’t " + what + ".");
    const save = (event: FormEvent) => {
        event.preventDefault();
        if (title.trim()) update.mutate({ title: title.trim(), topic: topic.trim() || null });
    };

    return (
        <section className="gside">
            <h3 className="gside__head">What it is</h3>
            {channel.kind === "DIRECT" && <p className="gside__text">Your own channel with {pod.name}. Nobody else in {pod.name} reads it.</p>}
            {channel.kind === "CONTACT" && (
                <>
                    <p className="gside__text">
                        {personName(channel.person, me)} writes to {pod.name} here, from outside it. Only its editors and admins read it.{" "}
                        <PlaceLink className="cside__link" href={writeAddress({ ...NOWHERE, podId: pod.id, tabId: "space:contacts" })}>See them in Contacts</PlaceLink>
                    </p>
                    {channel.attention && !channel.handler && (
                        <p className="cside__ask" role="status">Asks for a person{channel.attention.reason ? ": " + channel.attention.reason : "."} <span className="cside__since">{sinceShort(channel.attention.since)}</span></p>
                    )}
                    <div className="gowner">
                        <span>{whoAnswers(channel, pod.name, me)}</span>
                        <AgentSwitch podId={pod.id} channel={channel} />
                    </div>
                    <p className="gside__fine">
                        Writing to them steps in: the agent waits while you are in the conversation. Switch it off to answer them yourself until you switch it on again.
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
                    <p className="gside__fine">{whoAnswers(channel, pod.name, me)}</p>
                    <div className="cside__acts">
                        <button type="button" className="gsheet__inline" onClick={() => { setTitle(channel.title ?? ""); setTopic(channel.topic ?? ""); setEditing(true); }}>Rename or describe</button>
                        <button type="button" className="gsheet__inline" disabled={update.isPending} onClick={() => update.mutate({ archived: !channel.archived })}>
                            {channel.archived ? "Bring back from the archive" : "Archive"}
                        </button>
                    </div>
                </>
            ))}
            {channel.kind === "DIRECT" && <p className="gside__fine">{whoAnswers(channel, pod.name, me)}</p>}
            {update.isError && <p className="gside__problem" role="alert">{refused(update.error, "change it")}</p>}
        </section>
    );
}

/* ── how you hear of it ────────────────────────────────────────────── */

function Notify({ pod, channel }: { pod: Pod; channel: ChannelDetail }) {
    const ids = useId();
    const cache = useQueryClient();
    const set = useMutation({
        mutationFn: (level: NotifyLevel) => channelApi(pod.id).setNotify(channel.id, level),
        onSuccess: (saved) => keepChannel(cache, pod.id, saved),
    });
    const current = set.isPending && set.variables ? set.variables : channel.me.notify;
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
                            onChange={() => set.mutate(choice.level)}
                        />
                        <span className="gswitch__text">
                            <span>{choice.label}</span>
                            <small>{choice.note}</small>
                        </span>
                    </label>
                ))}
            </div>
            {set.isError && <p className="gside__problem" role="alert">{problemOf(set.error, "Couldn’t change that.")}</p>}
        </section>
    );
}

/* ── who is in it ──────────────────────────────────────────────────── */

function People({ pod, channel, onLeft }: { pod: Pod; channel: ChannelDetail; onLeft: () => void }) {
    const me = useMe();
    const cache = useQueryClient();
    const [adding, setAdding] = useState("");
    const group = channel.kind === "GROUP";
    const candidates = group ? addable(pod.members, channel.participants) : [];
    const add = useMutation({
        mutationFn: (userId: string) => channelApi(pod.id).addMembers(channel.id, [userId]),
        onSuccess: (saved) => { keepChannel(cache, pod.id, saved); setAdding(""); },
    });
    const remove = useMutation({
        mutationFn: (memberId: string) => channelApi(pod.id).removeMember(channel.id, memberId),
        onSuccess: (_done, memberId) => {
            if (memberId === me) { refreshLists(cache, pod.id); onLeft(); return; }
            void cache.invalidateQueries({ queryKey: channelKey(pod.id, channel.id) });
        },
    });
    return (
        <section className="gside">
            <h3 className="gside__head">Who is in it</h3>
            <ul className="gpeople">
                {channel.participants.map((one) => {
                    const name = personName(one.person, me);
                    const you = name === "You";
                    const removable = group && one.person.kind === "MEMBER" && one.person.id;
                    return (
                        <li key={one.person.kind + (one.person.id ?? name)}>
                            <span className="gface" aria-hidden="true">{initials(one.person.name ?? name)}</span>
                            <span className="gpeople__who">
                                <span>{name}</span>
                                <small>{one.person.kind === "CONTACT" ? "Outside " + pod.name : one.person.kind === "AGENT" ? "Agent" : "In " + pod.name}</small>
                            </span>
                            {removable && (
                                <button
                                    type="button"
                                    className="ghost-pill gpeople__invite"
                                    disabled={remove.isPending}
                                    aria-label={you ? "Leave this channel" : "Take " + name + " out of this channel"}
                                    onClick={() => remove.mutate(String(one.person.id))}
                                >
                                    {you ? "Leave" : "Remove"}
                                </button>
                            )}
                        </li>
                    );
                })}
            </ul>
            {remove.isError && <p className="gside__problem" role="alert">{isForbidden(remove.error) ? "Only who made this channel, or an admin, can take people out." : problemOf(remove.error, "Couldn’t do that.")}</p>}
            {group && candidates.length > 0 && (
                <form className="cside__add" onSubmit={(event) => { event.preventDefault(); if (adding) add.mutate(adding); }}>
                    <label className="sr-only" htmlFor={channel.id + "-add"}>Add someone from {pod.name}</label>
                    <select id={channel.id + "-add"} value={adding} onChange={(event) => setAdding(event.target.value)}>
                        <option value="">Add someone from {pod.name}…</option>
                        {candidates.map((member) => <option key={member.userId} value={member.userId}>{member.name}</option>)}
                    </select>
                    <button type="submit" className="ghost-pill" disabled={!adding || add.isPending}>{add.isPending ? "Adding…" : "Add"}</button>
                </form>
            )}
            {add.isError && <p className="gside__problem" role="alert">{problemOf(add.error, "Couldn’t add them.")}</p>}
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
                        {open === place.id && <SourceSettingsView pod={pod} sourceId={place.id} />}
                    </li>
                ))}
            </ul>
        </section>
    );
}
