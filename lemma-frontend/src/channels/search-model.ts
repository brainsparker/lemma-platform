import { readLine, readPerson, type ChannelKind, type Line, type Page } from "./model";
import type { PersonRef } from "./waiting";

/** One line found by `GET /search`, in the channel it was said in -- only
 *  channels the reader may see, which the server decides. */
export interface SearchHit {
    line: Line;
    channel: { id: string; kind: ChannelKind; title: string | null; person: PersonRef | null };
    /** The server's plain-text cut of the line around the match, when it
     *  made one: the words searched for are marked here, not by the server. */
    snippet: string | null;
}

type Wire = Record<string, unknown>;
const record = (value: unknown): Wire => (value && typeof value === "object" ? (value as Wire) : {});

export function readSearchPage(raw: unknown): Page<SearchHit> {
    const body = record(raw);
    const items = Array.isArray(body.items) ? body.items : [];
    return {
        items: items.flatMap((entry): SearchHit[] => {
            const row = record(entry);
            const line = readLine(row.message);
            const channel = record(row.channel);
            if (!line || typeof channel.id !== "string" || !channel.id) return [];
            const kind = channel.kind === "DIRECT" || channel.kind === "CONTACT" ? channel.kind : "GROUP";
            return [{
                line,
                channel: {
                    id: channel.id, kind,
                    title: typeof channel.title === "string" && channel.title ? channel.title : null,
                    person: readPerson(channel.person),
                },
                snippet: typeof row.snippet === "string" && row.snippet.trim() ? row.snippet : null,
            }];
        }),
        nextBefore: typeof body.next_cursor === "string" && body.next_cursor ? body.next_cursor : null,
    };
}

/** The words of a line around what was searched for, the first match in the
 *  middle, cut to `width` -- as pieces, so a view can mark the matches
 *  without parsing HTML. */
export function snippet(text: string, query: string, width = 160): { text: string; match: boolean }[] {
    const flat = text.replace(/\s+/g, " ").trim();
    const words = query.toLowerCase().split(/\s+/).filter((word) => word.length > 0);
    if (!words.length) return [{ text: flat.slice(0, width), match: false }];
    const lower = flat.toLowerCase();
    const first = Math.min(...words.map((word) => lower.indexOf(word)).filter((at) => at >= 0), flat.length);
    const start = first > width / 2 && first < flat.length ? Math.max(0, first - Math.floor(width / 3)) : 0;
    const cut = (start > 0 ? "…" : "") + flat.slice(start, start + width) + (start + width < flat.length ? "…" : "");
    const pattern = new RegExp("(" + words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
    return cut.split(pattern).filter((piece) => piece !== "").map((piece) => ({ text: piece, match: words.includes(piece.toLowerCase()) }));
}
