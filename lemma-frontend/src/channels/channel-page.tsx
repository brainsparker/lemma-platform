"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { isForbidden, isMissing } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { ChannelIcon } from "@/shell/channels";
import { InfoIcon } from "@/ui/icons";
import { ChannelPanel } from "./channel-panel";
import { Composer } from "./composer";
import { mentionables } from "./mentions";
import { channelTitle, platformsOf, whoAnswers, withLine, type ChannelDetail, type Line, type Page } from "./model";
import { linesKey, refreshLists, useChannel, useLines, useMarkRead } from "./queries";
import { platformName } from "./sources";
import { ThreadPanel } from "./thread-panel";
import { Timeline } from "./timeline";

/** One channel, in one place: everything said there, oldest at the top, the
 *  box to say something, and -- beside it -- either a thread or what is true
 *  about the channel. Read again every few seconds while it is on screen, and
 *  marked read as far as the newest line you have seen. */
export function ChannelPage({ pod, channelId, visible, onNamed, onOpenChannels }: {
    pod: Pod;
    channelId: string;
    visible: boolean;
    /** The page knows the channel's name before the tab does. */
    onNamed: (title: string) => void;
    onOpenChannels: () => void;
}) {
    const detail = useChannel(pod.id, channelId, visible);
    const channel = detail.data ?? null;
    const title = channel ? channelTitle(channel, pod.name) : null;
    useEffect(() => { if (title) onNamed(title); }, [title, onNamed]);

    if (detail.isPending) return <div className="cpage"><p className="cpage__quiet" role="status">Loading the channel…</p></div>;
    /* A read that fails after one worked keeps the page it had. */
    if (!channel) {
        const gone = isMissing(detail.error);
        return (
            <div className="cpage">
                <div className="cpage__gone" role="alert">
                    <p>{gone ? "This channel is not here any more." : isForbidden(detail.error) ? "You may not open this channel." : "Couldn’t load this channel."}</p>
                    <div className="cpage__gone-acts">
                        {!gone && !isForbidden(detail.error) && <button type="button" className="ghost-pill" onClick={() => void detail.refetch()}>Try again</button>}
                        <button type="button" className="ghost-pill" onClick={onOpenChannels}>All channels</button>
                    </div>
                </div>
            </div>
        );
    }
    return <ChannelView key={channel.id} pod={pod} channel={channel} title={title ?? ""} visible={visible} onOpenChannels={onOpenChannels} />;
}

function ChannelView({ pod, channel, title, visible, onOpenChannels }: {
    pod: Pod;
    channel: ChannelDetail;
    title: string;
    visible: boolean;
    onOpenChannels: () => void;
}) {
    const me = useMe();
    const cache = useQueryClient();
    const timeline = useLines(pod.id, channel.id, visible);
    const agents = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const [side, setSide] = useState<"about" | "thread" | null>(null);
    const [threadRoot, setThreadRoot] = useState<string | null>(null);
    const [pin, setPin] = useState(0);
    const newest = timeline.lines.at(-1)?.id ?? null;
    useMarkRead(pod.id, channel.id, newest, visible);

    const people = useMemo(
        () => mentionables(agents.data ?? [], channel.participants.map((one) => one.person), me),
        [agents.data, channel.participants, me],
    );
    const root = threadRoot ? timeline.lines.find((line) => line.id === threadRoot) ?? null : null;
    const platforms = platformsOf(channel);
    const posted = (line: Line) => {
        cache.setQueryData<InfiniteData<Page<Line>>>(linesKey(pod.id, channel.id), (was) => was ? { ...was, pages: withLine(was.pages, line) } : was);
        setPin((count) => count + 1);
        refreshLists(cache, pod.id);
    };
    const placeholder = channel.kind === "DIRECT" ? "Write to " + pod.name
        : channel.kind === "CONTACT" ? "Write to " + (channel.person?.name ?? "them")
        : "Write in " + title;

    return (
        <div className="cpage" data-side={side ?? undefined}>
            <div className="cpage__main">
                <header className="cpage__head">
                    <div className="cpage__who">
                        <h1>{title}</h1>
                        <p>
                            {platforms.length > 0 && (
                                <span className="cpage__places">
                                    {platforms.map((platform) => (
                                        <span key={platform} className="chrow__chip"><ChannelIcon platform={platform} size={13} /> {platformName(platform)}</span>
                                    ))}
                                </span>
                            )}
                            <span>{channel.topic ?? whoAnswers(channel, pod.name, me)}</span>
                        </p>
                    </div>
                    <button
                        type="button"
                        className="icon-button"
                        aria-label="About this channel"
                        title="About this channel"
                        aria-pressed={side === "about"}
                        onClick={() => setSide((was) => (was === "about" ? null : "about"))}
                    >
                        <InfoIcon size={19} />
                    </button>
                </header>
                {channel.archived && <p className="cpage__note" role="status">This channel is archived. It still reads and writes; bring it back from About to list it again.</p>}
                {channel.kind === "CONTACT" && channel.attention && !channel.handler && (
                    <p className="cpage__note cpage__note--ask" role="status">
                        {channel.person?.name ?? "This contact"} asks for a person{channel.attention.reason ? ": " + channel.attention.reason : "."}{" "}
                        <button type="button" className="gsheet__inline" onClick={() => setSide("about")}>Step in</button>
                    </p>
                )}
                <Timeline
                    podId={pod.id}
                    channel={channel}
                    lines={timeline.lines}
                    loading={timeline.isPending}
                    failed={timeline.isError && !timeline.data}
                    hasOlder={Boolean(timeline.hasNextPage)}
                    loadingOlder={timeline.isFetchingNextPage}
                    onOlder={() => void timeline.fetchNextPage()}
                    onRetry={() => void timeline.refetch()}
                    onThread={(line) => { setThreadRoot(line.id); setSide("thread"); }}
                    pin={pin}
                />
                <Composer podId={pod.id} channel={channel} people={people} placeholder={placeholder} onPosted={posted} />
            </div>
            {side === "thread" && root && (
                <ThreadPanel podId={pod.id} channel={channel} root={root} people={people} visible={visible} onClose={() => { setSide(null); setThreadRoot(null); }} />
            )}
            {side === "about" && <ChannelPanel pod={pod} channel={channel} onClose={() => setSide(null)} onLeft={onOpenChannels} />}
        </div>
    );
}
