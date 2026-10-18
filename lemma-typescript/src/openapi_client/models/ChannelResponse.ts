/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttentionResponse } from './AttentionResponse.js';
import type { ChannelKind } from './ChannelKind.js';
import type { ChannelSettingsResponse } from './ChannelSettingsResponse.js';
import type { LinePreview } from './LinePreview.js';
import type { PersonRef } from './PersonRef.js';
import type { SourceSummary } from './SourceSummary.js';
import type { UnreadResponse } from './UnreadResponse.js';
export type ChannelResponse = {
    archived?: boolean;
    /**
     * The channel asks for a person, and why.
     */
    attention?: (AttentionResponse | null);
    created_at: string;
    /**
     * A member who stepped in by writing to the contact: agents stay quiet until they have not written for `settings.hand_back_after_hours`, or the agent is switched on again.
     */
    handler?: (PersonRef | null);
    id: string;
    kind: ChannelKind;
    last_line?: (LinePreview | null);
    last_message_at?: (string | null);
    /**
     * Whose channel it is: the member's for a DIRECT one, the contact's for a CONTACT one; absent for a GROUP.
     */
    person?: (PersonRef | null);
    settings?: ChannelSettingsResponse;
    /**
     * The outside places feeding it.
     */
    sources?: Array<SourceSummary>;
    title?: (string | null);
    topic?: (string | null);
    unread?: UnreadResponse;
};
