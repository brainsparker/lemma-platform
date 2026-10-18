/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ChannelSettingsResponse = {
    /**
     * A contact's channel: whether its agents answer the contact. Off, only members do.
     */
    agents_answer?: boolean;
    /**
     * How long a member who stepped in holds the contact after their last line, before the agent answers again.
     */
    hand_back_after_hours?: number;
};
