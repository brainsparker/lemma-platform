import test from "node:test";
import assert from "node:assert/strict";
import {
    answeringStep,
    mergeChannel,
    readChannelEvent,
    readInboxEvent,
    upsertThread,
    upsertTimeline,
    withChannelUnread,
    withInboxTotals,
    withLastLine,
    withReadSeq,
    type Answering,
    type ChannelEvent,
} from "../src/channels/live.ts";
import { joinPages, readChannel, readChannelDetail, readLine, type Line, type LinePage } from "../src/channels/model.ts";
import { readInbox } from "../src/channels/waiting.ts";

/** What the live socket says about a channel, applied to what the views
 *  hold: as upserts, so a repeat after a resume changes nothing. */

const priya = { kind: "MEMBER", id: "user-priya", name: "Priya" };
const line = (seq: number, extra: Record<string, unknown> = {}): Line => readLine({ id: "l" + seq, seq, sender: priya, text: "line " + seq, created_at: "2026-10-19T10:00:00Z", ...extra })!;
const page = (seqs: number[], nextBefore: number | null = null): LinePage => ({ items: seqs.map((seq) => line(seq)), nextBefore });

test("reads each event the channel topic sends, and ignores one it does not know", () => {
    const created = readChannelEvent({ type: "line.created", message: { id: "l9", seq: 9, sender: priya, text: "Ship it", created_at: "x" } });
    assert.equal(created?.type, "line.created");
    assert.equal(created && "line" in created ? created.line.text : null, "Ship it");
    assert.deepEqual(readChannelEvent({ type: "read.updated", channel_id: "c", read_seq: 12 }), { type: "read.updated", readSeq: 12 });
    assert.deepEqual(readChannelEvent({ type: "typing", channel_id: "c", user: priya }), { type: "typing", user: { kind: "MEMBER", id: "user-priya", name: "Priya" } });
    assert.deepEqual(readChannelEvent({ type: "line.streaming", channel_id: "c", message_id: "l9", agent_id: "a", agent_run_id: "r", kind: "text", delta: "Hi" }),
        { type: "line.streaming", messageId: "l9", agentId: "a", kind: "text", delta: "Hi" });
    assert.deepEqual(readChannelEvent({ type: "question.closed", channel_id: "c", message_id: "l3", state: "EXPIRED" }), { type: "question.closed", messageId: "l3", state: "EXPIRED" });
    assert.equal(readChannelEvent({ type: "channel.updated", channel: { id: "c", kind: "GROUP", joined: true, created_at: "" } })?.type, "channel.updated");
    assert.equal(readChannelEvent({ type: "something.new" }), null);
    assert.equal(readChannelEvent({ type: "line.created", message: { text: "no id" } }), null);
});

test("a new line joins the newest page in seq order; the same line twice is one line", () => {
    const held = [page([5, 6]), page([3, 4], null)];
    const once = upsertTimeline(held, line(8));
    assert.equal(once.added, true);
    const again = upsertTimeline(once.pages, line(8, { text: "edited" }));
    assert.equal(again.added, false);
    const late = upsertTimeline(again.pages, line(7));
    assert.deepEqual(joinPages(late.pages).map((one) => [one.seq, one.text]), [[3, "line 3"], [4, "line 4"], [5, "line 5"], [6, "line 6"], [7, "line 7"], [8, "edited"]]);
});

test("an edit or delete of a line older than every page held waits for that page to be read", () => {
    const held = [page([20, 21], 20)];
    const result = upsertTimeline(held, line(4, { deleted: true }));
    assert.equal(result.added, false);
    assert.deepEqual(joinPages(result.pages).map((one) => one.seq), [20, 21]);
    /* With nothing older to read, the timeline is the whole channel. */
    assert.equal(upsertTimeline([page([2, 3])], line(1)).added, true);
});

test("a deleted line keeps its place, empty", () => {
    const result = upsertTimeline([page([1, 2])], line(2, { deleted: true, text: "gone" }));
    const [, two] = joinPages(result.pages);
    assert.equal(two.deleted, true);
    assert.equal(two.text, null);
});

test("a reply joins its thread in seq order, once", () => {
    const thread = upsertThread({ items: [line(2, { thread_root_id: "l1" })], nextBefore: null }, line(4, { thread_root_id: "l1" }));
    assert.deepEqual(thread.items.map((one) => one.seq), [2, 4]);
    assert.deepEqual(upsertThread(thread, line(4, { thread_root_id: "l1", text: "changed" })).items.map((one) => one.text), ["line 2", "changed"]);
    /* The thread's first line comes again as `line.edited` with its count. */
    const root = upsertTimeline([{ items: [line(1, { reply_count: 1 })], nextBefore: null }], line(1, { reply_count: 2 }));
    assert.equal(root.pages[0].items[0].replyCount, 2);
});

test("the channel as the list shows it is laid over the detail, which keeps its members and places", () => {
    const detail = readChannelDetail({
        id: "c", kind: "GROUP", title: "launch", created_at: "", members: [{ person: priya, role: "owner" }],
        source_places: [{ id: "s1", platform: "SLACK" }], me: { joined: true, read_seq: 4, notify: "ALL" },
    })!;
    const listed = readChannel({ id: "c", kind: "GROUP", title: "Launch week", created_at: "", visibility: "PRIVATE", joined: true, unread: { lines: 2, read_seq: 3 } })!;
    const merged = mergeChannel(detail, listed);
    assert.equal(merged.title, "Launch week");
    assert.equal(merged.visibility, "PRIVATE");
    assert.equal(merged.members.length, 1);
    assert.equal(merged.places[0].id, "s1");
    assert.equal(merged.me.readSeq, 4, "a read position never goes back");
});

test("the read position moves forward only, and clears what was unread", () => {
    const detail = readChannelDetail({ id: "c", kind: "GROUP", created_at: "", me: { read_seq: 5 }, unread: { lines: 3, mentions: 1, read_seq: 5 } })!;
    assert.equal(withReadSeq(detail, 3), detail);
    const read = withReadSeq(detail, 9);
    assert.equal(read.me.readSeq, 9);
    assert.deepEqual(read.unread, { lines: 0, mentions: 0, waiting: 0, readSeq: 9 });
});

test("an answer being written: started, written a piece at a time, started over, finished", () => {
    const steps: ChannelEvent[] = [
        { type: "answering.started", messageId: "l9", agentId: "lem" },
        { type: "line.streaming", messageId: "l9", agentId: "lem", kind: "text", delta: "Thurs" },
        { type: "line.streaming", messageId: "l9", agentId: "lem", kind: "thinking", delta: "hmm" },
        { type: "line.streaming", messageId: "l9", agentId: "lem", kind: "text", delta: "day" },
    ];
    let held: Answering[] = [];
    for (const step of steps) held = answeringStep(held, step);
    assert.deepEqual(held, [{ messageId: "l9", agentId: "lem", text: "Thursday" }]);
    held = answeringStep(held, { type: "answering.started", messageId: "l9", agentId: "lem" });
    assert.equal(held.length, 1, "a repeated start is one answer");
    held = answeringStep(held, { type: "line.streaming", messageId: "l9", agentId: "lem", kind: "stream_reset", delta: "" });
    assert.equal(held[0].text, "");
    /* Arriving mid-answer, the text so far replaces what was held. */
    held = answeringStep(held, { type: "line.streaming", messageId: "l9", agentId: "lem", kind: "snapshot", delta: "Thursday, after" });
    assert.equal(held[0].text, "Thursday, after");
    held = answeringStep(held, { type: "answering.started", messageId: "l9", agentId: "other" });
    held = answeringStep(held, { type: "answering.finished", messageId: "l9", agentId: "lem" });
    assert.deepEqual(held.map((one) => one.agentId), ["other"]);
});

test("the inbox's counts are absolute: each event is the truth for its channel and the totals", () => {
    const event = readInboxEvent({ type: "inbox.updated", channel_id: "c1", unread: { lines: 4, mentions: 1, waiting: 0, read_seq: 7 }, totals: { channels: 2, mentions: 1, waiting: 3 } });
    assert.deepEqual(event, { type: "inbox.updated", channelId: "c1", unread: { lines: 4, mentions: 1, waiting: 0, readSeq: 7 }, totals: { channels: 2, mentions: 1, waiting: 3 } });
    assert.deepEqual(readInboxEvent({ type: "question.waiting", channel_id: "c1", message_id: "q1", message: {} }), { type: "question.waiting", channelId: "c1", messageId: "q1" });
    assert.equal(readInboxEvent({ type: "inbox.updated" }), null);
    const inbox = withInboxTotals(readInbox({ totals: { channels: 0, mentions: 0, waiting: 0 } }), { channels: 2, mentions: 1, waiting: 3 });
    assert.deepEqual([inbox.channels, inbox.mentions, inbox.waiting], [2, 1, 3]);
    const rows = [readChannel({ id: "c1", kind: "GROUP", created_at: "" })!, readChannel({ id: "c2", kind: "GROUP", created_at: "" })!];
    const patched = withChannelUnread(rows, "c1", { lines: 4, mentions: 1, waiting: 0, readSeq: 7 });
    assert.deepEqual(patched.map((row) => row.unread.lines), [4, 0]);
});

test("a new line is a list row's preview and moves it to the top", () => {
    const rows = [readChannel({ id: "c1", kind: "GROUP", created_at: "", last_seq: 3 })!];
    const [row] = withLastLine(rows, "c1", line(4));
    assert.equal(row.lastSeq, 4);
    assert.equal(row.lastLine?.text, "line 4");
    assert.equal(withLastLine(rows, "c1", line(2))[0].lastSeq, 3, "an older line is not the newest");
});
