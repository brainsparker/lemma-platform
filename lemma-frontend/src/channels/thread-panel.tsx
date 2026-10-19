"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CloseIcon } from "@/ui/icons";
import { Composer } from "./composer";
import { LineView } from "./line";
import type { Mentionable } from "./mentions";
import type { ChannelDetail, Line, LinePage } from "./model";
import { linesKey, threadKey, useThread } from "./queries";

/** One thread: the line it hangs off, its replies, and a reply of your own. */
export function ThreadPanel({ podId, channel, root, people, visible, onClose }: {
    podId: string;
    channel: ChannelDetail;
    root: Line;
    people: Mentionable[];
    visible: boolean;
    onClose: () => void;
}) {
    const cache = useQueryClient();
    const replies = useThread(podId, channel.id, root.id, visible);
    const posted = (line: Line) => {
        cache.setQueryData<LinePage>(threadKey(podId, channel.id, root.id), (was) => was ? { ...was, items: [...was.items, line] } : was);
        /* The first line counts its replies. */
        void cache.invalidateQueries({ queryKey: linesKey(podId, channel.id) });
    };
    return (
        <aside className="cside chthread" aria-label="Thread">
            <header className="cside__head">
                <h2>Thread</h2>
                <button type="button" className="icon-button" aria-label="Close the thread" onClick={onClose}><CloseIcon size={16} /></button>
            </header>
            <div className="chthread__body">
                <ol className="cday__lines">
                    <LineView podId={podId} channel={channel} line={root} />
                </ol>
                <p className="chthread__count">
                    {replies.isPending ? "Loading replies…" : replies.isError ? "Couldn’t load the replies." : (replies.data?.items.length ?? 0) === 1 ? "1 reply" : (replies.data?.items.length ?? 0) + " replies"}
                    {replies.isError && <> <button type="button" className="gsheet__inline" onClick={() => void replies.refetch()}>Try again</button></>}
                </p>
                <ol className="cday__lines">
                    {(replies.data?.items ?? []).map((line) => <LineView key={line.id} podId={podId} channel={channel} line={line} />)}
                </ol>
            </div>
            <Composer podId={podId} channel={channel} people={people} threadRootId={root.id} placeholder="Reply in the thread" onPosted={posted} />
        </aside>
    );
}
