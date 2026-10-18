import { platformName } from "./sources";
import type { PersonRef, WaitingQuestion } from "./waiting";

/** A pod's channels as the app draws them, read off the wire and said.
 *
 *  A channel is one shared timeline: `DIRECT` is the reader and the pod (their
 *  own; nobody else's is listed), `CONTACT` one contact and the pod, `GROUP` a
 *  chat several people read. It may be fed by outside places -- a WhatsApp
 *  group, a Slack channel, an email thread -- or by none. Everybody in one is
 *  a `PersonRef`: a member, a contact or an agent.
 *
 *  Pure, so the test runner can hold every sentence the pages say to what the
 *  API actually sent. */

export type ChannelKind = "DIRECT" | "CONTACT" | "GROUP";
export type NotifyLevel = "ALL" | "MENTIONS" | "NONE";

export interface Unread {
    lines: number;
    mentions: number;
    waiting: number;
}

export interface SourceSummary {
    platform: string;
    title: string | null;
}

export interface Channel {
    id: string;
    kind: ChannelKind;
    title: string | null;
    topic: string | null;
    /** Whose it is: the member's for a DIRECT one, the contact's for a
     *  CONTACT one; null for a group. */
    person: PersonRef | null;
    sources: SourceSummary[];
    /** A member answering by hand; its agents do not answer meanwhile. */
    handler: PersonRef | null;
    /** It asks for a person, and why. */
    attention: { since: string; reason: string | null } | null;
    archived: boolean;
    lastLine: { sender: PersonRef; text: string; createdAt: string } | null;
    lastMessageAt: string | null;
    createdAt: string;
    unread: Unread;
}

/** One outside place feeding a channel. */
export interface SourcePlace {
    id: string;
    platform: string;
    kind: "PRIVATE" | "SHARED";
    title: string | null;
    surfaceId: string | null;
    pending: boolean;
    lastInboundAt: string | null;
}

export interface Participant {
    person: PersonRef;
    notify: NotifyLevel;
    lastReadAt: string | null;
}

export interface ChannelDetail extends Channel {
    places: SourcePlace[];
    participants: Participant[];
    me: { joined: boolean; notify: NotifyLevel; lastReadAt: string | null };
}

export interface Interaction {
    kind: "ASK" | "APPROVAL" | "FORM";
    state: "OPEN" | "ANSWERED" | "CANCELLED" | "EXPIRED";
    question: Record<string, unknown>;
    /** The one person who may answer it. */
    awaits: PersonRef | null;
    answer: Record<string, unknown> | null;
    answeredBy: PersonRef | null;
    answeredAt: string | null;
}

export interface Line {
    id: string;
    sender: PersonRef;
    /** For an agent's line: whom it acted for, with whose access. */
    onBehalfOf: PersonRef | null;
    /** Null where the line is withheld from the reader, or deleted. */
    text: string | null;
    createdAt: string;
    editedAt: string | null;
    deleted: boolean;
    withheld: boolean;
    membersOnly: boolean;
    /** Where it was said; null for a line written in Lemma. */
    sourceId: string | null;
    threadRootId: string | null;
    replyCount: number;
    interaction: Interaction | null;
}

export interface Page<T> {
    items: T[];
    nextBefore: string | null;
}

/* ── off the wire ──────────────────────────────────────────────────── */

type Wire = Record<string, unknown>;

const record = (value: unknown): Wire => (value && typeof value === "object" ? (value as Wire) : {});

function words(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value : null;
}

function count(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function rows(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

const KINDS: readonly ChannelKind[] = ["DIRECT", "CONTACT", "GROUP"];
const LEVELS: readonly NotifyLevel[] = ["ALL", "MENTIONS", "NONE"];

function level(value: unknown, fallback: NotifyLevel = "ALL"): NotifyLevel {
    return LEVELS.includes(value as NotifyLevel) ? (value as NotifyLevel) : fallback;
}

export function readPerson(raw: unknown): PersonRef | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Wire;
    const kind = row.kind === "CONTACT" || row.kind === "AGENT" ? row.kind : "MEMBER";
    return { kind, id: words(row.id), name: words(row.name) };
}

const SOMEBODY: PersonRef = { kind: "MEMBER", id: null, name: null };

/** One channel off a list. Null without the id everything about it is keyed by. */
export function readChannel(raw: unknown): Channel | null {
    const row = record(raw);
    const id = words(row.id);
    if (!id) return null;
    const attention = row.attention && typeof row.attention === "object" ? record(row.attention) : null;
    const last = row.last_line && typeof row.last_line === "object" ? record(row.last_line) : null;
    const unread = record(row.unread);
    return {
        id,
        kind: KINDS.includes(row.kind as ChannelKind) ? (row.kind as ChannelKind) : "GROUP",
        title: words(row.title),
        topic: words(row.topic),
        person: readPerson(row.person),
        sources: rows(row.sources).flatMap((entry) => {
            const source = record(entry);
            const platform = words(source.platform);
            return platform ? [{ platform: platform.toUpperCase(), title: words(source.title) }] : [];
        }),
        handler: readPerson(row.handler),
        attention: attention ? { since: String(attention.since ?? ""), reason: words(attention.reason) } : null,
        archived: row.archived === true,
        lastLine: last ? {
            sender: readPerson(last.sender) ?? SOMEBODY,
            text: typeof last.text === "string" ? last.text : "",
            createdAt: String(last.created_at ?? ""),
        } : null,
        lastMessageAt: words(row.last_message_at),
        createdAt: String(row.created_at ?? ""),
        unread: { lines: count(unread.lines), mentions: count(unread.mentions), waiting: count(unread.waiting) },
    };
}

export function readChannelPage(raw: unknown): Page<Channel> {
    const body = record(raw);
    return {
        items: rows(body.items).map(readChannel).filter((channel): channel is Channel => channel !== null),
        nextBefore: words(body.next_before),
    };
}

export function readChannelDetail(raw: unknown): ChannelDetail | null {
    const channel = readChannel(raw);
    if (!channel) return null;
    const row = record(raw);
    const me = record(row.me);
    return {
        ...channel,
        places: rows(row.source_places).flatMap((entry) => {
            const place = record(entry);
            const id = words(place.id);
            if (!id) return [];
            return [{
                id,
                platform: (words(place.platform) ?? "").toUpperCase(),
                kind: place.kind === "SHARED" ? "SHARED" as const : "PRIVATE" as const,
                title: words(place.title),
                surfaceId: words(place.surface_id),
                pending: place.pending === true,
                lastInboundAt: words(place.last_inbound_at),
            }];
        }),
        participants: rows(row.participants).flatMap((entry) => {
            const participant = record(entry);
            const person = readPerson(participant.person);
            return person ? [{ person, notify: level(participant.notify), lastReadAt: words(participant.last_read_at) }] : [];
        }),
        me: { joined: me.joined !== false, notify: level(me.notify), lastReadAt: words(me.last_read_at) },
    };
}

function readInteraction(raw: unknown): Interaction | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Wire;
    const kind = row.kind === "APPROVAL" || row.kind === "FORM" ? row.kind : row.kind === "ASK" ? "ASK" : null;
    if (!kind) return null;
    const state = row.state === "ANSWERED" || row.state === "CANCELLED" || row.state === "EXPIRED" ? row.state : "OPEN";
    return {
        kind,
        state,
        question: record(row.question),
        awaits: readPerson(row.awaits),
        answer: row.answer && typeof row.answer === "object" ? (row.answer as Wire) : null,
        answeredBy: readPerson(row.answered_by),
        answeredAt: words(row.answered_at),
    };
}

export function readLine(raw: unknown): Line | null {
    const row = record(raw);
    const id = words(row.id);
    if (!id) return null;
    const deleted = row.deleted === true;
    const withheld = row.withheld === true;
    return {
        id,
        sender: readPerson(row.sender) ?? SOMEBODY,
        onBehalfOf: readPerson(row.on_behalf_of),
        /* A withheld or deleted line keeps no text even if some arrived. */
        text: deleted || withheld ? null : typeof row.text === "string" ? row.text : null,
        createdAt: String(row.created_at ?? ""),
        editedAt: words(row.edited_at),
        deleted,
        withheld,
        membersOnly: row.members_only === true,
        sourceId: words(row.source_id),
        threadRootId: words(row.thread_root_id),
        replyCount: count(row.reply_count),
        interaction: readInteraction(row.interaction),
    };
}

export function readLinePage(raw: unknown): Page<Line> {
    const body = record(raw);
    return {
        items: rows(body.items).map(readLine).filter((line): line is Line => line !== null),
        nextBefore: words(body.next_before),
    };
}

/** The pages of a timeline as react-query holds them -- newest first, each
 *  oldest first inside -- as one run, oldest first, each line once. */
export function joinPages(pages: readonly Page<Line>[]): Line[] {
    const seen = new Set<string>();
    const out: Line[] = [];
    for (const page of [...pages].reverse()) {
        for (const line of page.items) {
            if (seen.has(line.id)) continue;
            seen.add(line.id);
            out.push(line);
        }
    }
    return out;
}

/** A line written or changed here, put where it belongs in the pages already
 *  held: replaced where it is, or added to the newest page. */
export function withLine(pages: readonly Page<Line>[], line: Line): Page<Line>[] {
    if (pages.some((page) => page.items.some((one) => one.id === line.id))) {
        return pages.map((page) => ({ ...page, items: page.items.map((one) => (one.id === line.id ? line : one)) }));
    }
    const [newest, ...rest] = pages;
    return newest ? [{ ...newest, items: [...newest.items, line] }, ...rest] : [{ items: [line], nextBefore: null }];
}

/* ── what a channel is called ──────────────────────────────────────── */

/** What to call a person, to the reader: "You" for themselves. */
export function personName(person: PersonRef | null, me: string | null): string {
    if (!person) return "Someone";
    if (me && person.kind === "MEMBER" && person.id === me) return "You";
    if (person.name) return person.name;
    return person.kind === "AGENT" ? "An agent" : person.kind === "CONTACT" ? "A contact" : "A member";
}

/** A channel's name. The reader's own is them and the space; a contact's is
 *  the contact; a group is what it was called, or where it is. */
export function channelTitle(channel: Pick<Channel, "kind" | "title" | "person" | "sources">, space: string): string {
    if (channel.kind === "DIRECT") return "You and " + space;
    if (channel.kind === "CONTACT") return channel.person?.name ?? channel.title ?? "A contact";
    return channel.title ?? channel.sources.find((source) => source.title)?.title ?? "Untitled channel";
}

/** The platforms feeding a channel, each once, in the order they came. */
export function platformsOf(channel: Pick<Channel, "sources">): string[] {
    return [...new Set(channel.sources.map((source) => source.platform.toUpperCase()))];
}

/** The last line, as a row's second line: "Priya: Thursday works". */
export function previewOf(channel: Pick<Channel, "lastLine" | "topic">, me: string | null): string {
    const last = channel.lastLine;
    if (!last) return channel.topic ?? "Nothing said yet.";
    const text = last.text.replace(/\s+/g, " ").trim();
    return personName(last.sender, me) + ": " + (text || "…");
}

/** The three ways a list is cut: the reader's own, the groups, and -- for
 *  an editor -- the contacts. A section with nothing in it is left out. */
export interface Section {
    key: "direct" | "groups" | "contacts";
    label: string;
    channels: Channel[];
}

export function sectionsOf(channels: readonly Channel[], maySeeContacts: boolean): Section[] {
    const of = (kind: ChannelKind) => channels.filter((channel) => channel.kind === kind);
    const sections: Section[] = [
        { key: "direct", label: "Your channel", channels: of("DIRECT") },
        { key: "groups", label: "Groups", channels: of("GROUP") },
    ];
    if (maySeeContacts) sections.push({ key: "contacts", label: "Contacts", channels: of("CONTACT") });
    return sections.filter((section) => section.channels.length > 0);
}

/** What is new in a channel, for a row's accessible name. */
export function sayUnread(unread: Unread): string | null {
    const parts: string[] = [];
    if (unread.lines) parts.push(unread.lines >= 100 ? "100 or more new" : unread.lines + " new");
    if (unread.mentions) parts.push(unread.mentions === 1 ? "mentions you" : unread.mentions + " mention you");
    if (unread.waiting) parts.push(unread.waiting === 1 ? "a question waiting on you" : unread.waiting + " questions waiting on you");
    return parts.length ? parts.join(", ") : null;
}

/** A count for a badge: nothing, the number, or "99+". */
export function badge(count: number): string | null {
    if (count <= 0) return null;
    return count > 99 ? "99+" : String(count);
}

/** What the sidebar's badge counts and says: questions first, since only the
 *  reader can answer them; then mentions; then channels with anything new. */
export function inboxBadge(totals: { channels: number; mentions: number; waiting: number }): { count: number; tone: "waiting" | "mention" | "new"; said: string } | null {
    if (totals.waiting > 0) return { count: totals.waiting, tone: "waiting", said: totals.waiting === 1 ? "1 question waiting on you" : totals.waiting + " questions waiting on you" };
    if (totals.mentions > 0) return { count: totals.mentions, tone: "mention", said: totals.mentions === 1 ? "1 mention of you" : totals.mentions + " mentions of you" };
    if (totals.channels > 0) return { count: totals.channels, tone: "new", said: totals.channels === 1 ? "1 channel with something new" : totals.channels + " channels with something new" };
    return null;
}

/** When something happened, short enough for a column: 12 min, 2 h,
 *  Yesterday, 3 days, then the date. */
export function sinceShort(iso: string | null | undefined, now = new Date()): string {
    if (!iso) return "";
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const minutes = Math.floor((now.getTime() - at.getTime()) / 60_000);
    if (minutes < 1) return "Now";
    if (minutes < 60) return minutes + " min";
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + " h";
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const day = new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
    const days = Math.round((today - day) / 86_400_000);
    if (days <= 1) return "Yesterday";
    if (days < 7) return days + " days";
    return at.toLocaleDateString([], at.getFullYear() === now.getFullYear()
        ? { day: "numeric", month: "short" }
        : { day: "numeric", month: "short", year: "numeric" });
}

/* ── one line ──────────────────────────────────────────────────────── */

/** Who wrote a line, as the reader sees it. An agent's line says whom it
 *  acted for: "Lem, for Priya". */
export function senderLabel(line: Pick<Line, "sender" | "onBehalfOf">, me: string | null): string {
    const name = personName(line.sender, me);
    if (line.sender.kind !== "AGENT" || !line.onBehalfOf) return name;
    const whom = personName(line.onBehalfOf, me);
    return name + ", for " + (whom === "You" ? "you" : whom);
}

/** Where a line was said: "#acme (Slack)", "WhatsApp". Nothing for a line
 *  written in Lemma, or from a place the reader cannot see. */
export function sourceLabel(sourceId: string | null, places: readonly SourcePlace[]): string | null {
    if (!sourceId) return null;
    const place = places.find((candidate) => candidate.id === sourceId);
    if (!place) return null;
    const platform = platformName(place.platform);
    return place.title ? place.title + " (" + platform + ")" : platform;
}

/** A line's small print after its sender: where, and when. */
export function lineMeta(line: Pick<Line, "sourceId" | "createdAt">, places: readonly SourcePlace[]): string {
    return [sourceLabel(line.sourceId, places), clockTime(line.createdAt)].filter(Boolean).join(" · ");
}

/** "10:02", in the reader's own clock. */
export function clockTime(iso: string): string {
    const at = new Date(iso);
    return Number.isNaN(at.getTime()) ? "" : at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** What stands where a withheld line's text would be: whom the agent
 *  answered with their own access, and so that only they can read it. */
export function withheldNote(line: Pick<Line, "withheld" | "onBehalfOf">, me: string | null): string | null {
    if (!line.withheld) return null;
    const whom = line.onBehalfOf ? personName(line.onBehalfOf, me) : null;
    return whom && whom !== "You"
        ? "Answered " + whom + " — only they can read it"
        : "Answered someone with their own access — only they can read it";
}

/** Whether the reader may change or delete a line: their own, still there. */
export function isMine(line: Pick<Line, "sender" | "deleted" | "withheld">, me: string | null): boolean {
    return Boolean(me && line.sender.kind === "MEMBER" && line.sender.id === me && !line.deleted && !line.withheld);
}

/** Where a question on a line stands, to the reader. */
export type QuestionState =
    | { kind: "yours" }
    | { kind: "waiting"; on: string }
    | { kind: "answered"; by: string | null }
    | { kind: "closed"; why: "CANCELLED" | "EXPIRED" };

export function questionState(interaction: Interaction, me: string | null): QuestionState {
    if (interaction.state === "ANSWERED") return { kind: "answered", by: interaction.answeredBy ? personName(interaction.answeredBy, me) : null };
    if (interaction.state === "CANCELLED" || interaction.state === "EXPIRED") return { kind: "closed", why: interaction.state };
    const awaits = interaction.awaits;
    if (awaits && me && awaits.kind === "MEMBER" && awaits.id === me) return { kind: "yours" };
    return { kind: "waiting", on: awaits ? personName(awaits, me) : "someone" };
}

/** What a question that is not the reader's to answer says. */
export function sayQuestion(state: QuestionState): string | null {
    switch (state.kind) {
        case "yours": return null;
        case "waiting": return "Waiting on " + state.on;
        case "answered": return state.by ? "Answered by " + (state.by === "You" ? "you" : state.by) : "Answered";
        case "closed": return state.why === "EXPIRED" ? "No longer waiting: it expired" : "No longer waiting";
    }
}

/** What an answer said, in a few words: the choice, the words, or the
 *  decision. A form's fields are not repeated on the timeline. */
export function answerSummary(answer: Record<string, unknown> | null): string | null {
    if (!answer) return null;
    if (typeof answer.choice === "string" && answer.choice) return answer.choice;
    if (typeof answer.text === "string" && answer.text.trim()) return answer.text.trim();
    if (typeof answer.decision === "string") return answer.decision === "DENY" ? "Denied" : "Approved";
    return null;
}

/** A question on a line, in the shape the waiting bell answers. */
export function asWaitingQuestion(line: Line, channel: Pick<Channel, "id" | "kind" | "title">, me: string | null): WaitingQuestion | null {
    if (!line.interaction) return null;
    return {
        id: line.id,
        channelId: channel.id,
        channelTitle: channel.title,
        channelKind: channel.kind,
        kind: line.interaction.kind,
        text: line.text ?? "",
        from: senderLabel(line, me),
        askedAt: line.createdAt,
        question: line.interaction.question,
    };
}

/** The timeline, cut at each day, the days named the way a chat names them. */
export function lineDays<T extends { createdAt: string }>(lines: readonly T[], now = new Date()): { key: string; label: string; lines: T[] }[] {
    const days: { key: string; label: string; lines: T[] }[] = [];
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    for (const line of lines) {
        const at = new Date(line.createdAt);
        if (Number.isNaN(at.getTime())) continue;
        const day = new Date(at.getFullYear(), at.getMonth(), at.getDate());
        const key = day.toDateString();
        const last = days.at(-1);
        if (last?.key === key) { last.lines.push(line); continue; }
        const back = Math.round((today - day.getTime()) / 86_400_000);
        const label = back === 0 ? "Today" : back === 1 ? "Yesterday"
            : at.toLocaleDateString([], at.getFullYear() === now.getFullYear()
                ? { weekday: "long", day: "numeric", month: "long" }
                : { day: "numeric", month: "long", year: "numeric" });
        days.push({ key, label, lines: [line] });
    }
    return days;
}

/** Two letters for a face. */
export function initials(name: string): string {
    const parts = name.replace(/^[@+]+/, "").split(/[\s._-]+/).filter(Boolean);
    const letters = parts.slice(0, 2).map((word) => word[0] ?? "").join("");
    return (letters || name.slice(0, 1) || "?").toUpperCase();
}

/** What a composer in this channel offers. A note to members is only a thing
 *  where something outside would otherwise hear it; a choice of place only
 *  where there is more than one, and only of places that exist yet. */
export function composerChoices(detail: Pick<ChannelDetail, "places">): { notes: boolean; places: SourcePlace[] } {
    const live = detail.places.filter((place) => !place.pending);
    return { notes: detail.places.length > 0, places: live.length > 1 ? live : [] };
}

/** Who answers in a channel when nobody is named. In the reader's own the
 *  pod's agent answers every line; anywhere else an agent answers only when
 *  @mentioned, and not at all while a member has stepped in. */
export function whoAnswers(channel: Pick<Channel, "kind" | "handler">, space: string, me: string | null): string {
    if (channel.handler) {
        const name = personName(channel.handler, me);
        return name === "You" ? "You are answering here; its agents wait." : name + " is answering here; its agents wait.";
    }
    if (channel.kind === "DIRECT") return space + " answers everything you write here.";
    if (channel.kind === "CONTACT") return space + "’s agents answer them here.";
    return "@mention an agent to have it answer.";
}

/** The members of the space not in the channel yet, by user id. */
export function addable<M extends { userId?: string; kind?: string }>(members: readonly M[], participants: readonly Participant[]): M[] {
    const inIt = new Set(participants.filter((one) => one.person.kind === "MEMBER").map((one) => one.person.id));
    return members.filter((member) => member.userId && member.kind !== "teammate" && !inIt.has(member.userId));
}
