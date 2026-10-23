/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ImpactedPod } from './ImpactedPod.js';
import type { ImpactedPodConnector } from './ImpactedPodConnector.js';
/**
 * What deleting or disabling an installation stops, before it is done.
 *
 * The counts are the whole organization's; `pods` names only the pods the
 * caller can open.
 */
export type InstallationImpactResponse = {
    account_count: number;
    auth_config_id: string;
    /**
     * MCP event subscriptions held on its accounts.
     */
    event_subscription_count?: number;
    pod_account_count: number;
    pod_connectors: Array<ImpactedPodConnector>;
    /**
     * Per pod the caller can open: what stops there.
     */
    pods?: Array<ImpactedPod>;
    /**
     * Schedules that listen through one of its pod connectors.
     */
    schedule_count?: number;
    /**
     * Surfaces that answer through one of its pod connectors.
     */
    surface_count?: number;
};
