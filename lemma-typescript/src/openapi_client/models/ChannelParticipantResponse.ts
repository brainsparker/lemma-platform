/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotifyLevel } from './NotifyLevel.js';
import type { PersonRef } from './PersonRef.js';
export type ChannelParticipantResponse = {
    last_read_at?: (string | null);
    notify?: NotifyLevel;
    /**
     * A member or a contact.
     */
    person: PersonRef;
};
