import type { GeneratedClientAdapter } from "../generated.js";
import type { ChannelKind } from "../openapi_client/models/ChannelKind.js";
import type { ChannelPostRequest } from "../openapi_client/models/ChannelPostRequest.js";
import type { ChannelUpdateRequest } from "../openapi_client/models/ChannelUpdateRequest.js";
import type { NotifyLevel } from "../openapi_client/models/NotifyLevel.js";
import { AgentSurfacesService } from "../openapi_client/services/AgentSurfacesService.js";
import { ChannelsService } from "../openapi_client/services/ChannelsService.js";

/**
 * The places a pod's members, contacts and agents talk.
 *
 * A channel is one shared timeline fed by any number of outside sources -- a
 * WhatsApp chat, a Telegram group, a Slack channel, an email thread, a web
 * chat -- or by none: a channel made in Lemma. `DIRECT` is one member and the
 * pod, `CONTACT` one contact and the pod (editors and admins), `GROUP` a chat
 * several people read. Another member's direct channel is never listed.
 */
export class ChannelsNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** The pod's channels, most recently active first, each with what is new in
   *  it for the caller and its last line. Pass the opaque `next_before` back
   *  as `before` for the next page. */
  list(
    podId: string,
    options: {
      kind?: ChannelKind | `${ChannelKind}`;
      archived?: boolean;
      /** Only this contact's channel. */
      contactId?: string;
      limit?: number;
      before?: string;
    } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelList(
        podId,
        options.kind as ChannelKind | undefined,
        options.archived ?? false,
        options.contactId,
        options.limit ?? 50,
        options.before,
      ),
    );
  }

  /** Make a channel in Lemma, with the caller and the members named in it. */
  create(podId: string, body: { title: string; topic?: string | null; members?: string[] }) {
    return this.client.request(() =>
      ChannelsService.channelCreate(podId, { members: [], ...body }),
    );
  }

  /** One channel, with its sources, participants and the caller's settings. */
  get(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelGet(podId, channelId));
  }

  /** Rename, re-describe or archive a group. Who made it, or an admin. */
  update(podId: string, channelId: string, body: ChannelUpdateRequest) {
    return this.client.request(() => ChannelsService.channelUpdate(podId, channelId, body));
  }

  /** Whether a contact's agents answer them. Off, only members do; on hands
   *  them back to the agent at once. Takes `contact.message`. */
  setAgentAnswers(
    podId: string,
    channelId: string,
    on: boolean,
    options: { handBackAfterHours?: number } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelUpdate(podId, channelId, {
        settings: {
          agents_answer: on,
          ...(options.handBackAfterHours !== undefined
            ? { hand_back_after_hours: options.handBackAfterHours }
            : {}),
        },
      }),
    );
  }

  /** Add members of the pod to a group. */
  addMembers(podId: string, channelId: string, members: string[]) {
    return this.client.request(() =>
      ChannelsService.channelParticipantAdd(podId, channelId, { members }),
    );
  }

  /** Leave a channel (the caller's own id), or take somebody out of it. */
  removeMember(podId: string, channelId: string, memberId: string) {
    return this.client.request(() =>
      ChannelsService.channelParticipantRemove(podId, channelId, memberId),
    );
  }

  /** How the caller hears of this channel's lines outside the app. */
  setNotify(
    podId: string,
    channelId: string,
    notify: NotifyLevel | `${NotifyLevel}`,
    via?: string[] | null,
  ) {
    return this.client.request(() =>
      ChannelsService.channelNotifyUpdate(podId, channelId, {
        notify: notify as NotifyLevel,
        notify_via: via ?? null,
      }),
    );
  }

  /** The channel's timeline, oldest first: who said what, when and where. A
   *  thread's replies are left out and counted on its first line. The newest
   *  page unless `before` is the previous page's `next_before`. */
  messages(podId: string, channelId: string, options: { limit?: number; before?: string } = {}) {
    return this.client.request(() =>
      ChannelsService.channelMessageList(podId, channelId, options.limit ?? 50, options.before),
    );
  }

  /** A thread's replies, oldest first. */
  thread(
    podId: string,
    channelId: string,
    messageId: string,
    options: { limit?: number; before?: string } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelMessageThread(
        podId,
        channelId,
        messageId,
        options.limit ?? 50,
        options.before,
      ),
    );
  }

  /** Write in a channel. It goes to one outside place -- `source_id`, or the
   *  one the channel last heard from -- unless `members_only` makes it a note
   *  to members. A named agent answers. */
  post(podId: string, channelId: string, body: ChannelPostRequest) {
    return this.client.request(() => ChannelsService.channelMessagePost(podId, channelId, body));
  }

  /** Change what one of the caller's own lines says. */
  edit(podId: string, channelId: string, messageId: string, text: string) {
    return this.client.request(() =>
      ChannelsService.channelMessageUpdate(podId, channelId, messageId, { text }),
    );
  }

  /** Delete one of the caller's own lines; it keeps its place, empty. */
  delete(podId: string, channelId: string, messageId: string) {
    return this.client.request(() =>
      ChannelsService.channelMessageDelete(podId, channelId, messageId),
    );
  }

  /** Answer what a line asks the caller: `{choice}` or `{text}` for a
   *  question, `{decision}` for an approval, a form's fields. */
  answer(podId: string, channelId: string, messageId: string, answer: Record<string, unknown>) {
    return this.client.request(() =>
      ChannelsService.channelMessageAnswer(podId, channelId, messageId, { answer }),
    );
  }

  /** What is new for the caller across the pod, and what waits on them. */
  inbox(podId: string) {
    return this.client.request(() => ChannelsService.channelInboxGet(podId));
  }

  /** Lines matching every word, newest first, in the channels the caller sees. */
  search(podId: string, q: string, options: { limit?: number } = {}) {
    return this.client.request(() =>
      ChannelsService.channelMessageSearch(podId, q, options.limit ?? 30),
    );
  }

  /** Tell another member of the pod something: a line in their own channel,
   *  carried on to where they last spoke. `to` is a pod member id, user id or
   *  email address. */
  notify(podId: string, body: { to: string; text: string; title?: string | null }) {
    return this.client.request(() => AgentSurfacesService.agentNoticeSend(podId, body));
  }

  /** Note that the caller has read the channel up to now. */
  markRead(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelMarkRead(podId, channelId));
  }
}
