"use client";

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { liveAvailable, podLive } from "@/live/pod-live";
import { usePanelFocus } from "@/ui/focus-trap";
import { CloseIcon } from "@/ui/icons";
import { Composer } from "./composer";
import { LineView } from "./line";
import { upsertThread, type Answering } from "./live";
import type { Mentionable } from "./mentions";
import type { ChannelDetail, Line, LinePage } from "./model";
import { linesKey, threadKey, useThread } from "./queries";

/** One thread: the line it hangs off, its replies, and a reply of your own.
 *  Mounted per thread (keyed by its first line), so a reply begun in one
 *  thread stays with it. Holds the keyboard while open: Escape closes it and
 *  gives focus back to what opened it. */
export function ThreadPanel({ podId, channel, root, people, visible, mayWrite, writeRefused, answering, agentName, highlight, onTyping, onClose }: {
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
    onTyping: () => void;
    onClose: () => void;
}) {
    const cache = useQueryClient();
    const panel = useRef<HTMLElement>(null);
    const keys = usePanelFocus(panel, onClose, { focusFirst: false });
    const replies = useThread(podId, channel.id, root.id, visible);
    const posted = (line: Line) => {
        cache.setQueryData<LinePage>(threadKey(podId, channel.id, root.id), (was) => was ? upsertThread(was, line) : was);
        /* The first line counts its replies: the socket sends it again with
           the new count; without the socket it is read again. */
        if (!liveAvailable() || podLive(podId).status !== "open") void cache.invalidateQueries({ queryKey: linesKey(podId, channel.id) });
    };
    useEffect(() => {
        if (!highlight) return;
        panel.current?.querySelector("#line-" + CSS.escape(highlight))?.scrollIntoView({ block: "center" });
    }, [highlight, replies.data]);
    const count = replies.data?.items.length ?? 0;
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
                    {replies.isPending ? "Loading replies…" : replies.isError ? "Couldn’t load the replies." : count === 1 ? "1 reply" : count + " replies"}
                    {replies.isError && <> <button type="button" className="gsheet__inline" onClick={() => void replies.refetch()}>Try again</button></>}
                </p>
                <ol className="cday__lines">
                    {(replies.data?.items ?? []).map((line) => (
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
                {answering.filter((one) => one.messageId === root.id).map((one) => (
                    <p key={"a" + (one.agentId ?? "")} className="cline__answering-who" role="status">{agentName(one.agentId)} is answering…</p>
                ))}
            </div>
            <Composer
                podId={podId}
                channel={channel}
                people={people}
                threadRootId={root.id}
                placeholder="Reply in the thread"
                disabledReason={mayWrite ? null : writeRefused ?? "You can’t write here."}
                onPosted={posted}
                onTyping={onTyping}
            />
        </aside>
    );
}
