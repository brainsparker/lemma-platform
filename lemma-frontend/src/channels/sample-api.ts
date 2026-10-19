import {
    SAMPLE_CHANNEL_LINES,
    SAMPLE_CHANNEL_THREADS,
    SAMPLE_CHANNELS,
    SAMPLE_SOURCE_SETTINGS,
} from "@/data/fixtures";
import type { ChannelApi } from "./api";
import { readChannelDetail, readChannelPage, readLine, readLinePage, type Line } from "./model";
import { readSourceSettings } from "./sources";

/** The sample's channels, answering the same calls the API does from the
 *  fixtures, and keeping what is written in them for as long as the tab is
 *  open: a line posted stays posted, a group started arrives. Anything the
 *  sample cannot honestly do -- answer an agent, carry a line to WhatsApp --
 *  it does not pretend to. */

type Wire = Record<string, unknown>;

const ME = { kind: "MEMBER", id: "sample-user", name: "Dana Jones" };

let channels: Wire[] = SAMPLE_CHANNELS.map((channel) => ({ ...channel }));
const lines = new Map<string, Wire[]>(Object.entries(SAMPLE_CHANNEL_LINES).map(([id, list]) => [id, [...list]]));
const threads = new Map<string, Wire[]>(Object.entries(SAMPLE_CHANNEL_THREADS).map(([id, list]) => [id, [...list]]));
const settings = new Map<string, Wire>(Object.entries(SAMPLE_SOURCE_SETTINGS).map(([id, row]) => [id, { ...row }]));

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const now = () => new Date().toISOString();
const id = (prefix: string) => prefix + "-" + Math.random().toString(16).slice(2, 10);

function missing(what: string): Error {
    return Object.assign(new Error("That " + what + " is not here any more."), { statusCode: 404 });
}

function sampleOnly(): Error {
    return Object.assign(new Error("This is the sample source — connect a session to change that."), { statusCode: 422 });
}

function channelOf(channelId: string): Wire {
    const found = channels.find((channel) => channel.id === channelId);
    if (!found) throw missing("channel");
    return found;
}

function save(channelId: string, change: Wire): Wire {
    channels = channels.map((channel) => (channel.id === channelId ? { ...channel, ...change } : channel));
    return channelOf(channelId);
}

/* A thread's first line counts its replies, as the API counts them. */
function withReplies(line: Wire): Wire {
    const replies = threads.get(String(line.id));
    return replies ? { ...line, reply_count: replies.length, last_reply_at: replies.at(-1)?.created_at ?? null } : line;
}

function lastLineOf(channelId: string): Wire | null {
    const last = lines.get(channelId)?.at(-1);
    if (!last) return null;
    return { id: last.id, seq: last.seq, sender: last.sender, text: typeof last.text === "string" ? last.text : "", created_at: last.created_at };
}

/* The next line's place in a channel, as the API numbers them. */
function nextSeq(channel: Wire): number {
    return (typeof channel.last_seq === "number" ? channel.last_seq : 0) + 1;
}

function listed(channel: Wire): Wire {
    return { ...channel, last_line: lastLineOf(String(channel.id)) };
}

/* WhatsApp confirms a group it was asked for a few seconds later; so does the
   sample, the first time anything reads it after that. */
const readyAt = new Map<string, number>();
function settle(): void {
    for (const [sourceId, at] of readyAt) {
        if (at > Date.now()) continue;
        readyAt.delete(sourceId);
        const row = settings.get(sourceId);
        if (!row) continue;
        settings.set(sourceId, { ...row, pending: false, invite_link: "https://chat.whatsapp.com/" + sourceId.replace(/[^0-9a-z]/gi, "").slice(0, 10) + "Lm" });
        const channel = channels.find((one) => one.id === row.channel_id);
        if (channel) {
            save(String(channel.id), {
                source_places: (channel.source_places as Wire[]).map((place) => (place.id === sourceId ? { ...place, pending: false } : place)),
            });
        }
    }
}

export function sampleChannelApi(_podId: string): ChannelApi {
    return {
        async list(options) {
            await wait(120);
            settle();
            const wanted = channels.filter((channel) => (channel.archived === true) === (options.archived === true)
                && (!options.kind || channel.kind === options.kind)
                && (!options.contactId || options.contactId.endsWith(String((channel.person as Wire | null)?.id ?? "\u0000"))));
            const sorted = [...wanted].sort((a, b) => String(b.last_line_at ?? b.created_at).localeCompare(String(a.last_line_at ?? a.created_at)));
            return readChannelPage({ items: sorted.map(listed), next_cursor: null });
        },
        async get(channelId) {
            await wait(100);
            settle();
            const detail = readChannelDetail(listed(channelOf(channelId)));
            if (!detail) throw missing("channel");
            return detail;
        },
        async lines(channelId, beforeSeq) {
            await wait(140);
            channelOf(channelId);
            return readLinePage({ items: beforeSeq ? [] : (lines.get(channelId) ?? []).map(withReplies), has_more: false, next_before_seq: null });
        },
        async thread(_channelId, rootId) {
            await wait(120);
            return readLinePage({ items: threads.get(rootId) ?? [], has_more: false });
        },
        async post(channelId, body) {
            await wait(250);
            const channel = channelOf(channelId);
            const line: Wire = {
                id: id("line"), seq: nextSeq(channel), sender: ME, text: body.text, created_at: now(),
                members_only: body.membersOnly === true,
                source_id: body.membersOnly ? null : body.sourceId ?? (channel.source_places as Wire[] | undefined)?.[0]?.id ?? null,
                thread_root_id: body.threadRootId ?? null,
            };
            if (body.threadRootId) threads.set(body.threadRootId, [...(threads.get(body.threadRootId) ?? []), line]);
            else lines.set(channelId, [...(lines.get(channelId) ?? []), line]);
            save(channelId, { last_line_at: line.created_at, last_seq: line.seq });
            return readLine(line) as Line;
        },
        async edit(channelId, lineId, text) {
            await wait(200);
            const list = lines.get(channelId) ?? [];
            const was = list.find((line) => line.id === lineId);
            if (!was) throw missing("line");
            const edited = { ...was, text, edited_at: now() };
            lines.set(channelId, list.map((line) => (line.id === lineId ? edited : line)));
            return readLine(withReplies(edited)) as Line;
        },
        async remove(channelId, lineId) {
            await wait(200);
            lines.set(channelId, (lines.get(channelId) ?? []).map((line) => (line.id === lineId ? { ...line, text: null, deleted: true } : line)));
        },
        async markRead(channelId, seq) {
            const channel = channels.find((one) => one.id === channelId);
            if (!channel) return;
            const upto = Math.min(seq, nextSeq(channel) - 1);
            save(channelId, {
                unread: { ...(channel.unread as Wire), lines: 0, mentions: 0, read_seq: upto },
                me: { ...(channel.me as Wire), read_seq: upto },
            });
        },
        async create(body) {
            await wait(300);
            const made: Wire = {
                id: id("sample-channel"), kind: "GROUP", title: body.title, topic: body.topic, person: null,
                sources: [], source_places: [],
                visibility: "public",
                members: [{ person: ME, role: "owner" }, ...body.members.map((member) => ({ person: { kind: "MEMBER", id: member, name: null }, role: "member" }))],
                me: { joined: true, notify: "ALL", read_seq: 0, role: "owner", can_manage: true }, unread: { lines: 0, mentions: 0, waiting: 0, read_seq: 0 },
                created_at: now(), last_line_at: now(), last_seq: 0,
            };
            channels = [made, ...channels];
            lines.set(String(made.id), []);
            return readChannelDetail(made)!;
        },
        async update(channelId, body) {
            await wait(250);
            const change: Wire = {};
            if (body.title !== undefined) change.title = body.title;
            if (body.topic !== undefined) change.topic = body.topic;
            if (body.archived !== undefined) change.archived = body.archived;
            return readChannelDetail(listed(save(channelId, change)))!;
        },
        async addMembers() { throw sampleOnly(); },
        async removeMember() { throw sampleOnly(); },
        async leave() { throw sampleOnly(); },
        async setNotify(channelId, notify) {
            await wait(200);
            const channel = channelOf(channelId);
            return readChannelDetail(listed(save(channelId, { me: { ...(channel.me as Wire), notify } })))!;
        },
        async setAgentAnswers(channelId, on) {
            await wait(250);
            return readChannelDetail(listed(save(channelId, {
                agents_answer: on,
                ...(on ? { assignee: null } : {}),
            })))!;
        },
        async sources(surfaceName) {
            await wait(120);
            settle();
            return [...settings.values()].flatMap((row) => {
                const read = row.surface_name === surfaceName ? readSourceSettings(row) : null;
                return read ? [read] : [];
            });
        },
        async source(sourceId) {
            await wait(120);
            settle();
            const row = readSourceSettings(settings.get(sourceId));
            if (!row) throw missing("group");
            return row;
        },
        async updateSource(sourceId, change) {
            await wait(300);
            const was = settings.get(sourceId);
            if (!was) throw missing("group");
            const owner = change.take_over || (change.answers_outsiders && !was.owner) ? ME : was.owner;
            const answers = typeof change.answers_outsiders === "boolean" ? change.answers_outsiders : was.answers_outsiders === true;
            const saved = { ...was, owner, answers_outsiders: answers, welcomes_outsiders: answers && Boolean(owner) && was.bot_answers_outsiders !== false };
            settings.set(sourceId, saved);
            return readSourceSettings(saved)!;
        },
        async startWhatsAppGroup(body) {
            await wait(600);
            const channelId = id("sample-wa");
            const sourceId = id("src-wa");
            const title = body.title.slice(0, 128);
            channels = [{
                id: channelId, kind: "GROUP", title, topic: null, person: null,
                sources: [{ platform: "WHATSAPP", title }],
                source_places: [{ id: sourceId, platform: "WHATSAPP", kind: "SHARED", title, surface_id: "s-whatsapp", pending: true, last_inbound_at: null }],
                visibility: "private", members: [{ person: ME, role: "owner" }],
                me: { joined: true, notify: "ALL", read_seq: 0, role: "owner", can_manage: true },
                unread: { lines: 0, mentions: 0, waiting: 0, read_seq: 0 }, created_at: now(), last_line_at: now(), last_seq: 0,
            }, ...channels];
            lines.set(channelId, []);
            settings.set(sourceId, {
                id: sourceId, channel_id: channelId, platform: "WHATSAPP", title, surface_name: body.surfaceName,
                answers_outsiders: body.answersOutsiders, welcomes_outsiders: body.answersOutsiders, bot_answers_outsiders: true,
                can_manage: true, owner: ME, invite_link: null, pending: true, shared_externally: false, public: null, updated_at: now(),
            });
            readyAt.set(sourceId, Date.now() + 4_000);
            return readSourceSettings(settings.get(sourceId))!;
        },
        /* The link is real Telegram's shape and goes nowhere useful; the group
           it would have added arrives a few seconds later, the way the bot's
           hello would bring it. */
        async telegramLink(surfaceName) {
            await wait(400);
            window.setTimeout(() => {
                const channelId = id("sample-tg");
                channels = [{
                    id: channelId, kind: "GROUP", title: "Supplier chat", topic: null, person: null,
                    sources: [{ platform: "TELEGRAM", title: "Supplier chat" }],
                    source_places: [{ id: id("src-tg"), platform: "TELEGRAM", kind: "SHARED", title: "Supplier chat", surface_id: "s-telegram", pending: false, last_inbound_at: now() }],
                    visibility: "private", members: [{ person: ME, role: "owner" }],
                    me: { joined: true, notify: "ALL", read_seq: 0, role: "owner", can_manage: true },
                    unread: { lines: 1, mentions: 0, waiting: 0, read_seq: 0 }, created_at: now(), last_line_at: now(), last_seq: 1,
                }, ...channels];
                lines.set(channelId, [{ id: id("line"), seq: 1, sender: { kind: "AGENT", id: "a-default", name: "Lem" }, text: "Hi, I’m Lem. Mention me or reply to me to ask something.", created_at: now() }]);
            }, 6_000);
            return { url: "https://t.me/" + surfaceName.replace(/^@/, "") + "_bot?startgroup=sample", expiresAt: new Date(Date.now() + 3_600_000).toISOString() };
        },
    };
}
