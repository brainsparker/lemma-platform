"use client";

import { useMemo } from "react";
import { useInfiniteQuery, useQuery, type InfiniteData, type QueryClient } from "@tanstack/react-query";
import { pollWhenDown, useLiveStatus } from "@/live/pod-live";
import { isForbidden, isMissing } from "@/session/auth-state";
import { channelApi } from "./api";
import { joinPages, type Channel, type ChannelDetail, type LinePage, type Page } from "./model";
import type { ListFilter } from "./views";

/** The keys every view of a pod's channels reads under. All of them start
 *  `["channel", podId]`, so an answer given from the bell -- which drops that
 *  prefix -- is read again everywhere it shows. */
export const channelsKey = (podId: string, filter: ListFilter) => ["channel", podId, "list", filter] as const;
export const channelKey = (podId: string, channelId: string) => ["channel", podId, "detail", channelId] as const;
export const linesKey = (podId: string, channelId: string) => ["channel", podId, "lines", channelId] as const;
export const threadKey = (podId: string, channelId: string, rootId: string) => ["channel", podId, "thread", channelId, rootId] as const;
export const sourceKey = (podId: string, sourceId: string) => ["channel", podId, "source", sourceId] as const;
/** The react-query key everything about one pod's inbox reads under. */
export const inboxKey = (podId: string) => ["inbox", podId] as const;
export const deliveriesKey = (podId: string, channelId: string, lineId: string) => ["channel", podId, "deliveries", channelId, lineId] as const;

/** How often an open channel is read again over REST -- only while the live
 *  socket is down. While it is up, the socket says what changed. */
export const READ_EVERY_MS = 10_000;

const settled = (count: number, error: unknown) => !isMissing(error) && !isForbidden(error) && count < 3;

/** The pod's channels, most recently active first, a page at a time. `poll`
 *  overrides the fallback, for a sheet waiting on a group to arrive. */
export function useChannelList(podId: string, filter: ListFilter, poll: number | false | null = null) {
    const status = useLiveStatus(podId);
    return useInfiniteQuery({
        queryKey: channelsKey(podId, filter),
        queryFn: ({ pageParam }) => channelApi(podId).list({ ...filter, before: pageParam }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (last: Page<Channel>) => last.nextBefore ?? undefined,
        staleTime: 15_000,
        refetchInterval: poll ?? pollWhenDown(status, READ_EVERY_MS * 3),
        retry: settled,
    });
}

/** Every page read so far, as one list. */
export function allChannels(data: InfiniteData<Page<Channel>> | undefined): Channel[] {
    const seen = new Set<string>();
    return (data?.pages ?? []).flatMap((page) => page.items).filter((channel) => !seen.has(channel.id) && Boolean(seen.add(channel.id)));
}

export function useChannel(podId: string, channelId: string, visible: boolean) {
    const status = useLiveStatus(podId);
    return useQuery({
        queryKey: channelKey(podId, channelId),
        queryFn: () => channelApi(podId).get(channelId),
        refetchInterval: visible ? pollWhenDown(status, READ_EVERY_MS * 3) : false,
        staleTime: 10_000,
        retry: settled,
    });
}

/** A channel's timeline: the newest page first, older pages as asked for. */
export function useLines(podId: string, channelId: string, visible: boolean) {
    const status = useLiveStatus(podId);
    const query = useInfiniteQuery({
        queryKey: linesKey(podId, channelId),
        queryFn: ({ pageParam }) => channelApi(podId).lines(channelId, pageParam),
        initialPageParam: undefined as number | undefined,
        getNextPageParam: (last: LinePage) => last.nextBefore ?? undefined,
        refetchInterval: visible ? pollWhenDown(status, READ_EVERY_MS) : false,
        /* The socket keeps it; a remount does not need to read it again. */
        staleTime: status === "open" ? 60_000 : 5_000,
        retry: settled,
    });
    const lines = useMemo(() => joinPages(query.data?.pages ?? []), [query.data]);
    return { ...query, lines };
}

export function useThread(podId: string, channelId: string, rootId: string, visible: boolean) {
    const status = useLiveStatus(podId);
    return useQuery({
        queryKey: threadKey(podId, channelId, rootId),
        queryFn: () => channelApi(podId).thread(channelId, rootId),
        refetchInterval: visible ? pollWhenDown(status, READ_EVERY_MS) : false,
        staleTime: 5_000,
    });
}

export function useSourceSettings(podId: string, channelId: string, sourceId: string, enabled = true) {
    return useQuery({
        queryKey: sourceKey(podId, sourceId),
        queryFn: () => channelApi(podId).source(channelId, sourceId),
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

/** The group channels one bot is in, each with the bot's settings for its
 *  place there (`GET /channels?source_surface=`). An admin is also listed
 *  the bot's private groups they are not in, to join on request. */
export function useBotGroups(podId: string, surface: { id: string; name: string }, enabled = true) {
    return useQuery({
        queryKey: ["channel", podId, "bot-groups", surface.id] as const,
        enabled,
        staleTime: 30_000,
        queryFn: () => channelApi(podId).sources(surface.name),
    });
}
