/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotifyLevel } from './NotifyLevel.js';
export type MyChannelSettings = {
    joined?: boolean;
    last_read_at?: (string | null);
    notify?: NotifyLevel;
    /**
     * Where lines reach them outside the app; absent for the pod's rule.
     */
    notify_via?: (Array<string> | null);
};
