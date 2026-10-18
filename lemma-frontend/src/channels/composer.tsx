"use client";

import { useId, useRef, useState, type KeyboardEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { SendIcon } from "@/ui/icons";
import { channelApi } from "./api";
import { problemOf } from "./line";
import { insertMention, matchMentionable, mentionAt, mentionsIn, type Mentionable } from "./mentions";
import { composerChoices, sourceLabel, type ChannelDetail, type Line } from "./model";

/** Writing in a channel, or in one thread of it.
 *
 *  Enter sends and Shift+Enter breaks the line. `@` opens the people and
 *  agents who can be named; a named agent answers. Where something outside
 *  hears the channel, a line can be a note to members instead, which never
 *  leaves Lemma; where more than one place does, the line can be sent to one
 *  of them -- by default the server's choice, the one it last heard from. */
export function Composer({ podId, channel, people, threadRootId, placeholder, onPosted }: {
    podId: string;
    channel: ChannelDetail;
    people: Mentionable[];
    threadRootId?: string;
    placeholder: string;
    onPosted: (line: Line) => void;
}) {
    const ids = useId();
    const box = useRef<HTMLTextAreaElement>(null);
    const [text, setText] = useState("");
    const [caret, setCaret] = useState(0);
    const [picked, setPicked] = useState<Mentionable[]>([]);
    const [membersOnly, setMembersOnly] = useState(false);
    const [place, setPlace] = useState("");
    const [highlight, setHighlight] = useState(0);
    const [dismissed, setDismissed] = useState<number | null>(null);
    const choices = composerChoices(channel);

    const at = mentionAt(text, caret);
    const offered = at && dismissed !== at.start ? matchMentionable(people, at.query) : [];
    const menuOpen = offered.length > 0;

    const send = useMutation({
        mutationFn: (body: { text: string; mentions: ReturnType<typeof mentionsIn> }) => channelApi(podId).post(channel.id, {
            text: body.text,
            mentions: body.mentions,
            membersOnly: choices.notes && membersOnly,
            sourceId: membersOnly ? null : place || null,
            threadRootId: threadRootId ?? null,
        }),
        onSuccess: (line) => {
            setText("");
            setPicked([]);
            onPosted(line);
            box.current?.focus();
        },
    });

    const submit = () => {
        const said = text.trim();
        if (!said || send.isPending) return;
        send.mutate({ text: said, mentions: mentionsIn(said, picked) });
    };

    const pick = (who: Mentionable) => {
        if (!at) return;
        const next = insertMention(text, at, caret, who);
        setText(next.text);
        setPicked((was) => [...was, who]);
        setCaret(next.caret);
        setHighlight(0);
        requestAnimationFrame(() => {
            box.current?.focus();
            box.current?.setSelectionRange(next.caret, next.caret);
        });
    };

    const keys = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.nativeEvent.isComposing) return;
        if (menuOpen) {
            if (event.key === "ArrowDown") { event.preventDefault(); setHighlight((was) => (was + 1) % offered.length); return; }
            if (event.key === "ArrowUp") { event.preventDefault(); setHighlight((was) => (was - 1 + offered.length) % offered.length); return; }
            if (event.key === "Enter" || event.key === "Tab") { event.preventDefault(); pick(offered[Math.min(highlight, offered.length - 1)]); return; }
            if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (at) setDismissed(at.start); return; }
        }
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
    };

    const listId = ids + "-people";
    const active = menuOpen ? listId + "-" + Math.min(highlight, offered.length - 1) : undefined;
    return (
        <form className="chcompose" data-note={(choices.notes && membersOnly) || undefined} onSubmit={(event) => { event.preventDefault(); submit(); }}>
            {menuOpen && (
                <ul className="chcompose__menu" role="listbox" id={listId} aria-label="Name someone">
                    {offered.map((who, index) => (
                        <li
                            key={who.kind + who.id}
                            id={listId + "-" + index}
                            role="option"
                            aria-selected={index === Math.min(highlight, offered.length - 1)}
                            className="chcompose__option"
                            onMouseDown={(event) => { event.preventDefault(); pick(who); }}
                        >
                            <span>{who.name}</span>
                            <small>{who.kind === "AGENT" ? "Agent — answers when named" : who.kind === "CONTACT" ? "Contact" : "Member"}</small>
                        </li>
                    ))}
                </ul>
            )}
            <label className="sr-only" htmlFor={ids + "-text"}>{placeholder}</label>
            <textarea
                ref={box}
                id={ids + "-text"}
                className="chcompose__text"
                rows={Math.min(8, Math.max(1, text.split("\n").length))}
                value={text}
                placeholder={placeholder}
                maxLength={8000}
                role="combobox"
                aria-expanded={menuOpen}
                aria-controls={menuOpen ? listId : undefined}
                aria-activedescendant={active}
                aria-autocomplete="list"
                onChange={(event) => { setText(event.target.value); setCaret(event.target.selectionStart ?? event.target.value.length); setDismissed(null); }}
                onSelect={(event) => setCaret(event.currentTarget.selectionStart ?? 0)}
                onKeyDown={keys}
            />
            <div className="chcompose__bar">
                {choices.notes && (
                    <label className="chcompose__note">
                        <input type="checkbox" checked={membersOnly} onChange={(event) => setMembersOnly(event.target.checked)} />
                        <span>Note to members only</span>
                    </label>
                )}
                {choices.places.length > 0 && !membersOnly && (
                    <label className="chcompose__place">
                        <span className="sr-only">Send to</span>
                        <select value={place} onChange={(event) => setPlace(event.target.value)}>
                            <option value="">Where it last heard from</option>
                            {choices.places.map((one) => <option key={one.id} value={one.id}>{sourceLabel(one.id, channel.places)}</option>)}
                        </select>
                    </label>
                )}
                <button type="submit" className="icon-button chcompose__send" aria-label={membersOnly ? "Send note to members" : "Send"} disabled={send.isPending || !text.trim()}>
                    <SendIcon size={18} />
                </button>
            </div>
            {send.isError && <p className="cline__problem" role="alert">{problemOf(send.error, "Couldn’t send that.")}</p>}
        </form>
    );
}
