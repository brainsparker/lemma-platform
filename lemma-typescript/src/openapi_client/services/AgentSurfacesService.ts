/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentSurfaceListResponse } from '../models/AgentSurfaceListResponse.js';
import type { AgentSurfaceResponse } from '../models/AgentSurfaceResponse.js';
import type { AvailableSurfaceChannelsResponse } from '../models/AvailableSurfaceChannelsResponse.js';
import type { AvailableSurfacesResponse } from '../models/AvailableSurfacesResponse.js';
import type { NoticeRequest } from '../models/NoticeRequest.js';
import type { NoticeResponse } from '../models/NoticeResponse.js';
import type { SourceListResponse } from '../models/SourceListResponse.js';
import type { SourceSettingsResponse } from '../models/SourceSettingsResponse.js';
import type { SourceUpdateRequest } from '../models/SourceUpdateRequest.js';
import type { SurfaceCreateRequest } from '../models/SurfaceCreateRequest.js';
import type { SurfacePlatformSetupGuide } from '../models/SurfacePlatformSetupGuide.js';
import type { SurfaceSendRequest } from '../models/SurfaceSendRequest.js';
import type { SurfaceSendResponse } from '../models/SurfaceSendResponse.js';
import type { SurfaceSetupResponse } from '../models/SurfaceSetupResponse.js';
import type { SurfaceUpdateRequest } from '../models/SurfaceUpdateRequest.js';
import type { TelegramLinkRequest } from '../models/TelegramLinkRequest.js';
import type { TelegramLinkResponse } from '../models/TelegramLinkResponse.js';
import type { TelegramManagedBotSetupRequest } from '../models/TelegramManagedBotSetupRequest.js';
import type { TelegramManagedBotSetupResponse } from '../models/TelegramManagedBotSetupResponse.js';
import type { WebWidgetCreatedResponse } from '../models/WebWidgetCreatedResponse.js';
import type { WebWidgetCreateRequest } from '../models/WebWidgetCreateRequest.js';
import type { WebWidgetListResponse } from '../models/WebWidgetListResponse.js';
import type { WebWidgetResponse } from '../models/WebWidgetResponse.js';
import type { WebWidgetSecretResponse } from '../models/WebWidgetSecretResponse.js';
import type { WebWidgetUpdateRequest } from '../models/WebWidgetUpdateRequest.js';
import type { WhatsAppGroupRequest } from '../models/WhatsAppGroupRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentSurfacesService {
    /**
     * List Available Surfaces
     * The connectable-surface catalog: every surface platform with its connector,
     * supported credential modes, the schema to connect an account, and whether this
     * pod's org can still claim the platform's Lemma-managed bot/number. Otherwise
     * platform-level — no surface need exist.
     * @param podId
     * @returns AvailableSurfacesResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceAvailable(
        podId: string,
    ): CancelablePromise<AvailableSurfacesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/available-surfaces',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Send Notice
     * Tell another member of the pod something, in their own channel.
     * @param podId
     * @param requestBody
     * @returns NoticeResponse Successful Response
     * @throws ApiError
     */
    public static agentNoticeSend(
        podId: string,
        requestBody: NoticeRequest,
    ): CancelablePromise<NoticeResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/notices',
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
     * List Sources
     * Every group one bot is in, with each one's settings, newest first.
     * @param podId
     * @param surfaceName The bot's surface name.
     * @returns SourceListResponse Successful Response
     * @throws ApiError
     */
    public static agentSourceList(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<SourceListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/sources',
            path: {
                'pod_id': podId,
            },
            query: {
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Telegram Link
     * A one-use link that adds the pod's Telegram bot to a group the caller picks.
     *
     * The group becomes a GROUP channel, the caller's to answer for. Which
     * Telegram account is theirs is still their profile's to say: a link is
     * easily passed on, so the one that used it is never taken for them. The link
     * works for an hour.
     * @param podId
     * @param requestBody
     * @returns TelegramLinkResponse Successful Response
     * @throws ApiError
     */
    public static agentSourceTelegramLinkCreate(
        podId: string,
        requestBody: TelegramLinkRequest,
    ): CancelablePromise<TelegramLinkResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/sources/telegram-links',
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
     * Start Whatsapp Group
     * Start a WhatsApp group with the pod's bot in it, answered for by the caller.
     *
     * It is a new GROUP channel's source. WhatsApp confirms the group moments
     * later: it comes back ``pending``, and its invite link appears once
     * confirmed. Telegram and Slack cannot create groups for a bot; add the bot
     * to one of theirs instead.
     * @param podId
     * @param requestBody
     * @returns SourceSettingsResponse Successful Response
     * @throws ApiError
     */
    public static agentSourceWhatsappGroupStart(
        podId: string,
        requestBody: WhatsAppGroupRequest,
    ): CancelablePromise<SourceSettingsResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/sources/whatsapp-groups',
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
     * Get Source
     * A group source's settings: who answers its outsiders, and from what.
     * @param podId
     * @param sourceId
     * @returns SourceSettingsResponse Successful Response
     * @throws ApiError
     */
    public static agentSourceGet(
        podId: string,
        sourceId: string,
    ): CancelablePromise<SourceSettingsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/sources/{source_id}',
            path: {
                'pod_id': podId,
                'source_id': sourceId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Source
     * Switch outsiders on or off in one group, or take it over.
     *
     * The member who answers for the group may; so may anybody who configures the
     * bot when nobody in the pod answers for it, and an admin of the pod, whose
     * change the member is told about.
     * @param podId
     * @param sourceId
     * @param requestBody
     * @returns SourceSettingsResponse Successful Response
     * @throws ApiError
     */
    public static agentSourceUpdate(
        podId: string,
        sourceId: string,
        requestBody: SourceUpdateRequest,
    ): CancelablePromise<SourceSettingsResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/sources/{source_id}',
            path: {
                'pod_id': podId,
                'source_id': sourceId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Surface Setup Guide
     * The static pre-creation checklist for a platform (env/OAuth
     * prerequisites) — works before any surface of this platform exists.
     * @param podId
     * @param platform
     * @returns SurfacePlatformSetupGuide Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSetupGuide(
        podId: string,
        platform: string,
    ): CancelablePromise<SurfacePlatformSetupGuide> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surface-setup/{platform}',
            path: {
                'pod_id': podId,
                'platform': platform,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Surfaces
     * List surfaces in the pod. A pod may have several surfaces of the same
     * ``platform`` (different bots/accounts, one per agent); filter by
     * ``platform`` and/or ``agent_name`` to narrow the results.
     * @param podId
     * @param limit
     * @param pageToken
     * @param platform
     * @param agentName
     * @returns AgentSurfaceListResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceList(
        podId: string,
        limit: number = 100,
        pageToken?: (string | null),
        platform?: (string | null),
        agentName?: (string | null),
    ): CancelablePromise<AgentSurfaceListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surfaces',
            path: {
                'pod_id': podId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
                'platform': platform,
                'agent_name': agentName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Surface
     * Create a surface. ``name`` defaults to the lowercased platform and is the
     * pod-unique handle the API addresses it by. A second surface of the same
     * platform has to belong to a different agent: one agent reaches a platform in
     * one place — one Slack app, one WhatsApp number, one Telegram bot.
     * @param podId
     * @param requestBody
     * @returns AgentSurfaceResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceCreate(
        podId: string,
        requestBody: SurfaceCreateRequest,
    ): CancelablePromise<AgentSurfaceResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/surfaces',
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
     * Delete Surface
     * @param podId
     * @param surfaceName
     * @returns void
     * @throws ApiError
     */
    public static agentSurfaceDelete(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/surfaces/{surface_name}',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Surface
     * @param podId
     * @param surfaceName
     * @returns AgentSurfaceResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceGet(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<AgentSurfaceResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surfaces/{surface_name}',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Surface
     * Partially update a surface. Only fields present in the request are
     * applied; the surface's platform and name are immutable.
     *
     * Passing ``account_id`` rebinds the surface to a different connected account
     * — the repair when the account it runs on expires, or its owner leaves the
     * pod. It must be an account the caller owns.
     * @param podId
     * @param surfaceName
     * @param requestBody
     * @returns AgentSurfaceResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceUpdate(
        podId: string,
        surfaceName: string,
        requestBody: SurfaceUpdateRequest,
    ): CancelablePromise<AgentSurfaceResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/surfaces/{surface_name}',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Surface Channels
     * List the channels/groups this surface bot can be configured to respond in.
     *
     * Returns an empty list for platforms without an enumerable channel concept
     * (Telegram groups, WhatsApp, email).
     * @param podId
     * @param surfaceName
     * @returns AvailableSurfaceChannelsResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceChannels(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<AvailableSurfaceChannelsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surfaces/{surface_name}/channels',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Send Surface Message
     * Proactively send a message to a pod member on this surface.
     *
     * Powers notifications from functions/workflows. Reuses the member's existing
     * thread (bots can't cold-DM), and a 404 carries the reason it could not be
     * reached, in the vocabulary the notification API uses.
     * @param podId
     * @param surfaceName
     * @param requestBody
     * @returns SurfaceSendResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSend(
        podId: string,
        surfaceName: string,
        requestBody: SurfaceSendRequest,
    ): CancelablePromise<SurfaceSendResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/surfaces/{surface_name}/send',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Surface Setup
     * Live setup state for an existing surface: static platform checklist plus
     * webhook URL and admin-consent status. Readable with ``AGENT_READ``; the org's
     * own shared secrets in it are not. For the pre-creation checklist (before any
     * surface exists) use ``GET /pods/{pod_id}/surface-setup/{platform}``.
     * @param podId
     * @param surfaceName
     * @returns SurfaceSetupResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSetup(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<SurfaceSetupResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surfaces/{surface_name}/setup',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Start Telegram Managed Bot Setup
     * @param podId
     * @param requestBody
     * @returns TelegramManagedBotSetupResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceTelegramManagedStart(
        podId: string,
        requestBody: TelegramManagedBotSetupRequest,
    ): CancelablePromise<TelegramManagedBotSetupResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/telegram-bot-setups',
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
     * Get Telegram Managed Bot Setup
     * @param podId
     * @param setupId
     * @returns TelegramManagedBotSetupResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceTelegramManagedGet(
        podId: string,
        setupId: string,
    ): CancelablePromise<TelegramManagedBotSetupResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/telegram-bot-setups/{setup_id}',
            path: {
                'pod_id': podId,
                'setup_id': setupId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Widgets
     * @param podId
     * @returns WebWidgetListResponse Successful Response
     * @throws ApiError
     */
    public static agentWebWidgetList(
        podId: string,
    ): CancelablePromise<WebWidgetListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/web-widgets',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Widget
     * @param podId
     * @param requestBody
     * @returns WebWidgetCreatedResponse Successful Response
     * @throws ApiError
     */
    public static agentWebWidgetCreate(
        podId: string,
        requestBody: WebWidgetCreateRequest,
    ): CancelablePromise<WebWidgetCreatedResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/web-widgets',
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
     * Delete Widget
     * @param podId
     * @param widgetId
     * @returns void
     * @throws ApiError
     */
    public static agentWebWidgetDelete(
        podId: string,
        widgetId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/web-widgets/{widget_id}',
            path: {
                'pod_id': podId,
                'widget_id': widgetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Widget
     * @param podId
     * @param widgetId
     * @param requestBody
     * @returns WebWidgetResponse Successful Response
     * @throws ApiError
     */
    public static agentWebWidgetUpdate(
        podId: string,
        widgetId: string,
        requestBody: WebWidgetUpdateRequest,
    ): CancelablePromise<WebWidgetResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/web-widgets/{widget_id}',
            path: {
                'pod_id': podId,
                'widget_id': widgetId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reissue Widget
     * Mint a new signing secret. Tokens signed with the old one stop working.
     *
     * So do the sessions they started: a host session is the old secret's word
     * for who somebody is, and a secret is reissued because that word is no
     * longer trusted.
     * @param podId
     * @param widgetId
     * @returns WebWidgetSecretResponse Successful Response
     * @throws ApiError
     */
    public static agentWebWidgetReissue(
        podId: string,
        widgetId: string,
    ): CancelablePromise<WebWidgetSecretResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/web-widgets/{widget_id}/secret',
            path: {
                'pod_id': podId,
                'widget_id': widgetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Slack App Manifest
     * The Slack app manifest to paste when running your own Slack app.
     *
     * Served rather than copied out of the repo so the URLs always match the
     * deployment answering this request, and the scopes always match the code
     * that will consume the events.
     *
     * Signed-in access is the only gate, and that is enough: every value in here
     * is already public — this deployment's URLs and the scopes its own code
     * asks for. It carries no credential and reveals nothing about a pod: the
     * agent name is supplied by the caller and echoed back, never read from one.
     * @param agentName Name the app after this agent, so a bot made for one agent arrives already called by its name. Defaults to Lemma.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackManifest(
        agentName?: (string | null),
    ): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surface-setup/slack/manifest',
            query: {
                'agent_name': agentName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
