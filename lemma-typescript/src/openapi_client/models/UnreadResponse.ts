/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UnreadResponse = {
    /**
     * New lines since the reader last read it, in channels they joined; stops at 100.
     */
    lines?: number;
    /**
     * Unread lines naming the reader.
     */
    mentions?: number;
    /**
     * The last line the reader read.
     */
    read_seq?: number;
    /**
     * Questions here waiting on the reader's answer.
     */
    waiting?: number;
};
