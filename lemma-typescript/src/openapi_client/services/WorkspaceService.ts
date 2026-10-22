/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceFileEntry } from '../models/WorkspaceFileEntry.js';
import type { WorkspaceFileListResponse } from '../models/WorkspaceFileListResponse.js';
import type { WorkspaceStatusResponse } from '../models/WorkspaceStatusResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class WorkspaceService {
    /**
     * List workspace files
     * @param path
     * @param podId The pod whose workspace to use. Each pod has its own, with its own files and browser. Omitted, your personal workspace.
     * @param agent A named agent of `pod_id`, to use that agent's workspace. Each agent has its own. Omitted, the pod assistant's.
     * @param wake Start the workspace if it is paused. Off by default.
     * @param after Continue after this entry's path, from a previous response's `next_after`.
     * @returns WorkspaceFileListResponse Successful Response
     * @throws ApiError
     */
    public static workspaceFilesList(
        path?: (string | null),
        podId?: (string | null),
        agent?: (string | null),
        wake: boolean = false,
        after?: (string | null),
    ): CancelablePromise<WorkspaceFileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/files',
            query: {
                'path': path,
                'pod_id': podId,
                'agent': agent,
                'wake': wake,
                'after': after,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read workspace file content
     * @param path
     * @param podId The pod whose workspace to use. Each pod has its own, with its own files and browser. Omitted, your personal workspace.
     * @param agent A named agent of `pod_id`, to use that agent's workspace. Each agent has its own. Omitted, the pod assistant's.
     * @param offset
     * @param length
     * @param range
     * @param ifNoneMatch
     * @returns any Successful Response
     * @throws ApiError
     */
    public static workspaceFilesContent(
        path: string,
        podId?: (string | null),
        agent?: (string | null),
        offset?: number,
        length?: (number | null),
        range?: (string | null),
        ifNoneMatch?: (string | null),
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/files:content',
            headers: {
                'Range': range,
                'If-None-Match': ifNoneMatch,
            },
            query: {
                'path': path,
                'pod_id': podId,
                'agent': agent,
                'offset': offset,
                'length': length,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stat one workspace file
     * @param path
     * @param podId The pod whose workspace to use. Each pod has its own, with its own files and browser. Omitted, your personal workspace.
     * @param agent A named agent of `pod_id`, to use that agent's workspace. Each agent has its own. Omitted, the pod assistant's.
     * @returns WorkspaceFileEntry Successful Response
     * @throws ApiError
     */
    public static workspaceFilesStat(
        path: string,
        podId?: (string | null),
        agent?: (string | null),
    ): CancelablePromise<WorkspaceFileEntry> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/files:stat',
            query: {
                'path': path,
                'pod_id': podId,
                'agent': agent,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Whether your computer is ready
     * @param podId The pod whose workspace to use. Each pod has its own, with its own files and browser. Omitted, your personal workspace.
     * @param agent A named agent of `pod_id`, to use that agent's workspace. Each agent has its own. Omitted, the pod assistant's.
     * @returns WorkspaceStatusResponse Successful Response
     * @throws ApiError
     */
    public static workspaceStatus(
        podId?: (string | null),
        agent?: (string | null),
    ): CancelablePromise<WorkspaceStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/status',
            query: {
                'pod_id': podId,
                'agent': agent,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
