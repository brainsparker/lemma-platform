/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttentionResponse } from './AttentionResponse.js';
import type { ChannelKind } from './ChannelKind.js';
import type { ContactStatus } from './ContactStatus.js';
import type { LinePreview } from './LinePreview.js';
import type { PersonRef } from './PersonRef.js';
import type { SourceSummary } from './SourceSummary.js';
import type { UnreadResponse } from './UnreadResponse.js';
import type { Visibility } from './Visibility.js';
export type ChannelResponse = {
    /**
     * A contact's channel: whether its agents answer the contact.
     */
    agents_answer?: (boolean | null);
    archived?: boolean;
    /**
     * A contact's channel: who answers them by hand.
     */
    assignee?: (PersonRef | null);
    /**
     * The channel asks for a person, and why.
     */
    attention?: (AttentionResponse | null);
    created_at: string;
    /**
     * A contact's channel: how long the assignee holds the contact after their last line before the agent answers again.
     */
    hand_back_after_hours?: (number | null);
    id: string;
    kind: ChannelKind;
    last_line?: (LinePreview | null);
    last_line_at: string;
    last_seq?: number;
    /**
     * Whose channel it is: the member's for a DIRECT one, the contact's for a CONTACT one; absent for a GROUP.
     */
    person?: (PersonRef | null);
    /**
     * The outside places feeding it.
     */
    sources?: Array<SourceSummary>;
    /**
     * A contact's channel: where it stands.
     */
    status?: (ContactStatus | null);
    title?: (string | null);
    topic?: (string | null);
    unread?: UnreadResponse;
    visibility: Visibility;
};
