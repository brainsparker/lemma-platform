/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GenerationSettings } from './GenerationSettings.js';
/**
 * Select an agent runtime profile, an optional catalog model, and how it generates.
 */
export type AgentRuntimeConfig = {
    model_name?: (string | null);
    profile_id: string;
    settings?: (GenerationSettings | null);
};
