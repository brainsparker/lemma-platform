/** Naming somebody in a line: the `@` picker, and the mentions a post sends.
 *
 *  What is typed stays plain text -- "@Lem can you…" -- and the people picked
 *  from the menu are remembered beside it. A post names each one whose "@Name"
 *  is still in the text when it is sent, so deleting the words un-names them,
 *  and typing "@Lem" by hand without picking names nobody: two people may
 *  share a name, and only the menu knows which one was meant. */

export interface Mentionable {
    kind: "MEMBER" | "CONTACT" | "AGENT";
    id: string;
    name: string;
}

export interface Mention {
    kind: "MEMBER" | "CONTACT" | "AGENT";
    id: string;
}

/** The `@` being typed at the caret, if there is one: where it starts and
 *  what follows it. An `@` counts at the start or after a space, so an email
 *  address never opens the menu. */
export function mentionAt(text: string, caret: number): { start: number; query: string } | null {
    const before = text.slice(0, caret);
    const match = /(^|\s)@([^\s@]{0,40})$/.exec(before);
    if (!match) return null;
    return { start: before.length - match[2].length - 1, query: match[2] };
}

/** Who the menu offers for what was typed: anyone a word of whose name starts
 *  with it, agents first, then in the order given. */
export function matchMentionable(people: readonly Mentionable[], query: string, limit = 6): Mentionable[] {
    const wanted = query.toLowerCase();
    const fits = (who: Mentionable) => !wanted
        || who.name.toLowerCase().startsWith(wanted)
        || who.name.toLowerCase().split(/\s+/).some((word) => word.startsWith(wanted));
    const found = people.filter(fits);
    return [...found.filter((who) => who.kind === "AGENT"), ...found.filter((who) => who.kind !== "AGENT")].slice(0, limit);
}

/** The text with the `@query` at the caret replaced by the person picked,
 *  and where the caret goes after them. */
export function insertMention(text: string, at: { start: number }, caret: number, who: Mentionable): { text: string; caret: number } {
    const name = "@" + who.name + " ";
    const next = text.slice(0, at.start) + name + text.slice(caret).replace(/^ /, "");
    return { text: next, caret: at.start + name.length };
}

function escaped(name: string): string {
    return name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The mentions a post sends: each person picked whose "@Name" is still in
 *  the text, once. */
export function mentionsIn(text: string, picked: readonly Mentionable[]): Mention[] {
    const out: Mention[] = [];
    const seen = new Set<string>();
    for (const who of picked) {
        const key = who.kind + ":" + who.id;
        if (seen.has(key)) continue;
        if (!new RegExp("(^|\\s)@" + escaped(who.name) + "(?![\\p{L}\\p{N}_])", "u").test(text)) continue;
        seen.add(key);
        out.push({ kind: who.kind, id: who.id });
    }
    return out;
}

/** Everyone who can be named in a channel: its agents, then the people in it
 *  other than the reader. Each once; nobody without an id or a name. */
export function mentionables(
    agents: readonly { id: string; label: string }[],
    people: readonly { kind: "MEMBER" | "CONTACT" | "AGENT"; id: string | null; name: string | null }[],
    me: string | null,
): Mentionable[] {
    const out: Mentionable[] = [];
    const seen = new Set<string>();
    const add = (who: Mentionable) => {
        const key = who.kind + ":" + who.id;
        if (seen.has(key)) return;
        seen.add(key);
        out.push(who);
    };
    for (const agent of agents) if (agent.id && agent.label) add({ kind: "AGENT", id: agent.id, name: agent.label });
    for (const person of people) {
        if (!person.id || !person.name) continue;
        if (person.kind === "MEMBER" && person.id === me) continue;
        add({ kind: person.kind, id: person.id, name: person.name });
    }
    return out;
}
