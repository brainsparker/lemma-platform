/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type NoticeRequest = {
    text: string;
    /**
     * An email's subject, if it goes by email.
     */
    title?: (string | null);
    /**
     * The member to tell: a pod member id, user id, or email address.
     */
    to: string;
};
