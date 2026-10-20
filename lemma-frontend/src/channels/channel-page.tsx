"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { isForbidden, isMissing } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { ChannelIcon } from "@/shell/channels";
import { InfoIcon } from "@/ui/icons";
import { channelAbilities } from "./abilities";
import { AgentSwitch } from "./agent-switch";
import { ChannelPanel } from "./channel-panel";
import { Composer } from "./composer";
import { ContactControls } from "./contact-controls";
import { onFocusAsked, takeFocus, type LineFocus } from "./focus";
import { answeringNews, sayTyping, typingIn, upsertTimeline } from "./live";
import { channelReaders, mentionables } from "./mentions";
import { channelTitle, newestSeq, personName, platformsOf, whoAnswers, type ChannelDetail, type Line, type LinePage } from "./model";
import { linesKey, refreshLists, useChannel, useLines } from "./queries";
import { readTarget, unseenThreadMention, usePageShown } from "./read-state";
import { platformName } from "./sources";
import { MentionablesContext } from "./mention-picker";
import { ThreadPanel } from "./thread-panel";
import { Timeline } from "./timeline";
import { useAbilities } from "./use-abilities";
import { useChannelLive } from "./use-channel-live";
import { useInbox } from "./waiting-panel";

/** One channel, in one place: everything said there, oldest at the top, the
 *  box to say something, and -- beside it -- either a thread or what is true
 *  about the channel. Kept live by the pod's socket while it is on screen,
 *  and marked read as far as the newest line the reader has seen. */
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
                    <p>{gone ? "This channel is not here any more, or you are not in it." : isForbidden(detail.error) ? "You may not open this channel." : "Couldn’t load this channel."}</p>
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
    const live = useChannelLive(pod.id, channel, visible, me);
    const may = channelAbilities(useAbilities(pod.id), channel, pod.name);
    const agents = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const [side, setSide] = useState<"about" | "thread" | null>(null);
    const [threadRoot, setThreadRoot] = useState<string | null>(null);
    /* Asked each time the reader opens a thread to reply in it: their next
       words go there, not into the channel's box. */
    const [replyAsked, setReplyAsked] = useState(0);
    const [pin, setPin] = useState(0);
    const [highlight, setHighlight] = useState<LineFocus | null>(null);
    /* Where the unread lines began when it was opened: the "New" mark
       stays there while the reader reads, rather than chasing them. */
    const [unreadFrom] = useState(channel.me.readSeq);
    const shown = usePageShown();
    const inbox = useInbox(pod.id, visible);

    /* Opened at one line, now or while already open. */
    useEffect(() => {
        const take = () => {
            const asked = takeFocus(channel.id);
            if (!asked) return;
            setHighlight(asked);
            if (asked.threadRootId) { setThreadRoot(asked.threadRootId); setSide("thread"); setReplyAsked(0); }
        };
        if (visible) take();
        return onFocusAsked((id) => { if (id === channel.id) take(); });
    }, [channel.id, visible]);

    /* The line asked for may be older than the pages held: read back until
       it is there, a few pages at most. */
    const focusSeq = useRef(0);
    useEffect(() => {
        const wanted = highlight?.threadRootId ?? highlight?.lineId;
        if (!wanted || timeline.isPending || timeline.lines.some((line) => line.id === wanted)) return;
        if (timeline.hasNextPage && !timeline.isFetchingNextPage && focusSeq.current < 8) {
            focusSeq.current += 1;
            void timeline.fetchNextPage();
        }
    }, [highlight, timeline]);

    /* Read as far as the newest line seen; not while reading back, not
       behind another tab, and not past a reply naming the reader in a
       thread they have not opened. */
    const heldBack = unseenThreadMention(inbox.data?.mentioned ?? [], channel.id, side === "thread" ? threadRoot : null);
    const marked = useRef(0);
    const [progress, setProgress] = useState({ seenMax: 0, atEnd: true });
    const onProgress = useCallback((next: { seenMax: number; atEnd: boolean }) => {
        setProgress((was) => (was.seenMax === next.seenMax && was.atEnd === next.atEnd ? was : next));
    }, []);
    const { markRead } = live;
    useEffect(() => {
        /* The inbox says which thread mentions are unread; until it has,
           what reading to the end may mark is not known. */
        if (!visible || !shown || timeline.isPending || inbox.isPending) return;
        const target = readTarget({
            seenMax: progress.seenMax,
            atEnd: progress.atEnd,
            newestShown: newestSeq(timeline.lines) ?? 0,
            lastSeq: channel.lastSeq,
            readSeq: channel.me.readSeq,
            marked: marked.current,
            heldBack,
        });
        if (target === null) return;
        const was = marked.current;
        marked.current = target;
        markRead(target).catch(() => { marked.current = was; });
    }, [visible, shown, progress, timeline.isPending, timeline.lines, channel.lastSeq, channel.me.readSeq, markRead, heldBack, inbox.isPending]);

    /* A reply naming the reader is read in its thread, not the timeline:
       while that thread is open on screen, it is read to the newest line. */
    const openThreadNamesMe = side === "thread" && threadRoot !== null
        && (inbox.data?.mentioned ?? []).some((one) => one.channelId === channel.id && one.threadRootId === threadRoot);
    useEffect(() => {
        if (!visible || !shown || !openThreadNamesMe || !threadRoot) return;
        markRead(channel.lastSeq, threadRoot).catch(() => { /* read again when the inbox next says so */ });
    }, [visible, shown, openThreadNamesMe, threadRoot, channel.lastSeq, markRead]);

    const people = useMemo(
        () => mentionables(agents.data ?? [], channel.members.map((one) => one.person), me, channelReaders(channel, pod.members)),
        [agents.data, channel, pod.members, me],
    );
    const agentName = useCallback((agentId: string | null) => {
        const member = channel.members.find((one) => one.person.kind === "AGENT" && one.person.id === agentId);
        return member?.person.name ?? agents.data?.find((agent) => agent.id === agentId)?.label ?? pod.teammate?.name ?? "An agent";
    }, [channel.members, agents.data, pod.teammate]);
    const root = threadRoot ? timeline.lines.find((line) => line.id === threadRoot) ?? null : null;
    const platforms = platformsOf(channel);
    const posted = (line: Line) => {
        cache.setQueryData<InfiniteData<LinePage>>(linesKey(pod.id, channel.id), (was) => was ? { ...was, pages: upsertTimeline(was.pages, line).pages } : was);
        setPin((count) => count + 1);
        setHighlight(null);
        refreshLists(cache, pod.id);
    };
    const placeholder = channel.kind === "DIRECT" ? "Write to " + pod.name
        : channel.kind === "CONTACT" ? "Write to " + (channel.person?.name ?? "them")
        : "Write in " + title;
    const typingNames = typingIn(live.typing, null).map((one) => personName(one.who, me));
    const threadTyping = root ? typingIn(live.typing, root.id).map((one) => personName(one.who, me)) : [];

    /* What a screen reader hears: a new line from somebody else, and an
       answer starting -- never the answer a piece at a time. */
    const [said, setSaid] = useState("");
    useEffect(() => { if (live.announced) setSaid(live.announced); }, [live.announced]);
    const heardAnswering = useRef(live.answering);
    useEffect(() => {
        const started = answeringNews(heardAnswering.current, live.answering);
        heardAnswering.current = live.answering;
        if (started.length) setSaid(started.map((one) => agentName(one.agentId)).join(", ") + (started.length === 1 ? " is answering" : " are answering"));
    }, [live.answering, agentName]);

    return (
        /* Who the box a line is changed in can name: as the composer's. */
        <MentionablesContext.Provider value={people}>
        <div className="cpage" data-side={side ?? undefined}>
            <div className="cpage__main">
                <header className="cpage__head">
                    <div className="cpage__who">
                        <h1>{title}</h1>
                        <p>
                            {channel.kind === "GROUP" && channel.visibility === "PRIVATE" && <span className="chrow__chip">Private</span>}
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
                    {channel.kind === "CONTACT" && may.contactOps && (
                        <div className="cpage__contact">
                            <ContactControls pod={pod} channel={channel} compact />
                            <AgentSwitch podId={pod.id} channel={channel} />
                        </div>
                    )}
                    <button
                        type="button"
                        className="icon-button"
                        aria-label="About this channel"
                        title="About this channel"
                        aria-expanded={side === "about"}
                        onClick={() => setSide((was) => (was === "about" ? null : "about"))}
                    >
                        <InfoIcon size={19} />
                    </button>
                </header>
                {channel.archived && <p className="cpage__note" role="status">This channel is archived. Bring it back from About to write in it and list it again.</p>}
                {channel.kind === "CONTACT" && channel.attention && !channel.assignee && (
                    <p className="cpage__note cpage__note--ask" role="status">
                        {channel.person?.name ?? "This contact"} is waiting on a person{channel.attention.reason ? ": " + channel.attention.reason : "."}{" "}
                        {may.write ? "Write below to answer them." : ""}
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
                    onThread={(line) => { setThreadRoot(line.id); setSide("thread"); setReplyAsked((was) => was + 1); }}
                    pin={pin}
                    visible={visible}
                    mayWrite={may.write}
                    answering={live.answering}
                    agentName={agentName}
                    highlight={highlight ? highlight.threadRootId ?? highlight.lineId : null}
                    announced={said}
                    onProgress={onProgress}
                    unreadFrom={unreadFrom}
                    me={me}
                />
                <p className="cpage__typing" aria-hidden={typingNames.length === 0}>{sayTyping(typingNames)}</p>
                <Composer
                    podId={pod.id}
                    channel={channel}
                    people={people}
                    placeholder={placeholder}
                    disabledReason={channel.archived ? "This channel is archived. Bring it back to write in it." : may.writeRefused}
                    onPosted={posted}
                    onTyping={() => live.typed(null)}
                />
            </div>
            {side === "thread" && root && (
                <ThreadPanel
                    key={root.id}
                    podId={pod.id}
                    channel={channel}
                    root={root}
                    people={people}
                    visible={visible}
                    mayWrite={may.write && !channel.archived}
                    writeRefused={may.writeRefused}
                    answering={live.answering}
                    agentName={agentName}
                    highlight={highlight?.threadRootId ? highlight.lineId : null}
                    typing={threadTyping}
                    replyAsked={replyAsked}
                    onTyping={live.typed}
                    onClose={() => { setSide(null); setThreadRoot(null); }}
                />
            )}
            {side === "about" && <ChannelPanel pod={pod} channel={channel} may={may} onClose={() => setSide(null)} onLeft={onOpenChannels} />}
        </div>
        </MentionablesContext.Provider>
    );
}
