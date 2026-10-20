/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InboxItem } from './InboxItem.js';
import type { InboxTotals } from './InboxTotals.js';
export type InboxResponse = {
    /**
     * Unread lines naming the reader, newest first.
     */
    mentions?: Array<InboxItem>;
    totals: InboxTotals;
    /**
     * Questions waiting on the reader, newest first.
     */
    waiting?: Array<InboxItem>;
};
