/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AppPermissionsReplaceRequest } from './AppPermissionsReplaceRequest.js';
export type UpdateAppRequest = {
    description?: (string | null);
    /**
     * Replaces the app's declared grants; absent leaves them.
     */
    permissions?: (AppPermissionsReplaceRequest | null);
    public_slug?: (string | null);
    visibility?: (string | null);
};
