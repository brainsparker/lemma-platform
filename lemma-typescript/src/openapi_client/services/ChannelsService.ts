/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelAnswerRequest } from '../models/ChannelAnswerRequest.js';
import type { ChannelCasesResponse } from '../models/ChannelCasesResponse.js';
import type { ChannelCreateRequest } from '../models/ChannelCreateRequest.js';
import type { ChannelDetailResponse } from '../models/ChannelDetailResponse.js';
import type { ChannelEditRequest } from '../models/ChannelEditRequest.js';
import type { ChannelKind } from '../models/ChannelKind.js';
import type { ChannelListResponse } from '../models/ChannelListResponse.js';
import type { ChannelMemberListResponse } from '../models/ChannelMemberListResponse.js';
import type { ChannelMessageResponse } from '../models/ChannelMessageResponse.js';
import type { ChannelMessagesResponse } from '../models/ChannelMessagesResponse.js';
import type { ChannelPostRequest } from '../models/ChannelPostRequest.js';
import type { ChannelUpdateRequest } from '../models/ChannelUpdateRequest.js';
import type { ContactStatus } from '../models/ContactStatus.js';
import type { DeliveriesResponse } from '../models/DeliveriesResponse.js';
import type { DeliveryResponse } from '../models/DeliveryResponse.js';
import type { InboxResponse } from '../models/InboxResponse.js';
import type { MembersAddRequest } from '../models/MembersAddRequest.js';
import type { MemberUpdateRequest } from '../models/MemberUpdateRequest.js';
import type { MyMembershipUpdate } from '../models/MyMembershipUpdate.js';
import type { ReadRequest } from '../models/ReadRequest.js';
import type { ReadResponse } from '../models/ReadResponse.js';
import type { SearchResponse } from '../models/SearchResponse.js';
import type { SenderKind } from '../models/SenderKind.js';
import type { ThreadResponse } from '../models/ThreadResponse.js';
import type { Visibility } from '../models/Visibility.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class ChannelsService {
    /**
     * List Channels
     * The channels the reader may see, most recently active first, each with
     * what is new in it for them and its last line.
     * @param podId
     * @param kind
     * @param visibility Only `PUBLIC` or only `PRIVATE` channels; a member's and a contact's own channel are private.
     * @param q Only channels whose title holds these words, in any case.
     * @param archived
     * @param contactId Only this contact's channel.
     * @param assignee `me`, or a member's user id: contacts' channels assigned to them.
     * @param unassigned
     * @param status
     * @param needsPerson Only contacts' channels waiting on a person: open, unassigned, and the agent off or asking for one.
     * @param joined Only channels the reader is in.
     * @param sourceSurface A bot's surface name: only the groups it is in, each with its `source_settings`. Takes reading the bot.
     * @param cursor
     * @param limit
     * @returns ChannelListResponse Successful Response
     * @throws ApiError
     */
    public static channelList(
        podId: string,
        kind?: (ChannelKind | null),
        visibility?: (Visibility | null),
        q?: (string | null),
        archived: boolean = false,
        contactId?: (string | null),
        assignee?: (string | null),
        unassigned: boolean = false,
        status?: (ContactStatus | null),
        needsPerson: boolean = false,
        joined: boolean = false,
        sourceSurface?: (string | null),
        cursor?: (string | null),
        limit: number = 50,
    ): CancelablePromise<ChannelListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels',
            path: {
                'pod_id': podId,
            },
            query: {
                'kind': kind,
                'visibility': visibility,
                'q': q,
                'archived': archived,
                'contact_id': contactId,
                'assignee': assignee,
                'unassigned': unassigned,
                'status': status,
                'needs_person': needsPerson,
                'joined': joined,
                'source_surface': sourceSurface,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Channel
     * A group made in Lemma: public unless asked otherwise, with its maker as
     * its owner and the members and agents named in it. The same
     * `Idempotency-Key` from the same member returns the group it made.
     * @param podId
     * @param requestBody
     * @param idempotencyKey
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelCreate(
        podId: string,
        requestBody: ChannelCreateRequest,
        idempotencyKey?: (string | null),
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels',
            path: {
                'pod_id': podId,
            },
            headers: {
                'Idempotency-Key': idempotencyKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get My Channel
     * The reader's own channel with the pod; 404 until it is made.
     * @param podId
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelMeGet(
        podId: string,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/me',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create My Channel
     * Make the reader's own channel with the pod; 200 with it if it exists.
     * @param podId
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelMeCreate(
        podId: string,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/me',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Channel
     * Delete a group made in Lemma, and its timeline: its owners' or an
     * admin's. A channel an outside place feeds is archived instead.
     * @param podId
     * @param channelId
     * @returns void
     * @throws ApiError
     */
    public static channelDelete(
        podId: string,
        channelId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
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
     * Get Channel
     * One channel, with the places it is fed from, its members and agents,
     * and how the reader hears of it.
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
     * Change exactly the fields named; `null` clears.
     *
     * A group: `title`, `topic`, `visibility`, `archived` -- its owners' or an
     * admin's. A contact's channel: `agents_answer`, `hand_back_after_hours`,
     * `status`, `assignee` -- for who may write to contacts. Switching the agent
     * on hands the contact back to it now: nobody is assigned.
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
     * List Cases
     * A contact's channel's cases, the most recently opened first, each with
     * what happened in it: time to first response, reopens and time to close
     * are read from here. Only a contact's channel has cases.
     * @param podId
     * @param channelId
     * @param cursor
     * @param limit
     * @returns ChannelCasesResponse Successful Response
     * @throws ApiError
     */
    public static channelCaseList(
        podId: string,
        channelId: string,
        cursor?: (string | null),
        limit: number = 20,
    ): CancelablePromise<ChannelCasesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/cases',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Members
     * Who is in a channel -- members, contacts and agents -- in the order
     * they joined, a page at a time.
     * @param podId
     * @param channelId
     * @param cursor
     * @param limit
     * @returns ChannelMemberListResponse Successful Response
     * @throws ApiError
     */
    public static channelMemberList(
        podId: string,
        channelId: string,
        cursor?: (string | null),
        limit: number = 100,
    ): CancelablePromise<ChannelMemberListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/members',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Members
     * Add members of the pod, and the pod's agents, to a group. An agent
     * already in it answers as now asked.
     * @param podId
     * @param channelId
     * @param requestBody
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelMemberAdd(
        podId: string,
        channelId: string,
        requestBody: MembersAddRequest,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/members',
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
     * Leave Channel
     * Leave a channel. Leaving one not joined is nothing; a member's own
     * channel cannot be left.
     * @param podId
     * @param channelId
     * @returns void
     * @throws ApiError
     */
    public static channelMemberLeave(
        podId: string,
        channelId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/channels/{channel_id}/members/me',
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
     * Update My Membership
     * How the reader hears of this channel's lines outside the app. Absent is
     * unchanged; `null` clears `notify_via` (the pod's rule) and `muted_until`.
     * @param podId
     * @param channelId
     * @param requestBody
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelMemberMeUpdate(
        podId: string,
        channelId: string,
        requestBody: MyMembershipUpdate,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/channels/{channel_id}/members/me',
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
     * Join Channel
     * Join a channel the reader may see: a public group, or -- holding
     * `contact.read` -- a contact's channel. Holding `channel.manage`, a private
     * group too, on request: how a pod admin finds and takes over a group nobody
     * let them into; it is logged. Joining one already joined is nothing.
     * @param podId
     * @param channelId
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelMemberJoin(
        podId: string,
        channelId: string,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/members/me',
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
     * Remove Member
     * Take somebody out of a group -- its owners' or an editor's -- or leave a
     * channel oneself.
     * @param podId
     * @param channelId
     * @param kind
     * @param memberId
     * @returns void
     * @throws ApiError
     */
    public static channelMemberRemove(
        podId: string,
        channelId: string,
        kind: SenderKind,
        memberId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/channels/{channel_id}/members/{kind}:{member_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'kind': kind,
                'member_id': memberId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Member
     * A person's role (`owner` or `member`), or when an agent answers.
     * @param podId
     * @param channelId
     * @param kind
     * @param memberId
     * @param requestBody
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelMemberUpdate(
        podId: string,
        channelId: string,
        kind: SenderKind,
        memberId: string,
        requestBody: MemberUpdateRequest,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/channels/{channel_id}/members/{kind}:{member_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'kind': kind,
                'member_id': memberId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Messages
     * The timeline, oldest first: the newest page, the page before
     * `before_seq`, or the page after `after_seq`. A thread's replies are not in
     * it; its first line counts them.
     * @param podId
     * @param channelId
     * @param beforeSeq
     * @param afterSeq
     * @param limit
     * @returns ChannelMessagesResponse Successful Response
     * @throws ApiError
     */
    public static channelMessageList(
        podId: string,
        channelId: string,
        beforeSeq?: (number | null),
        afterSeq?: (number | null),
        limit: number = 50,
    ): CancelablePromise<ChannelMessagesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/messages',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            query: {
                'before_seq': beforeSeq,
                'after_seq': afterSeq,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Message
     * Write in the channel. A reply goes where its thread is; otherwise to
     * the place named, or the one the channel last heard from -- unless it is a
     * note to members. Naming somebody who cannot see the channel is refused.
     * @param podId
     * @param channelId
     * @param requestBody
     * @param idempotencyKey
     * @returns ChannelMessageResponse Successful Response
     * @throws ApiError
     */
    public static channelMessagePost(
        podId: string,
        channelId: string,
        requestBody: ChannelPostRequest,
        idempotencyKey?: (string | null),
    ): CancelablePromise<ChannelMessageResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/messages',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            headers: {
                'Idempotency-Key': idempotencyKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Message
     * Delete one's own line: it keeps its place, saying nothing.
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
     * Edit Message
     * Change what one's own line says: absent is unchanged, `null` parts
     * clears them. A copy already sent outside stays as sent.
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
     * Answer Question
     * Answer what this line asks the reader: a question, an approval (which
     * needs a `decision`), a form. Only the person it waits on may -- and an
     * approval, or an agent's paused question, only by their own hand: an agent
     * calling with a token delegated by them is refused, as its tool is.
     *
     * No session is held across the answer: what asked takes it in its own -- a
     * run resumed, a form submitted -- and may call out to do so.
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
     * List Deliveries
     * Where a line went outside Lemma, and what became of each send. A push
     * to a member is shown only to that member.
     * @param podId
     * @param channelId
     * @param messageId
     * @returns DeliveriesResponse Successful Response
     * @throws ApiError
     */
    public static channelDeliveryList(
        podId: string,
        channelId: string,
        messageId: string,
    ): CancelablePromise<DeliveriesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/deliveries',
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
     * Retry Delivery
     * Try a failed or given-up send again, now: for who wrote the line, or
     * may change the channel.
     * @param podId
     * @param channelId
     * @param messageId
     * @param deliveryId
     * @returns DeliveryResponse Successful Response
     * @throws ApiError
     */
    public static channelDeliveryRetry(
        podId: string,
        channelId: string,
        messageId: string,
        deliveryId: string,
    ): CancelablePromise<DeliveryResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/deliveries/{delivery_id}/retry',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'message_id': messageId,
                'delivery_id': deliveryId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Line File
     * A file a line shares, for a reader of the line: read as its writer
     * reads it. Gone with the line, or once its writer can no longer read it.
     * @param podId
     * @param channelId
     * @param messageId
     * @param fileId
     * @returns binary The file's bytes
     * @throws ApiError
     */
    public static channelMessageFile(
        podId: string,
        channelId: string,
        messageId: string,
        fileId: string,
    ): CancelablePromise<Blob> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/files/{file_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'message_id': messageId,
                'file_id': fileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Thread
     * A thread: its first line, and its replies oldest first after `after_seq`.
     * @param podId
     * @param channelId
     * @param messageId
     * @param afterSeq
     * @param limit
     * @returns ThreadResponse Successful Response
     * @throws ApiError
     */
    public static channelMessageThread(
        podId: string,
        channelId: string,
        messageId: string,
        afterSeq?: (number | null),
        limit: number = 50,
    ): CancelablePromise<ThreadResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/thread',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'message_id': messageId,
            },
            query: {
                'after_seq': afterSeq,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Mark Read
     * The reader has seen the channel up to line `seq`: it never goes back,
     * and never past the newest line. Mentions up to it are read too.
     * @param podId
     * @param channelId
     * @param requestBody
     * @returns ReadResponse Successful Response
     * @throws ApiError
     */
    public static channelRead(
        podId: string,
        channelId: string,
        requestBody: ReadRequest,
    ): CancelablePromise<ReadResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/read',
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
     * Get Inbox
     * What is new for the reader across the pod: the badges, the questions
     * waiting on them, and the unread lines naming them -- newest first.
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
    /**
     * Search
     * Lines matching every word, newest first, in the channels the reader
     * sees -- or in one of them -- each with its rank and the words around the
     * match. An answer withheld from the reader is not found by what it said.
     * @param podId
     * @param q
     * @param channelId
     * @param kind Only channels of this kind.
     * @param senderKind Only lines said by this person: with `sender_id`.
     * @param senderId A member's user id, a contact's id or an agent's id.
     * @param since Said at or after.
     * @param until Said before.
     * @param archived Search archived channels too.
     * @param cursor
     * @param limit
     * @returns SearchResponse Successful Response
     * @throws ApiError
     */
    public static channelSearch(
        podId: string,
        q: string,
        channelId?: (string | null),
        kind?: (ChannelKind | null),
        senderKind?: (SenderKind | null),
        senderId?: (string | null),
        since?: (string | null),
        until?: (string | null),
        archived: boolean = false,
        cursor?: (string | null),
        limit: number = 30,
    ): CancelablePromise<SearchResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/search',
            path: {
                'pod_id': podId,
            },
            query: {
                'q': q,
                'channel_id': channelId,
                'kind': kind,
                'sender_kind': senderKind,
                'sender_id': senderId,
                'since': since,
                'until': until,
                'archived': archived,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
