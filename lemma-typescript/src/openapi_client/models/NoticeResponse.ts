/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type NoticeResponse = {
    channel_id: string;
    delivered_via?: (string | null);
    message_id: string;
    /**
     * Why it went no further than their channel, if it didn't.
     */
    reason?: (string | null);
};
