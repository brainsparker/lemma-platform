/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AppPermissionsReplaceRequest } from './AppPermissionsReplaceRequest.js';
export type CreateAppRequest = {
    description?: (string | null);
    name: string;
    /**
     * The app's declared grants. Its page acts for its viewer with the viewer's access intersected with these, and is refused `APP_NOT_GRANTED` outside them.
     */
    permissions?: (AppPermissionsReplaceRequest | null);
    public_slug?: (string | null);
    visibility?: (string | null);
};
