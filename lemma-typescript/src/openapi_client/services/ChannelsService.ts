/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelAnswerRequest } from '../models/ChannelAnswerRequest.js';
import type { ChannelCreateRequest } from '../models/ChannelCreateRequest.js';
import type { ChannelDetailResponse } from '../models/ChannelDetailResponse.js';
import type { ChannelEditRequest } from '../models/ChannelEditRequest.js';
import type { ChannelKind } from '../models/ChannelKind.js';
import type { ChannelListResponse } from '../models/ChannelListResponse.js';
import type { ChannelMessageResponse } from '../models/ChannelMessageResponse.js';
import type { ChannelMessagesResponse } from '../models/ChannelMessagesResponse.js';
import type { ChannelPostRequest } from '../models/ChannelPostRequest.js';
import type { ChannelUpdateRequest } from '../models/ChannelUpdateRequest.js';
import type { InboxResponse } from '../models/InboxResponse.js';
import type { NotifyRequest } from '../models/NotifyRequest.js';
import type { ParticipantsAddRequest } from '../models/ParticipantsAddRequest.js';
import type { SearchResponse } from '../models/SearchResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class ChannelsService {
    /**
     * List Channels
     * The pod's channels this member sees, most recently active first, each
     * with what is new in it for them and its last line.
     * @param podId
     * @param kind
     * @param archived
     * @param contactId Only this contact's channel.
     * @param limit
     * @param before
     * @returns ChannelListResponse Successful Response
     * @throws ApiError
     */
    public static channelList(
        podId: string,
        kind?: (ChannelKind | null),
        archived: boolean = false,
        contactId?: (string | null),
        limit: number = 50,
        before?: (string | null),
    ): CancelablePromise<ChannelListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels',
            path: {
                'pod_id': podId,
            },
            query: {
                'kind': kind,
                'archived': archived,
                'contact_id': contactId,
                'limit': limit,
                'before': before,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Channel
     * A channel made in Lemma, with its maker and the members they named in it.
     * @param podId
     * @param requestBody
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelCreate(
        podId: string,
        requestBody: ChannelCreateRequest,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Search Channels
     * Lines matching every word, newest first, in the channels the reader sees.
     * @param podId
     * @param q
     * @param limit
     * @returns SearchResponse Successful Response
     * @throws ApiError
     */
    public static channelMessageSearch(
        podId: string,
        q: string,
        limit: number = 30,
    ): CancelablePromise<SearchResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/search',
            path: {
                'pod_id': podId,
            },
            query: {
                'q': q,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Channel
     * One channel, with the places it is fed from, the people in it, and how
     * the reader hears of it.
     * @param podId
     * @param channelId
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelGet(
        podId: string,
        channelId: string,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Channel
     * Rename, re-describe or archive a group; switch a contact's agent.
     *
     * A contact's channel changes only its `settings`, and takes
     * `contact.message`: switching its agent off leaves the contact to members;
     * switching it on hands them back to the agent at once, whoever stepped in.
     * @param podId
     * @param channelId
     * @param requestBody
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelUpdate(
        podId: string,
        channelId: string,
        requestBody: ChannelUpdateRequest,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/channels/{channel_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Set My Notify
     * How the reader hears of this channel's lines outside the app.
     * @param podId
     * @param channelId
     * @param requestBody
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelNotifyUpdate(
        podId: string,
        channelId: string,
        requestBody: NotifyRequest,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/channels/{channel_id}/me',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Channel Messages
     * The channel's timeline, oldest first: who said what, when and where.
     * A thread's replies are not in it; its first line counts them.
     * @param podId
     * @param channelId
     * @param limit
     * @param before
     * @returns ChannelMessagesResponse Successful Response
     * @throws ApiError
     */
    public static channelMessageList(
        podId: string,
        channelId: string,
        limit: number = 50,
        before?: (string | null),
    ): CancelablePromise<ChannelMessagesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/messages',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            query: {
                'limit': limit,
                'before': before,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Channel Message
     * Write in the channel. It goes to one outside place -- the one named, or
     * the one the channel last heard from -- unless it is a note to members.
     * @param podId
     * @param channelId
     * @param requestBody
     * @returns ChannelMessageResponse Successful Response
     * @throws ApiError
     */
    public static channelMessagePost(
        podId: string,
        channelId: string,
        requestBody: ChannelPostRequest,
    ): CancelablePromise<ChannelMessageResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/messages',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Channel Message
     * Delete one's own line: it stays in its place, saying nothing.
     * @param podId
     * @param channelId
     * @param messageId
     * @returns void
     * @throws ApiError
     */
    public static channelMessageDelete(
        podId: string,
        channelId: string,
        messageId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/channels/{channel_id}/messages/{message_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'message_id': messageId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Edit Channel Message
     * Change what one's own line says. A copy already sent outside stays as sent.
     * @param podId
     * @param channelId
     * @param messageId
     * @param requestBody
     * @returns ChannelMessageResponse Successful Response
     * @throws ApiError
     */
    public static channelMessageUpdate(
        podId: string,
        channelId: string,
        messageId: string,
        requestBody: ChannelEditRequest,
    ): CancelablePromise<ChannelMessageResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/channels/{channel_id}/messages/{message_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'message_id': messageId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Answer Channel Question
     * Answer what this line asks the reader: a question, an approval, a form.
     *
     * No session is held across the answer: what asked the question takes it in
     * its own -- a run resumed, a form submitted -- and may call out to do so.
     * @param podId
     * @param channelId
     * @param messageId
     * @param requestBody
     * @returns ChannelMessageResponse Successful Response
     * @throws ApiError
     */
    public static channelMessageAnswer(
        podId: string,
        channelId: string,
        messageId: string,
        requestBody: ChannelAnswerRequest,
    ): CancelablePromise<ChannelMessageResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/answer',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'message_id': messageId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Thread
     * A thread's replies, oldest first, the newest page of them.
     * @param podId
     * @param channelId
     * @param messageId
     * @param limit
     * @param before
     * @returns ChannelMessagesResponse Successful Response
     * @throws ApiError
     */
    public static channelMessageThread(
        podId: string,
        channelId: string,
        messageId: string,
        limit: number = 50,
        before?: (string | null),
    ): CancelablePromise<ChannelMessagesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/thread',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'message_id': messageId,
            },
            query: {
                'limit': limit,
                'before': before,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Participants
     * Add members of the pod to a group.
     * @param podId
     * @param channelId
     * @param requestBody
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelParticipantAdd(
        podId: string,
        channelId: string,
        requestBody: ParticipantsAddRequest,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/participants',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Participant
     * Leave a channel, or -- for who may change it -- take somebody out.
     * @param podId
     * @param channelId
     * @param memberId
     * @returns void
     * @throws ApiError
     */
    public static channelParticipantRemove(
        podId: string,
        channelId: string,
        memberId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/channels/{channel_id}/participants/{member_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'member_id': memberId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Mark Channel Read
     * Everything in the channel up to now has been read by this member, and
     * every line naming them.
     * @param podId
     * @param channelId
     * @returns void
     * @throws ApiError
     */
    public static channelMarkRead(
        podId: string,
        channelId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/read',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Inbox
     * What is new for the reader across the pod, and every question waiting
     * on them, newest first.
     * @param podId
     * @returns InboxResponse Successful Response
     * @throws ApiError
     */
    public static channelInboxGet(
        podId: string,
    ): CancelablePromise<InboxResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/inbox',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
