import type { GeneratedClientAdapter } from "../generated.js";
import type { SourceUpdateRequest } from "../openapi_client/models/SourceUpdateRequest.js";
import type { WhatsAppGroupRequest } from "../openapi_client/models/WhatsAppGroupRequest.js";
import { AgentSurfacesService } from "../openapi_client/services/AgentSurfacesService.js";

/**
 * The outside places a pod's group channels are fed by, from the bot's side.
 *
 * A WhatsApp or Telegram group, a Slack or Teams channel, a group DM: each is a
 * source of a GROUP channel, read and written in through `channels`. Here is
 * only what the bot keeps about it -- whether people outside the pod are
 * answered there, and which member answers for them -- and the two ways to
 * bring the bot into a group: `startWhatsAppGroup` (a business number creates
 * groups, it cannot join one; it comes back `pending` until WhatsApp confirms
 * it) and `telegramLink` (a one-use, hour-long link that adds the bot to a
 * group the caller picks).
 */
export class PodSourcesNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** Every group one bot is in, with each one's settings, newest first. */
  list(podId: string, surfaceName: string) {
    return this.client.request(() => AgentSurfacesService.agentSourceList(podId, surfaceName));
  }

  /** A group source's settings: who answers its outsiders, and from what. */
  get(podId: string, sourceId: string) {
    return this.client.request(() => AgentSurfacesService.agentSourceGet(podId, sourceId));
  }

  /** Switch outsiders on or off in one group, or take it over. */
  update(podId: string, sourceId: string, payload: SourceUpdateRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSourceUpdate(podId, sourceId, payload),
    );
  }

  /** Start a WhatsApp group with the pod's bot in it: a new group channel. */
  startWhatsAppGroup(podId: string, payload: WhatsAppGroupRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSourceWhatsappGroupStart(podId, payload),
    );
  }

  /** A one-use, hour-long Telegram link that adds the bot to a group. */
  telegramLink(podId: string, surfaceName: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSourceTelegramLinkCreate(podId, { surface_name: surfaceName }),
    );
  }
}
