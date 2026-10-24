/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ImpactedPodConnector } from './ImpactedPodConnector.js';
/**
 * What deleting or disabling an installation stops, before it is done.
 */
export type InstallationImpactResponse = {
    account_count: number;
    auth_config_id: string;
    pod_account_count: number;
    pod_connectors: Array<ImpactedPodConnector>;
};
