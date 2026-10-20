/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FilePart } from './FilePart.js';
import type { LineDelivery } from './LineDelivery.js';
import type { LinkPart } from './LinkPart.js';
import type { MentionPart } from './MentionPart.js';
import type { PersonRef } from './PersonRef.js';
import type { QuestionResponse } from './QuestionResponse.js';
export type ChannelMessageResponse = {
    channel_id: string;
    client_message_id?: (string | null);
    conversation_id?: (string | null);
    created_at: string;
    deleted?: boolean;
    /**
     * Only on the reader's own line: each place outside Lemma it went to, and what became of it. Pushes to members are not listed here (`…/deliveries` has every send).
     */
    delivered?: (Array<LineDelivery> | null);
    edited_at?: (string | null);
    id: string;
    last_reply_at?: (string | null);
    members_only?: boolean;
    /**
     * For an agent's line: the member or contact it acted for, whose access the answer was made with.
     */
    on_behalf_of?: (PersonRef | null);
    parts?: Array<(MentionPart | FilePart | LinkPart)>;
    question?: (QuestionResponse | null);
    /**
     * On a thread's first line.
     */
    reply_count?: number;
    reply_to_id?: (string | null);
    sender: PersonRef;
    /**
     * Its place in the channel's timeline.
     */
    seq: number;
    /**
     * Where it was said or sent; absent for a line only in Lemma.
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
