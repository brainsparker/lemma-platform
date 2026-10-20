/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PersonRef } from './PersonRef.js';
export type LinePreview = {
    created_at: string;
    id: string;
    sender: PersonRef;
    seq: number;
    /**
     * The start of what was said; empty when withheld.
     */
    text: string;
    withheld?: boolean;
};
