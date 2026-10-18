/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InteractionResponse } from './InteractionResponse.js';
import type { PersonRef } from './PersonRef.js';
export type ChannelMessageResponse = {
    /**
     * For an agent's line: the conversation it came from.
     */
    conversation_id?: (string | null);
    created_at: string;
    deleted?: boolean;
    edited_at?: (string | null);
    id: string;
    interaction?: (InteractionResponse | null);
    last_reply_at?: (string | null);
    members_only?: boolean;
    /**
     * For an agent's line: the member or contact it acted for, whose access the answer was made with.
     */
    on_behalf_of?: (PersonRef | null);
    parts?: Array<Record<string, any>>;
    /**
     * On a thread's first line.
     */
    reply_count?: number;
    reply_to_id?: (string | null);
    sender: PersonRef;
    /**
     * Where it was said; absent for a line written in Lemma.
     */
    source_id?: (string | null);
    /**
     * Absent where the line is withheld from the reader, or deleted.
     */
    text?: (string | null);
    /**
     * The line this one is a reply in the thread of.
     */
    thread_root_id?: (string | null);
    /**
     * An answer an agent made with another member's own access: the reader sees that it was given, and for whom, not what it said.
     */
    withheld?: boolean;
};
