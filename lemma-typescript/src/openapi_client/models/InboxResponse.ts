/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InboxTotals } from './InboxTotals.js';
import type { WaitingItem } from './WaitingItem.js';
export type InboxResponse = {
    totals: InboxTotals;
    /**
     * What is waiting on the reader, newest first.
     */
    waiting?: Array<WaitingItem>;
};
