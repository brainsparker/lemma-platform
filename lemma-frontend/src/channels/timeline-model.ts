import type { Line } from "./model";

/** What the timeline works out about where the reader is: where the unread
 *  lines start, and what came in while they were reading back. Pure, so the
 *  test runner holds it without a page. */

const someoneElse = (line: Pick<Line, "sender">, me: string | null) => !(line.sender.kind === "MEMBER" && line.sender.id === me);

/** The first line the reader had not read when they opened the channel --
 *  the newest line past `readSeq` said by somebody else -- or null. The
 *  reader's own lines are never news to them. */
export function firstUnread(lines: readonly Pick<Line, "id" | "seq" | "sender">[], readSeq: number, me: string | null): string | null {
    for (const line of lines) if (line.seq > readSeq && someoneElse(line, me)) return line.id;
    return null;
}

/** Whether unread lines start before the oldest line held: reading back
 *  brings them. */
export function unreadBeforeHeld(lines: readonly Pick<Line, "seq">[], readSeq: number, hasOlder: boolean): boolean {
    const oldest = lines.find((line) => line.seq > 0)?.seq ?? null;
    return hasOlder && oldest !== null && readSeq + 1 < oldest;
}

/** How many lines from others came in after `sinceSeq`: the "new lines"
 *  pill while the reader is up the timeline. */
export function newSince(lines: readonly Pick<Line, "seq" | "sender">[], sinceSeq: number, me: string | null): number {
    let count = 0;
    for (const line of lines) if (line.seq > sinceSeq && someoneElse(line, me)) count += 1;
    return count;
}

/** The pill's words. */
export function sayNewLines(count: number): string {
    return count === 1 ? "1 new line" : count + " new lines";
}
