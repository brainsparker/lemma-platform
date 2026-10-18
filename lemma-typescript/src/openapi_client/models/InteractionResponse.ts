/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InteractionKind } from './InteractionKind.js';
import type { InteractionState } from './InteractionState.js';
import type { PersonRef } from './PersonRef.js';
export type InteractionResponse = {
    answer?: (Record<string, any> | null);
    answered_at?: (string | null);
    answered_by?: (PersonRef | null);
    /**
     * The one person who may answer it.
     */
    awaits?: (PersonRef | null);
    kind: InteractionKind;
    /**
     * What is asked: a `prompt`, and `choices` or a `form` where the asking side offers them.
     */
    question: Record<string, any>;
    state: InteractionState;
};
