/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelMessageResponse } from './ChannelMessageResponse.js';
export type ThreadResponse = {
    has_more: boolean;
    /**
     * Replies, oldest first.
     */
    items: Array<ChannelMessageResponse>;
    next_after_seq?: (number | null);
    root: ChannelMessageResponse;
};
