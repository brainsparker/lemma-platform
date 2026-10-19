import { MentionPart, NotifyLevel as WireNotifyLevel } from "lemma-sdk";
import { source } from "@/data";
import { lemma } from "@/session/client";
import {
    readChannelDetail,
    readChannelPage,
    readLine,
    readLinePage,
    type Channel,
    type ChannelDetail,
    type ChannelKind,
    type Line,
    type LinePage,
    type NotifyLevel,
    type Page,
} from "./model";
import type { PersonRef } from "./waiting";
import { readSourceSettings, type SourceSettings } from "./sources";
import type { Mention } from "./mentions";

/** Everything the Channels place asks of the API, read into what it draws.
 *
 *  One seam, so the sample source can answer the same calls from its
 *  fixtures: channels are the caller's own view of the pod -- what is unread
 *  is theirs -- and were never part of `PodSource`. */
export interface ChannelApi {
    list(options: { kind?: ChannelKind; archived?: boolean; contactId?: string; before?: string; limit?: number }): Promise<Page<Channel>>;
    get(channelId: string): Promise<ChannelDetail>;
    /** The newest page of top-level lines, or the one before line `beforeSeq`. */
    lines(channelId: string, beforeSeq?: number): Promise<LinePage>;
    /** A thread's replies, oldest first. */
    thread(channelId: string, rootId: string): Promise<LinePage>;
    post(channelId: string, body: PostBody): Promise<Line>;
    edit(channelId: string, lineId: string, text: string): Promise<Line>;
    remove(channelId: string, lineId: string): Promise<void>;
    /** The reader has seen the channel up to line `seq`. */
    markRead(channelId: string, seq: number): Promise<void>;
    create(body: { title: string; topic: string | null; members: string[] }): Promise<ChannelDetail>;
    update(channelId: string, body: { title?: string | null; topic?: string | null; archived?: boolean }): Promise<ChannelDetail>;
    addMembers(channelId: string, people: string[]): Promise<ChannelDetail>;
    /** Take somebody out of a group. */
    removeMember(channelId: string, member: Pick<PersonRef, "kind"> & { id: string }): Promise<void>;
    /** The reader leaves a channel they are in. */
    leave(channelId: string): Promise<void>;
    setNotify(channelId: string, notify: NotifyLevel): Promise<ChannelDetail>;
    /** A contact's channel: switch its agent on (handing them back now) or off. */
    setAgentAnswers(channelId: string, on: boolean): Promise<ChannelDetail>;
    /** Every group one bot is in, with its settings. */
    sources(surfaceName: string): Promise<SourceSettings[]>;
    source(sourceId: string): Promise<SourceSettings>;
    updateSource(sourceId: string, change: { answers_outsiders?: boolean; take_over?: boolean }): Promise<SourceSettings>;
    startWhatsAppGroup(body: { surfaceName: string; title: string; answersOutsiders: boolean }): Promise<SourceSettings>;
    telegramLink(surfaceName: string): Promise<{ url: string; expiresAt: string }>;
}

export interface PostBody {
    text: string;
    membersOnly?: boolean;
    sourceId?: string | null;
    threadRootId?: string | null;
    mentions?: Mention[];
    /** The composer's name for this line: a retried send is the same line. */
    clientMessageId?: string | null;
}

function must<T>(value: T | null, what: string): T {
    if (value === null) throw new Error("The " + what + " came back without an id.");
    return value;
}

function liveApi(podId: string): ChannelApi {
    const client = () => lemma(podId);
    const detail = (raw: unknown) => must(readChannelDetail(raw), "channel");
    const settings = (raw: unknown) => must(readSourceSettings(raw), "group");
    return {
        async list({ before, ...options }) {
            return readChannelPage(await client().channels.list(podId, { ...options, cursor: before }));
        },
        async get(channelId) {
            return detail(await client().channels.get(podId, channelId));
        },
        async lines(channelId, beforeSeq) {
            return readLinePage(await client().channels.messages(podId, channelId, { limit: 50, beforeSeq }));
        },
        async thread(channelId, rootId) {
            return readLinePage(await client().channels.thread(podId, channelId, rootId, { limit: 100 }));
        },
        async post(channelId, body) {
            return must(readLine(await client().channels.post(podId, channelId, {
                text: body.text,
                members_only: body.membersOnly ?? false,
                source_id: body.sourceId ?? null,
                thread_root_id: body.threadRootId ?? null,
                parts: (body.mentions ?? []).map((mention) => ({ type: "mention", id: mention.id, kind: MentionPart.kind[mention.kind] })),
                client_message_id: body.clientMessageId ?? null,
            })), "line");
        },
        async edit(channelId, lineId, text) {
            return must(readLine(await client().channels.edit(podId, channelId, lineId, text)), "line");
        },
        async remove(channelId, lineId) {
            await client().channels.deleteMessage(podId, channelId, lineId);
        },
        async markRead(channelId, seq) {
            await client().channels.markRead(podId, channelId, seq);
        },
        async create(body) {
            return detail(await client().channels.create(podId, body));
        },
        async update(channelId, body) {
            return detail(await client().channels.update(podId, channelId, body));
        },
        async addMembers(channelId, people) {
            return detail(await client().channels.addMembers(podId, channelId, { people }));
        },
        async removeMember(channelId, member) {
            await client().channels.removeMember(podId, channelId, member.kind, member.id);
        },
        async leave(channelId) {
            await client().channels.leave(podId, channelId);
        },
        async setNotify(channelId, notify) {
            return detail(await client().channels.updateMyMembership(podId, channelId, { notify: WireNotifyLevel[notify] }));
        },
        async setAgentAnswers(channelId, on) {
            return detail(await client().channels.setAgentAnswers(podId, channelId, on));
        },
        async sources(surfaceName) {
            const listed = await client().podSources.list(podId, surfaceName);
            return listed.items.flatMap((row) => {
                const read = readSourceSettings(row);
                return read ? [read] : [];
            });
        },
        async source(sourceId) {
            return settings(await client().podSources.get(podId, sourceId));
        },
        async updateSource(sourceId, change) {
            return settings(await client().podSources.update(podId, sourceId, change));
        },
        async startWhatsAppGroup(body) {
            return settings(await client().podSources.startWhatsAppGroup(podId, {
                surface_name: body.surfaceName,
                title: body.title,
                answers_outsiders: body.answersOutsiders,
            }));
        },
        async telegramLink(surfaceName) {
            const link = await client().podSources.telegramLink(podId, surfaceName);
            return { url: link.url, expiresAt: link.expires_at };
        },
    };
}

/** The sample's answers, loaded only when asked: the fixtures are large, and a
 *  live session never needs them. */
function sampleApi(podId: string): ChannelApi {
    return new Proxy({} as ChannelApi, {
        get: (_target, name) => async (...args: unknown[]) => {
            const { sampleChannelApi } = await import("./sample-api");
            const api = sampleChannelApi(podId) as unknown as Record<string, (...given: unknown[]) => Promise<unknown>>;
            return api[String(name)](...args);
        },
    });
}

export function channelApi(podId: string): ChannelApi {
    return source.label === "sample" ? sampleApi(podId) : liveApi(podId);
}
