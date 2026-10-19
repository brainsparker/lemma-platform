"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { liveAvailable, podLive, useLapsing, useLiveStatus, useLiveTopic } from "@/live/pod-live";
import type { LiveStatus } from "@/live/socket";
import { channelApi } from "./api";
import {
    answeringStep,
    mergeChannel,
    readChannelEvent,
    upsertThread,
    upsertTimeline,
    withChannel,
    withLastLine,
    withReadSeq,
    type Answering,
    type ChannelEvent,
} from "./live";
import { senderLabel, type Channel, type ChannelDetail, type Line, type LinePage, type Page } from "./model";
import { channelKey, inboxKey, linesKey, refreshLists, threadKey } from "./queries";
import type { PersonRef } from "./waiting";

/** Where each channel's subscription had got to, so a channel left and
 *  opened again resumes from its cursor -- the server replays what changed
 *  meanwhile -- rather than reading the whole timeline again. */
const cursors = new Map<string, string | null>();

/** How long "Priya is typing" stays up after the last note of it. */
const TYPING_SHOWN_MS = 5_000;
/** The least time between two typing notes from this composer. */
export const TYPING_EVERY_MS = 3_000;

export interface ChannelLive {
    status: LiveStatus;
    typing: PersonRef[];
    answering: Answering[];
    /** The last new line from somebody else, said for a screen reader. */
    announced: string;
    /** "I am typing here", at most every few seconds. */
    typed(): void;
    /** "I have seen this channel up to line `seq`": over the socket, or
     *  over REST when it is down. Resolves to the read position kept. */
    markRead(seq: number): Promise<number>;
}

/** One channel kept live while it is on screen: its timeline, its threads,
 *  the channel itself and the reader's read position follow the socket, and
 *  what is ephemeral -- typing, an answer being written -- is held here. */
export function useChannelLive(podId: string, channel: ChannelDetail, visible: boolean, me: string | null): ChannelLive {
    const cache = useQueryClient();
    const status = useLiveStatus(podId);
    const [typing, heardTyping] = useLapsing<PersonRef>(TYPING_SHOWN_MS);
    const [answering, setAnswering] = useState<Answering[]>([]);
    const [announced, setAnnounced] = useState("");
    const lastTyped = useRef(0);
    const key = podId + ":" + channel.id;

    const applyLine = (event: Extract<ChannelEvent, { line: Line }>) => {
        const line = event.line;
        /* A reply goes into its thread; its first line comes again as
           `line.edited` with the new `reply_count`, so nothing is counted here. */
        if (line.threadRootId) {
            cache.setQueryData<LinePage>(threadKey(podId, channel.id, line.threadRootId), (was) => was ? upsertThread(was, line) : was);
        } else {
            cache.setQueryData<InfiniteData<LinePage>>(linesKey(podId, channel.id), (was) => was ? { ...was, pages: upsertTimeline(was.pages, line).pages } : was);
            if (event.type === "line.created") {
                cache.setQueriesData<InfiniteData<Page<Channel>>>({ queryKey: ["channel", podId, "list"] }, (was) => was
                    ? { ...was, pages: was.pages.map((page) => ({ ...page, items: withLastLine(page.items, channel.id, line) })) }
                    : was);
            }
        }
        cache.setQueryData<ChannelDetail>(channelKey(podId, channel.id), (was) => was && line.seq > was.lastSeq ? { ...was, lastSeq: line.seq, lastMessageAt: line.createdAt } : was);
        const mine = line.sender.kind === "MEMBER" && line.sender.id === me;
        if (event.type === "line.created" && !mine) {
            const said = line.withheld ? "answered someone" : line.deleted ? "" : (line.text ?? "").replace(/\s+/g, " ").slice(0, 160);
            setAnnounced(senderLabel(line, me) + (line.threadRootId ? ", in a thread" : "") + ": " + said);
        }
    };

    const apply = (event: ChannelEvent) => {
        switch (event.type) {
            case "line.created":
            case "line.edited":
            case "line.deleted":
                applyLine(event);
                if (event.line.interaction) void cache.invalidateQueries({ queryKey: inboxKey(podId) });
                return;
            case "question.closed":
                void cache.invalidateQueries({ queryKey: inboxKey(podId) });
                return;
            case "channel.updated":
                cache.setQueryData<ChannelDetail>(channelKey(podId, channel.id), (was) => was ? mergeChannel(was, event.channel) : was);
                cache.setQueriesData<InfiniteData<Page<Channel>>>({ queryKey: ["channel", podId, "list"] }, (was) => was
                    ? { ...was, pages: was.pages.map((page) => ({ ...page, items: withChannel(page.items, event.channel) })) }
                    : was);
                /* Members and places are only on the detail; read it again. */
                void cache.invalidateQueries({ queryKey: channelKey(podId, channel.id) });
                return;
            case "read.updated":
                cache.setQueryData<ChannelDetail>(channelKey(podId, channel.id), (was) => was ? withReadSeq(was, event.readSeq) : was);
                return;
            case "typing":
                if (!(event.user.kind === "MEMBER" && event.user.id === me)) heardTyping(event.user, event.user.kind + ":" + (event.user.id ?? event.user.name ?? ""));
                return;
            case "answering.started":
            case "answering.finished":
            case "line.streaming":
                setAnswering((was) => answeringStep(was, event));
                return;
        }
    };

    const reread = () => {
        void cache.invalidateQueries({ queryKey: linesKey(podId, channel.id) });
        void cache.invalidateQueries({ queryKey: channelKey(podId, channel.id) });
        void cache.invalidateQueries({ queryKey: ["channel", podId, "thread", channel.id] });
    };

    const fresh = useRef(false);
    useLiveTopic(podId, "channel", visible ? { channel_id: channel.id } : null, {
        /* Subscribed with no cursor to resume from: anything written between
           the REST read and now was not replayed, so a timeline already held
           is read once more. */
        onSubscribed: () => {
            if (fresh.current && cache.getQueryData(linesKey(podId, channel.id))) reread();
            fresh.current = false;
        },
        onEvents: (events) => {
            for (const raw of events) {
                const event = readChannelEvent(raw);
                if (event) apply(event);
            }
        },
        onResync: reread,
        /* Gone, or no longer the reader's to see: the detail's 404 says so. */
        onEnded: () => {
            cursors.delete(key);
            void cache.invalidateQueries({ queryKey: channelKey(podId, channel.id) });
            refreshLists(cache, podId);
        },
    }, { since: () => { const held = cursors.get(key) ?? null; fresh.current = held === null; return held; }, onCursor: (cursor) => cursors.set(key, cursor) });

    const typed = useCallback(() => {
        if (!liveAvailable()) return;
        const now = Date.now();
        if (now - lastTyped.current < TYPING_EVERY_MS) return;
        lastTyped.current = now;
        podLive(podId).call("typing", { channel_id: channel.id }).catch(() => { /* a typing note is worth nothing late */ });
    }, [podId, channel.id]);

    const markRead = useCallback(async (seq: number) => {
        let kept = seq;
        if (liveAvailable() && podLive(podId).status === "open") {
            try {
                const result = (await podLive(podId).call("read", { channel_id: channel.id, seq })) as { read_seq?: unknown } | null;
                kept = typeof result?.read_seq === "number" ? result.read_seq : seq;
            } catch {
                await channelApi(podId).markRead(channel.id, seq);
            }
        } else {
            await channelApi(podId).markRead(channel.id, seq);
        }
        cache.setQueryData<ChannelDetail>(channelKey(podId, channel.id), (was) => was ? withReadSeq(was, kept) : was);
        /* Over the socket, its `inbox.updated` moves the counts everywhere;
           without it, the lists and the inbox are read again. */
        if (!liveAvailable() || podLive(podId).status !== "open") refreshLists(cache, podId);
        return kept;
    }, [podId, channel.id, cache]);

    return { status, typing, answering, announced, typed, markRead };
}
