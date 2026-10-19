/** What somebody was writing in a channel, or in one thread of it, kept
 *  while they look elsewhere: switching threads, closing the panel, opening
 *  another channel and coming back. One draft per place it would be sent --
 *  the channel itself, or each thread by its first line -- so a reply begun
 *  in one thread never turns up in another.
 *
 *  Held for the tab (`sessionStorage`), and in memory where storage is
 *  refused; a draft is a convenience, never something worth an error. */

const PREFIX = "lemma.channel-draft:";
const memory = new Map<string, string>();

export function draftKey(podId: string, channelId: string, threadRootId?: string | null): string {
    return PREFIX + podId + ":" + channelId + ":" + (threadRootId ?? "");
}

function storage(): Storage | null {
    try {
        return typeof sessionStorage === "undefined" ? null : sessionStorage;
    } catch {
        return null;
    }
}

export function readDraft(key: string): string {
    const held = memory.get(key);
    if (held !== undefined) return held;
    try {
        return storage()?.getItem(key) ?? "";
    } catch {
        return "";
    }
}

/** Keep `text` as the draft; an empty one is forgotten. */
export function keepDraft(key: string, text: string): void {
    if (text) memory.set(key, text);
    else memory.delete(key);
    try {
        if (text) storage()?.setItem(key, text);
        else storage()?.removeItem(key);
    } catch {
        /* storage refused: the in-memory copy is enough for this tab */
    }
}
