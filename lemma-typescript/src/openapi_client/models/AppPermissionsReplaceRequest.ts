/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AppResourcePermissionRequest } from './AppResourcePermissionRequest.js';
/**
 * The app's declared grants: what its page may do, at most, for whoever
 * views it. Replaces every grant it held; an empty list revokes them all.
 */
export type AppPermissionsReplaceRequest = {
    grants?: Array<AppResourcePermissionRequest>;
};
