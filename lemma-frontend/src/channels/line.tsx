"use client";

import { useContext, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMe } from "@/session/use-me";
import { Prose } from "@/thread/markdown";
import { AgentIcon } from "@/ui/icons";
import { channelApi } from "./api";
import {
    ASKER_ONLY_NOTE,
    answerSummary,
    asWaitingQuestion,
    initials,
    isMine,
    lineMeta,
    personName,
    questionState,
    sayQuestion,
    senderLabel,
    withheldNote,
    type ChannelDetail,
    type Line,
    type Part,
} from "./model";
import type { Answering } from "./live";
import { MentionablesContext, useMentionPicker } from "./mention-picker";
import { mentionsIn, type Mentionable } from "./mentions";
import { DeliveryStatus, LineParts, LineText } from "./parts";
import { problemOf } from "./problem";
import { lineKeys } from "./queries";
import { WaitingRow } from "./waiting-panel";

export { problemOf };

/** Changing what one's own line says: the composer's `@` menu, and the
 *  caret after the last word. Who it names goes with the words only when
 *  that changed -- then with its files and links, which the change keeps. */
function EditBox({ line, pending, problem, onSave, onCancel }: {
    line: Line;
    pending: boolean;
    problem: string | null;
    onSave: (change: { text: string; parts?: Part[] }) => void;
    onCancel: () => void;
}) {
    const people = useContext(MentionablesContext);
    const box = useRef<HTMLTextAreaElement>(null);
    const named = line.parts.filter((part): part is Extract<Part, { type: "mention" }> => part.type === "mention");
    const [draft, setDraft] = useState(line.text ?? "");
    const [picked, setPicked] = useState<Mentionable[]>(() => named.flatMap((part) => part.name ? [{ kind: part.kind, id: part.id, name: part.name }] : []));
    const mention = useMentionPicker({ people, text: draft, setText: setDraft, box, onPicked: (who) => setPicked((was) => [...was, who]) });
    const { setCaret } = mention;
    useEffect(() => {
        const end = box.current?.value.length ?? 0;
        box.current?.focus();
        box.current?.setSelectionRange(end, end);
        setCaret(end);
    }, [setCaret]);

    const save = (event: { preventDefault(): void }) => {
        event.preventDefault();
        const text = draft.trim();
        if (!text || pending) return;
        /* A name with no words to find it by -- named by an id alone -- is
           kept as it was. */
        const kept = named.filter((part) => !part.name);
        const now = mentionsIn(text, picked);
        const key = (one: { kind: string; id: string }) => one.kind + ":" + one.id;
        const before = new Set(named.map(key));
        const after = new Set([...now, ...kept].map(key));
        const renamed = before.size !== after.size || [...after].some((one) => !before.has(one));
        if (!renamed) {
            if (text === line.text) onCancel();
            else onSave({ text });
            return;
        }
        const mentions: Part[] = [
            ...kept,
            ...now.map((one): Part => ({ type: "mention", kind: one.kind, id: one.id, name: picked.find((who) => key(who) === key(one))?.name ?? null })),
        ];
        onSave({ text, parts: [...mentions, ...line.parts.filter((part) => part.type !== "mention")] });
    };

    return (
        <form className="cline__edit" onSubmit={save}>
            {mention.menu}
            <textarea
                ref={box}
                aria-label="Change what you said"
                value={draft}
                rows={Math.min(8, Math.max(2, draft.split("\n").length))}
                {...mention.boxProps}
                onChange={(event) => { mention.changed(event.target.value, event.target.selectionStart); }}
                onKeyDown={(event) => {
                    if (mention.keyDown(event)) return;
                    if (event.key === "Escape") { event.preventDefault(); onCancel(); }
                    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); save(event); }
                }}
            />
            <div className="cline__edit-acts">
                <button type="button" className="linkish" onClick={onCancel}>Cancel</button>
                <button type="submit" className="pill-button" disabled={pending || !draft.trim()}>{pending ? "Saving…" : "Save"}</button>
            </div>
            {problem && <p className="cline__problem" role="alert">{problem}</p>}
        </form>
    );
}

/** One line of a channel: who said it and for whom, where and when, what it
 *  said -- or that it was withheld, or deleted -- and, on a question, the way
 *  to answer it when it waits on the reader. The reader's own lines can be
 *  changed or deleted here, and whoever may change the channel can remove
 *  anybody's; a copy already carried outside stays as sent. */
export function LineView({ podId, channel, line, onThread, mayWrite = true, answering = [], agentName, highlighted = false }: {
    podId: string;
    channel: ChannelDetail;
    line: Line;
    /** Open the line's thread. Absent inside a thread, which has no threads. */
    onThread?: (line: Line) => void;
    /** The reader may write here: their own lines can be changed. */
    mayWrite?: boolean;
    /** Agents writing an answer to this line right now. */
    answering?: Answering[];
    agentName?: (agentId: string | null) => string;
    /** Opened from search: drawn out, and scrolled to. */
    highlighted?: boolean;
}) {
    const me = useMe();
    const cache = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [confirming, setConfirming] = useState(false);
    /* Only where this line is held; the socket brings the change too. */
    const reread = () => { for (const key of lineKeys(podId, channel.id, line)) void cache.invalidateQueries({ queryKey: key }); };
    const edit = useMutation({
        mutationFn: (change: { text: string; parts?: Part[] }) => channelApi(podId).edit(channel.id, line.id, change.text, change.parts),
        onSuccess: () => { setEditing(false); reread(); },
    });
    const remove = useMutation({
        mutationFn: () => channelApi(podId).remove(channel.id, line.id),
        onSuccess: () => { setConfirming(false); reread(); },
    });
    const [sharing, setSharing] = useState(false);
    const share = useMutation({
        mutationFn: () => channelApi(podId).share(channel.id, line.id),
        onSuccess: () => { setSharing(false); reread(); },
    });
    const mine = isMine(line, me) && mayWrite;
    /* Who may change the channel removes anybody's line in it -- never edits
       it. The server asks again; a copy already sent outside stays as sent. */
    const removable = mine || (mayWrite && channel.me.canManage);
    const agent = line.sender.kind === "AGENT";
    const withheld = withheldNote(line, me);
    const meta = lineMeta(line, channel.places);
    return (
        <li
            className="cline"
            id={"line-" + line.id}
            data-seq={line.seq || undefined}
            data-highlight={highlighted || undefined}
            data-agent={agent || undefined}
            data-note={line.membersOnly || undefined}
            data-gone={line.deleted || line.withheld || undefined}
        >
            <span className="cline__face" aria-hidden="true">
                {agent ? <span className="gface gface--agent"><AgentIcon size={16} /></span>
                    : <span className="gface">{initials(personName(line.sender, null))}</span>}
            </span>
            <div className="cline__body">
                <div className="cline__head">
                    <span className="cline__who">{senderLabel(line, me)}</span>
                    {line.sender.kind === "CONTACT" && <span className="cline__chip">Contact</span>}
                    {line.membersOnly && <span className="cline__chip cline__chip--note">Members only</span>}
                    {meta && <time className="cline__at" dateTime={line.createdAt}>{meta}</time>}
                    {line.editedAt && !line.deleted && <span className="cline__at">· edited</span>}
                </div>

                {line.deleted ? (
                    <p className="cline__gone">{removedNote(line, me)}</p>
                ) : withheld ? (
                    <p className="cline__gone">{withheld}</p>
                ) : editing ? (
                    <EditBox
                        line={line}
                        pending={edit.isPending}
                        problem={edit.isError ? problemOf(edit.error, "Couldn’t change it.") : null}
                        onSave={(change) => edit.mutate(change)}
                        onCancel={() => setEditing(false)}
                    />
                ) : line.text ? (
                    /* An agent writes markdown, as everywhere it answers; a
                       person's line is kept as typed. */
                    agent ? <div className="cline__text cline__text--md"><Prose text={line.text} /></div>
                        : <LineText text={line.text} parts={line.parts} me={me} />
                ) : null}
                {!line.deleted && !withheld && !editing && <LineParts podId={podId} channelId={channel.id} line={line} />}
                {line.canShare && (
                    <div className="cline__private">
                        <span>{ASKER_ONLY_NOTE}</span>
                        {sharing ? (
                            <>
                                <span className="cline__ask-delete">Everyone in this channel will read it.</span>
                                <button type="button" className="cline__act" disabled={share.isPending} onClick={() => share.mutate()}>{share.isPending ? "Sharing…" : "Share"}</button>
                                <button type="button" className="cline__act" onClick={() => setSharing(false)}>Keep private</button>
                            </>
                        ) : (
                            <button type="button" className="cline__act" onClick={() => { share.reset(); setSharing(true); }}>Share with channel</button>
                        )}
                    </div>
                )}
                {share.isError && <p className="cline__problem" role="alert">{problemOf(share.error, "Couldn’t share it.")}</p>}
                {line.delivered && isMine(line, me) && <DeliveryStatus podId={podId} channel={channel} line={line} canRetry={mayWrite} />}

                {line.interaction && <Question podId={podId} channel={channel} line={line} me={me} />}

                <div className="cline__foot">
                    {onThread && line.replyCount > 0 && (
                        <button type="button" className="cline__replies" onClick={() => onThread(line)}>
                            {line.replyCount === 1 ? "1 reply" : line.replyCount + " replies"}
                        </button>
                    )}
                    {!line.deleted && !editing && (
                        <span className="cline__acts">
                            {onThread && line.replyCount === 0 && <button type="button" className="cline__act" onClick={() => onThread(line)}>Reply in thread</button>}
                            {mine && line.text !== null && <button type="button" className="cline__act" onClick={() => { edit.reset(); setEditing(true); }}>Edit</button>}
                            {removable && (confirming ? (
                                <>
                                    <span className="cline__ask-delete">{!mine ? "Remove " + personName(line.sender, me) + "’s line for everyone?" : "Delete it?"}</span>
                                    <button type="button" className="cline__act cline__act--bad" disabled={remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "Deleting…" : "Delete"}</button>
                                    <button type="button" className="cline__act" onClick={() => setConfirming(false)}>Keep</button>
                                </>
                            ) : <button type="button" className="cline__act" onClick={() => setConfirming(true)}>Delete</button>)}
                        </span>
                    )}
                </div>
                {remove.isError && <p className="cline__problem" role="alert">{problemOf(remove.error, "Couldn’t delete it.")}</p>}
                {answering.map((one) => (
                    /* Not a live region: every piece written would be read out
                       again. Its start and its finished line are said by the
                       timeline's own announcer. */
                    <div key={one.messageId + (one.agentId ?? "")} className="cline__answering">
                        <span className="cline__answering-who">{(agentName?.(one.agentId) ?? "An agent") + " is answering…"}</span>
                        {one.text && <div className="cline__text cline__text--md cline__text--draft"><Prose text={one.text} /></div>}
                    </div>
                ))}
            </div>
        </li>
    );
}

/** What a deleted line says in its place: who removed it, when that was
 *  not who wrote it. */
function removedNote(line: Line, me: string | null): string {
    if (!line.removedBy) return "This line was deleted.";
    const who = me && line.removedBy.id === me ? "you" : personName(line.removedBy, me);
    return "Removed by " + who + ".";
}

/** The question a line carries: answered here when it waits on the reader,
 *  else where it stands. */
function Question({ podId, channel, line, me }: { podId: string; channel: ChannelDetail; line: Line; me: string | null }) {
    const interaction = line.interaction;
    if (!interaction) return null;
    const state = questionState(interaction, me);
    if (state.kind === "yours") {
        const question = asWaitingQuestion(line, channel, me);
        return question ? (
            <div className="cline__ask">
                {state.answering && <p className="cline__asked">Being answered… If that answer doesn’t arrive, answer it again here.</p>}
                <WaitingRow podId={podId} question={question} bare />
            </div>
        ) : null;
    }
    const said = sayQuestion(state);
    const answer = state.kind === "answered" ? answerSummary(interaction.answer) : null;
    return (
        <p className="cline__asked" data-open={state.kind === "waiting" || undefined}>
            {said}{answer && <>: <span className="cline__answer">{answer}</span></>}
        </p>
    );
}
