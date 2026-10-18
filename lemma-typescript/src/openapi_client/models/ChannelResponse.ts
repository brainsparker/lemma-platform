/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelKind } from './ChannelKind.js';
import type { PersonRef } from './PersonRef.js';
export type ChannelResponse = {
    created_at: string;
    /**
     * A member answering by hand; agents do not answer meanwhile.
     */
    handler?: (PersonRef | null);
    id: string;
    kind: ChannelKind;
    last_message_at?: (string | null);
    /**
     * Whose channel it is: the member's for a DIRECT one, the contact's for a CONTACT one; absent for a GROUP.
     */
    person?: (PersonRef | null);
    title?: (string | null);
};
