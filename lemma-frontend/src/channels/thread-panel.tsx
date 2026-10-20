"use client";

import { useEffect, useRef } from "react";
import { useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { liveAvailable, podLive } from "@/live/pod-live";
import { usePanelFocus } from "@/ui/focus-trap";
import { CloseIcon } from "@/ui/icons";
import { Composer } from "./composer";
import { LineView } from "./line";
import { sayTyping, upsertThread, type Answering } from "./live";
import type { Mentionable } from "./mentions";
import type { ChannelDetail, Line, ThreadPage } from "./model";
import { linesKey, threadKey, useThread } from "./queries";

/** How many pages of a thread are read on, at most, to reach a reply asked
 *  for by the bell or search. */
const FOCUS_PAGES = 8;

/** One thread: the line it hangs off, its replies -- a page at a time -- and
 *  a reply of your own. Mounted per thread (keyed by its first line), so a
 *  reply begun in one thread stays with it. Beside the timeline, not over
 *  it: Tab moves on out of it; Escape closes it and gives focus back to what
 *  opened it. Who is typing a reply here is said here, and only here. */
export function ThreadPanel({ podId, channel, root, people, visible, mayWrite, writeRefused, answering, agentName, highlight, typing = [], replyAsked = 0, onTyping, onClose }: {
    podId: string;
    channel: ChannelDetail;
    root: Line;
    people: Mentionable[];
    visible: boolean;
    mayWrite: boolean;
    writeRefused: string | null;
    answering: Answering[];
    agentName: (agentId: string | null) => string;
    highlight: string | null;
    /** The names of whoever is typing a reply in this thread. */
    typing?: string[];
    /** Changes each time the reader opens this thread to reply in it: the
     *  reply box takes the focus. */
    replyAsked?: number;
    onTyping: (threadRootId: string) => void;
    onClose: () => void;
}) {
    const cache = useQueryClient();
    const panel = useRef<HTMLElement>(null);
    const keys = usePanelFocus(panel, onClose, { focusFirst: false, trap: false });
    /* After the panel has taken the focus on opening, so the reply box is
       where it lands. */
    useEffect(() => {
        if (replyAsked) panel.current?.querySelector<HTMLTextAreaElement>(".chcompose__text")?.focus();
    }, [replyAsked]);
    const thread = useThread(podId, channel.id, root.id, visible);
    const replies = thread.replies;
    const posted = (line: Line) => {
        cache.setQueryData<InfiniteData<ThreadPage>>(threadKey(podId, channel.id, root.id), (was) => was ? { ...was, pages: upsertThread(was.pages, line) } : was);
        /* The first line counts its replies: the socket sends it again with
           the new count; without the socket it is read again. */
        if (!liveAvailable() || podLive(podId).status !== "open") void cache.invalidateQueries({ queryKey: linesKey(podId, channel.id) });
    };
    /* Opened at one reply: read on until it is held, then there once -- not
       again each time a reply arrives, which would pull the reader back. */
    const placed = useRef<string | null>(null);
    const pagesRead = useRef(0);
    useEffect(() => {
        if (!highlight || placed.current === highlight) return;
        const element = panel.current?.querySelector("#line-" + CSS.escape(highlight));
        if (element) {
            element.scrollIntoView({ block: "center" });
            placed.current = highlight;
            return;
        }
        if (thread.hasNextPage && !thread.isFetchingNextPage && pagesRead.current < FOCUS_PAGES) {
            pagesRead.current += 1;
            void thread.fetchNextPage();
        }
    }, [highlight, replies, thread]);
    const count = Math.max(root.replyCount, replies.length);
    return (
        <aside className="cside chthread" aria-label="Thread" ref={panel} tabIndex={-1} onKeyDown={keys}>
            <header className="cside__head">
                <h2>Thread</h2>
                <button type="button" className="icon-button" aria-label="Close the thread" onClick={onClose}><CloseIcon size={16} /></button>
            </header>
            <div className="chthread__body">
                <ol className="cday__lines">
                    <LineView podId={podId} channel={channel} line={root} mayWrite={mayWrite} />
                </ol>
                <p className="chthread__count">
                    {thread.isPending ? "Loading replies…" : thread.isError && !thread.data ? "Couldn’t load the replies." : count === 1 ? "1 reply" : count + " replies"}
                    {thread.isError && !thread.data && <> <button type="button" className="gsheet__inline" onClick={() => void thread.refetch()}>Try again</button></>}
                </p>
                <ol className="cday__lines">
                    {replies.map((line) => (
                        <LineView
                            key={line.id}
                            podId={podId}
                            channel={channel}
                            line={line}
                            mayWrite={mayWrite}
                            answering={answering.filter((one) => one.messageId === line.id)}
                            agentName={agentName}
                            highlighted={highlight === line.id}
                        />
                    ))}
                </ol>
                {thread.hasNextPage && (
                    <button type="button" className="ghost-pill chthread__more" disabled={thread.isFetchingNextPage} onClick={() => void thread.fetchNextPage()}>
                        {thread.isFetchingNextPage ? "Loading…" : "Show later replies"}
                    </button>
                )}
                {answering.filter((one) => one.messageId === root.id).map((one) => (
                    <p key={"a" + (one.agentId ?? "")} className="cline__answering-who" role="status">{agentName(one.agentId)} is answering…</p>
                ))}
            </div>
            <p className="cpage__typing" aria-hidden={typing.length === 0}>{sayTyping(typing)}</p>
            <Composer
                podId={podId}
                channel={channel}
                people={people}
                threadRootId={root.id}
                placeholder="Reply in the thread"
                disabledReason={mayWrite ? null : writeRefused ?? "You can’t write here."}
                onPosted={posted}
                onTyping={() => onTyping(root.id)}
            />
        </aside>
    );
}
