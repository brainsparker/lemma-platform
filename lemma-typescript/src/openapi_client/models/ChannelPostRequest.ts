/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Mention } from './Mention.js';
export type ChannelPostRequest = {
    /**
     * A note to the pod's members: never sent outside.
     */
    members_only?: boolean;
    mentions?: Array<Mention>;
    parts?: Array<Record<string, any>>;
    reply_to_id?: (string | null);
    /**
     * Which outside place it goes to; by default the one the channel last heard from.
     */
    source_id?: (string | null);
    text?: string;
    /**
     * Reply in the thread of this line.
     */
    thread_root_id?: (string | null);
};
