/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What the page needs to put a site in front of somebody.
 */
export type PendingSignInResponse = {
    /**
     * The conversation's agent when it is a named one; absent is the pod assistant's browser.
     */
    agent_id?: (string | null);
    origin: string;
    /**
     * The conversation's pod: whose browser the sign-in goes into. Pass it, with `agent_id`, on every workspace browser call the page makes.
     */
    pod_id?: (string | null);
    reason: string;
    tool_call_id: string;
};
