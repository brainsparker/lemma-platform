/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PersonRef } from './PersonRef.js';
export type ChannelMessageResponse = {
    created_at: string;
    id: string;
    members_only?: boolean;
    /**
     * For an agent's line: the member or contact it acted for, whose access the answer was made with.
     */
    on_behalf_of?: (PersonRef | null);
    sender: PersonRef;
    /**
     * Where it was said; absent for a line written in Lemma.
     */
    source_id?: (string | null);
    /**
     * Absent where the line is withheld from the reader.
     */
    text?: (string | null);
    /**
     * An answer an agent made with another member's own access: the reader sees that it was given, and for whom, not what it said.
     */
    withheld?: boolean;
};
