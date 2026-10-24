/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodConnectorMode } from './PodConnectorMode.js';
import type { PodConnectorSource } from './PodConnectorSource.js';
import type { PodConnectorStatus } from './PodConnectorStatus.js';
import type { PodConnectorVisibility } from './PodConnectorVisibility.js';
import type { SharedAccountSummary } from './SharedAccountSummary.js';
export type PodConnectorResponse = {
    auth_config_id: string;
    connector_id?: (string | null);
    created_at: string;
    created_by_user_id?: (string | null);
    id: string;
    installation_name?: (string | null);
    mode: PodConnectorMode;
    pod_id: string;
    purpose?: (string | null);
    required: boolean;
    shared_account?: (SharedAccountSummary | null);
    shared_pod_account_id?: (string | null);
    slug: string;
    source?: PodConnectorSource;
    status: PodConnectorStatus;
    updated_at: string;
    visibility: PodConnectorVisibility;
};
