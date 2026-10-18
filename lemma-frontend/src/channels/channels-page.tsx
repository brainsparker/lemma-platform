"use client";

import { useMemo, useState } from "react";
import type { Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { ChannelIcon } from "@/shell/channels";
import { useSurfaces } from "@/shell/surfaces";
import { useMaySeeContacts } from "@/space/contact-queries";
import { ChatIcon, ContactsIcon, GroupsIcon } from "@/ui/icons";
import { BringIn, FirstWays } from "./bring-in";
import {
    badge,
    channelTitle,
    platformsOf,
    previewOf,
    sayUnread,
    sectionsOf,
    sinceShort,
    type Channel,
} from "./model";
import { NewChannel } from "./new-channel";
import { allChannels, useChannelList } from "./queries";
import { GroupSheets, openTelegramLink, type GroupSheet } from "./source-sheets";
import { groupStarters, platformName, type Starter } from "./sources";

/** A space's channels, as one place in it: your own with the space, the
 *  groups, and -- for an editor -- each contact's.
 *
 *  A row says what it is, where it is fed from, its last line, and what is
 *  new for you: lines, a mention, a question only you can answer. A contact
 *  asking for a person says so. New channel makes one in Lemma; Add to a
 *  group brings the space's bot into a WhatsApp, Telegram or Slack group,
 *  which arrives as a channel of its own. */
export function ChannelsPage({ pod, onOpenChannel, onConnect }: {
    pod: Pod;
    onOpenChannel: (channel: { id: string; title: string | null }) => void;
    /** The space's bots, to connect or fix one. */
    onConnect: () => void;
}) {
    const me = useMe();
    const [archived, setArchived] = useState(false);
    const list = useChannelList(pod.id, archived);
    const maySeeContacts = useMaySeeContacts(pod.id);
    const surfaces = useSurfaces(pod.id);
    const [sheet, setSheet] = useState<GroupSheet | null>(null);
    const [making, setMaking] = useState(false);
    const starters = useMemo(() => groupStarters(surfaces.data ?? []), [surfaces.data]);
    const channels = useMemo(() => allChannels(list.data), [list.data]);
    const sections = useMemo(() => sectionsOf(channels, maySeeContacts), [channels, maySeeContacts]);
    const hasGroups = channels.some((channel) => channel.kind === "GROUP");

    const start = (starter: Starter) => {
        if (!starter.surface || starter.state !== "ready") { onConnect(); return; }
        if (starter.platform === "WHATSAPP") setSheet({ kind: "whatsapp", surface: starter.surface });
        else if (starter.platform === "SLACK") setSheet({ kind: "slack", surface: starter.surface });
        else setSheet({ kind: "telegram", surface: starter.surface });
    };
    const open = (channelId: string) => {
        const known = channels.find((channel) => channel.id === channelId);
        onOpenChannel({ id: channelId, title: known ? channelTitle(known, pod.name) : null });
    };

    return (
        <div className="all channels">
            <header className="channels__head">
                <div className="channels__title">
                    <h1>Channels</h1>
                    <p>Where people in {pod.name}, its contacts and its agents talk.</p>
                </div>
                <div className="channels__acts">
                    <BringIn pod={pod} starters={starters} loading={surfaces.isPending} onStart={start} />
                    <button type="button" className="pill-button channels__new" onClick={() => setMaking(true)}>New channel</button>
                </div>
            </header>
            <div className="channels__filter" role="group" aria-label="Which channels">
                <button type="button" className="channels__tab" aria-pressed={!archived} onClick={() => setArchived(false)}>Open</button>
                <button type="button" className="channels__tab" aria-pressed={archived} onClick={() => setArchived(true)}>Archived</button>
            </div>

            {list.isPending ? (
                <p className="all__empty" role="status">Loading channels…</p>
            ) : !list.data ? (
                <div className="channels__trouble" role="alert">
                    <p>{isForbidden(list.error) ? "You may not see " + pod.name + "’s channels." : "Couldn’t load the channels."}</p>
                    {!isForbidden(list.error) && <button type="button" className="ghost-pill" onClick={() => void list.refetch()}>Try again</button>}
                </div>
            ) : sections.length === 0 ? (
                <p className="channels__quiet">{archived ? "Nothing is archived." : "No channels yet."}</p>
            ) : (
                sections.map((section) => (
                    <section key={section.key} className="channels__section" aria-labelledby={"channels-" + section.key}>
                        <h2 id={"channels-" + section.key}>{section.label}</h2>
                        <ul className="crows channels__rows">
                            {section.channels.map((channel) => (
                                <ChannelRow key={channel.id} channel={channel} space={pod.name} me={me} onOpen={() => open(channel.id)} />
                            ))}
                        </ul>
                    </section>
                ))
            )}
            {list.hasNextPage && (
                <button type="button" className="ghost-pill channels__more" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                    {list.isFetchingNextPage ? "Loading…" : "Show more"}
                </button>
            )}

            {list.data && !archived && !hasGroups && (
                <FirstWays
                    pod={pod}
                    starters={starters}
                    loading={surfaces.isPending}
                    onStart={start}
                    onTelegramNow={(surface) => setSheet({ kind: "telegram", surface, started: openTelegramLink(pod.id, surface.name) })}
                    onConnect={onConnect}
                />
            )}
            {sheet && <GroupSheets pod={pod} sheet={sheet} onClose={() => setSheet(null)} onOpenChannel={open} />}
            {making && <NewChannel pod={pod} onClose={() => setMaking(false)} onMade={(id) => { setMaking(false); open(id); }} />}
        </div>
    );
}

function KindMark({ channel }: { channel: Channel }) {
    const [platform] = platformsOf(channel);
    if (platform) return <ChannelIcon platform={platform} size={26} />;
    if (channel.kind === "CONTACT") return <ContactsIcon size={22} aria-hidden="true" />;
    if (channel.kind === "DIRECT") return <ChatIcon size={22} aria-hidden="true" />;
    return <GroupsIcon size={22} aria-hidden="true" />;
}

function ChannelRow({ channel, space, me, onOpen }: { channel: Channel; space: string; me: string | null; onOpen: () => void }) {
    const title = channelTitle(channel, space);
    const news = sayUnread(channel.unread);
    const count = badge(channel.unread.lines);
    const when = sinceShort(channel.lastMessageAt ?? channel.createdAt);
    return (
        <li className="crow chrow" data-unread={channel.unread.lines > 0 || undefined}>
            <span className="chrow__mark" aria-hidden="true"><KindMark channel={channel} /></span>
            <button type="button" className="crow__open chrow__open" onClick={onOpen} aria-label={news ? title + ", " + news : title}>
                <span className="chrow__top">
                    <span className="crow__name">{title}</span>
                    {platformsOf(channel).map((platform) => (
                        <span key={platform} className="chrow__chip">{platformName(platform)}</span>
                    ))}
                    {channel.kind === "CONTACT" && channel.attention && !channel.handler && (
                        <span className="chrow__chip chrow__chip--ask" title={channel.attention.reason ?? undefined}>Needs a person</span>
                    )}
                    {channel.handler && <span className="chrow__chip">{channel.handler.id === me ? "You are answering" : (channel.handler.name ?? "A member") + " is answering"}</span>}
                </span>
                <span className="crow__sub chrow__preview">{previewOf(channel, me)}</span>
            </button>
            <span className="chrow__side">
                <span className="crow__when">{when}</span>
                <span className="chrow__marks" aria-hidden="true">
                    {channel.unread.waiting > 0 && <span className="chrow__dot chrow__dot--wait" title="Waiting on you" />}
                    {channel.unread.mentions > 0 && <span className="chrow__at" title="Mentions you">@</span>}
                    {count && <span className="snav__count">{count}</span>}
                </span>
            </span>
        </li>
    );
}
