/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UnreadResponse = {
    /**
     * New lines since the reader last read it; stops at 100.
     */
    lines?: number;
    /**
     * Lines naming the reader, unread.
     */
    mentions?: number;
    /**
     * Questions here waiting on the reader's answer.
     */
    waiting?: number;
};
