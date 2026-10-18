/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PersonRef } from './PersonRef.js';
export type ChannelParticipantResponse = {
    last_read_at?: (string | null);
    /**
     * A member or a contact.
     */
    person: PersonRef;
};
