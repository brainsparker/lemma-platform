/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourceType } from './ResourceType.js';
/**
 * One thing the app may do for its viewer, e.g. ``datastore_table``
 * ``metrics`` ``[datastore.record.read]``.
 */
export type AppResourcePermissionRequest = {
    permission_ids?: Array<string>;
    resource_name: string;
    resource_type: ResourceType;
};
