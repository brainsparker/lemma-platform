"use client";

import { useEffect, useMemo, useRef } from "react";
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData, type QueryClient } from "@tanstack/react-query";
import { isForbidden, isMissing } from "@/session/auth-state";
import { channelApi } from "./api";
import { joinPages, type Channel, type ChannelDetail, type Line, type Page } from "./model";
import { inboxKey } from "./waiting-panel";

/** The keys every view of a pod's channels reads under. All of them start
 *  `["channel", podId]`, so an answer given from the bell -- which drops that
 *  prefix -- is read again everywhere it shows. */
export const channelsKey = (podId: string, archived: boolean) => ["channel", podId, "list", archived] as const;
export const channelKey = (podId: string, channelId: string) => ["channel", podId, "detail", channelId] as const;
export const linesKey = (podId: string, channelId: string) => ["channel", podId, "lines", channelId] as const;
export const threadKey = (podId: string, channelId: string, rootId: string) => ["channel", podId, "thread", channelId, rootId] as const;
export const sourceKey = (podId: string, sourceId: string) => ["channel", podId, "source", sourceId] as const;

/** How often an open channel is read again while it is on screen: people
 *  talk, agents answer, and a question can arrive while you look. */
export const READ_EVERY_MS = 10_000;

const settled = (count: number, error: unknown) => !isMissing(error) && !isForbidden(error) && count < 3;

/** The pod's channels, most recently active first, a page at a time. */
export function useChannelList(podId: string, archived: boolean, poll: number | false = false) {
    return useInfiniteQuery({
        queryKey: channelsKey(podId, archived),
        queryFn: ({ pageParam }) => channelApi(podId).list({ archived, before: pageParam }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (last: Page<Channel>) => last.nextBefore ?? undefined,
        staleTime: 15_000,
        refetchInterval: poll,
        retry: settled,
    });
}

/** Every page read so far, as one list. */
export function allChannels(data: InfiniteData<Page<Channel>> | undefined): Channel[] {
    const seen = new Set<string>();
    return (data?.pages ?? []).flatMap((page) => page.items).filter((channel) => !seen.has(channel.id) && Boolean(seen.add(channel.id)));
}

export function useChannel(podId: string, channelId: string, visible: boolean) {
    return useQuery({
        queryKey: channelKey(podId, channelId),
        queryFn: () => channelApi(podId).get(channelId),
        refetchInterval: visible ? READ_EVERY_MS * 3 : false,
        staleTime: 10_000,
        retry: settled,
    });
}

/** A channel's timeline: the newest page first, older pages as asked for. */
export function useLines(podId: string, channelId: string, visible: boolean) {
    const query = useInfiniteQuery({
        queryKey: linesKey(podId, channelId),
        queryFn: ({ pageParam }) => channelApi(podId).lines(channelId, pageParam),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (last: Page<Line>) => last.nextBefore ?? undefined,
        refetchInterval: visible ? READ_EVERY_MS : false,
        staleTime: 5_000,
        retry: settled,
    });
    const lines = useMemo(() => joinPages(query.data?.pages ?? []), [query.data]);
    return { ...query, lines };
}

export function useThread(podId: string, channelId: string, rootId: string, visible: boolean) {
    return useQuery({
        queryKey: threadKey(podId, channelId, rootId),
        queryFn: () => channelApi(podId).thread(channelId, rootId),
        refetchInterval: visible ? READ_EVERY_MS : false,
        staleTime: 5_000,
    });
}

export function useSourceSettings(podId: string, sourceId: string, enabled = true) {
    return useQuery({
        queryKey: sourceKey(podId, sourceId),
        queryFn: () => channelApi(podId).source(sourceId),
        enabled,
        staleTime: 30_000,
        retry: settled,
    });
}

/** After anything about a channel changed: its lists and the inbox say what
 *  is new, so both are read again. */
export function refreshLists(cache: QueryClient, podId: string): void {
    void cache.invalidateQueries({ queryKey: ["channel", podId, "list"] });
    void cache.invalidateQueries({ queryKey: inboxKey(podId) });
}

/** The channel as saved, written where it is held, and the lists read again. */
export function keepChannel(cache: QueryClient, podId: string, saved: ChannelDetail): void {
    cache.setQueryData(channelKey(podId, saved.id), saved);
    refreshLists(cache, podId);
}

/** Marks the channel read as far as the newest line on screen, once per new
 *  line, while it is on screen -- and tells the lists and the inbox. */
export function useMarkRead(podId: string, channelId: string, newest: string | null, visible: boolean): void {
    const cache = useQueryClient();
    const marked = useRef<string | null>(null);
    useEffect(() => {
        if (!visible || !newest || marked.current === newest) return;
        marked.current = newest;
        channelApi(podId).markRead(channelId)
            .then(() => refreshLists(cache, podId))
            .catch(() => { marked.current = null; });
    }, [podId, channelId, newest, visible, cache]);
}

/** The group channels one bot is in, each with the place it is fed from
 *  there. The API lists sources only by channel, so this reads the group
 *  channels fed from the bot's platform and keeps those whose place is this
 *  bot's -- a few reads, on a settings page, rather than a list of its own. */
export function useBotGroups(podId: string, surface: { id: string; name: string }, enabled = true) {
    return useQuery({
        queryKey: ["channel", podId, "bot-groups", surface.id] as const,
        enabled,
        staleTime: 30_000,
        queryFn: () => channelApi(podId).sources(surface.name),
    });
}
