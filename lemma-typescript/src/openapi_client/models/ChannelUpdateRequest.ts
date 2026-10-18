/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelSettingsChange } from './ChannelSettingsChange.js';
export type ChannelUpdateRequest = {
    archived?: (boolean | null);
    /**
     * A contact's channel only; takes `contact.message`.
     */
    settings?: (ChannelSettingsChange | null);
    title?: (string | null);
    topic?: (string | null);
};
