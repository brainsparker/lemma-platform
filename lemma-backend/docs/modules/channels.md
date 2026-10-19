# Channels module

The places a pod's members, contacts and agents talk. A **channel** is one
shared timeline -- who said what, when, and where -- fed by any number of
outside **sources**: a WhatsApp or Telegram chat, a Telegram or WhatsApp group,
a Slack or Teams channel or group DM, an email thread, a web chat -- or by
none, a channel made in Lemma. A **conversation** is one agent
working for one person -- a member or a contact -- and in a channel there is
one per agent and person, however many threads, numbers or days they write
across.

Three kinds, decided by who the channel is about:

| Kind | Holds |
| --- | --- |
| `DIRECT` | One member and the pod: their private chats on every platform |
| `CONTACT` | One contact and the pod: their private chats, web chat and emails |
| `GROUP` | A chat several people read: a group, a shared channel, an email thread with others on it |

A source joins a channel once, the first time its place is heard, and stays:
a private chat is keyed by the person on it rather than the address it came
to, so a chat that arrives on another pool number is the same source; a group
is keyed by the chat. Nothing infers that two places are one. Replies go back
only to the source they answer -- nothing is mirrored between sources.

In-app chats with an agent, sub-agents and scheduled runs are not in a
channel: they are private workspaces, not shared places.

## What it owns

| Table | Meaning |
| --- | --- |
| `external_accounts` | An account a pod's bot speaks through -- a Slack team, a Teams tenant, a bot, an inbox, a WhatsApp number, a web widget -- keyed per pod by platform and `account_key`. A place belongs to an account rather than a surface, so a reinstalled bot keeps its places; `surface_id` is the installation that reaches it now |
| `channels` | One place per pod: `kind`, `visibility` (`PUBLIC` or `PRIVATE`; only a group may be public), a `title` and `topic`, whose it is (`member_user_id` for `DIRECT`, `contact_id` for `CONTACT`, each unique per pod), when it was archived. A contact's channel is also an inbox item: `agents_answer`, `hand_back_after_hours`, its `assignee_user_id` (who answers by hand, and when they last wrote), `status` and a call for a person (`attention_at`, `attention_reason`). The timeline's head: `last_seq`, `last_line_id`, `last_line_at` |
| `channel_members` | Who is in a channel -- a member, a contact or an agent -- with their `role`, how a person hears of it outside the app (`notify`, `notify_via`, `muted_until`) and, for an agent, when it answers (`answers`) |
| `channel_read_state` | How far each member has read each channel: `last_read_seq`, so unread is exact |
| `channel_sources` | The outside places a channel is fed by, in an account, keyed by chat and -- for a private chat -- the person's platform id. Whether the platform keeps it to its members (`is_private`), how to answer it (`delivery_ref`) and, for a group, who answers for its outsiders |
| `channel_messages` | The timeline, numbered per channel (`seq`, gapless). `sender_kind` (`MEMBER`, `CONTACT`, `AGENT`) with the sender's id; for an agent, `on_behalf_user_id` or `on_behalf_contact_id` -- whom it answered, and so whose access the answer was made with. `source_id` is where it was said (absent for a line written in Lemma); `members_only` marks a note never delivered or shown outside; typed `parts` (mentions, files, links). One line per `client_message_id`, and per platform message |
| `channel_mentions` | Who a line names -- a member, a contact or an agent -- at its `seq`: the unread mentions a member is shown |
| `channel_questions` | A question a line puts to one person: its `ref` names what asked it and is unique per pod, `kind` is `ASK`, `APPROVAL` or `FORM`, and `state` runs `OPEN` → `ANSWERING` → `ANSWERED`, or `CANCELLED` / `EXPIRED`, with the answer that took effect |
| `channel_message_deliveries` | Every send of a line outside Lemma -- to a place, or a push to a member -- unique per line and target: `PENDING`, `SENT`, `FAILED` (due again after a backoff), `SKIPPED` (and why) or `DEAD` |

A line can be a reply in a thread (`thread_root_id`; a Slack or Teams thread
is one here too), be edited or deleted (it keeps its place, empty), and name
people and agents. An agent's working -- tool calls, reasoning -- is never a
channel message. It stays in the conversation (`agent_messages`).

## Who sees what

One rule, twice: `domain/access.may_see` and its SQL twin
`infrastructure/access.visible_to`, which every read path uses.

- A `DIRECT` channel is its member's alone.
- A `CONTACT` channel is every member's who holds `contact.read`.
- A `GROUP` is everybody's in the pod when public, its members' when private.
  A group mirrored from a platform is private when the platform keeps it to
  its members; one made in Lemma is public unless made private.

### Permissions

On top of seeing a channel, one table (`api/access.py` holds the same one).
`channel.manage` implies `channel.write`, which implies `channel.read`;
`contact.message` implies `contact.read`.

| What | Takes |
|---|---|
| Reading channels, lines, members, threads, deliveries, the inbox, search | `channel.read` |
| One's own channel (`/channels/me`); joining a public group or a contact's channel; leaving; one's own notify settings; marking read | `channel.read` |
| Joining a private group nobody let them into, on request (logged: `channels.member.joined_private_by_right.observed`) | `channel.manage` |
| Answering a question | `channel.read`, and being the person it waits on |
| Writing, editing and deleting one's own lines; starting a group; retrying one's own line's delivery; notices | `channel.write` (+ `contact.message` in a `CONTACT` channel) |
| A group's title, topic, visibility, archive, delete, members, roles, agents | `channel.write` and being its owner -- or `channel.manage` |
| A contact's channel: assignee, status, the agent switch, the hand-back window | `channel.write` + `contact.message` |
| Seeing a `CONTACT` channel at all | `contact.read` |
| A group source's settings (`/channels/{c}/sources/{s}`) | the bot's `agent.read`; changing them, `agent.update` and answering for the group -- or nobody in the pod does, or a pod admin |

| Role | Holds |
|---|---|
| Viewer | `channel.read` |
| User | `channel.write` |
| Editor | `channel.manage`, `contact.message` |
| Admin | everything an editor holds |

Viewers read and answer what is asked of them; users talk in the pod and keep
the groups they make; editors -- who already change the pod's agents and apps
-- keep every group, and are the ones who speak to contacts. Another pod
linked to this one never holds `channel.read` here: a channel carries what
contacts said to this pod.

Within what a reader sees, `domain/projection.py` decides what a run is shown:
an answer an agent made with another member's own access shows only that it
was given; a contact's run reads only its own source, never a note.

## Posting a line

`services/posting.Timeline.post` is the only way a line is written, and does
everything that follows in the same transaction: numbers it, seats whoever
said it, records who it names and what it asks, moves a contact's channel
along (a member writing to them is assigned and the channel waits on the
contact; the contact writing reopens it), plans its deliveries -- to the
outside places it is for, and pushes to the members who hear of it -- and
announces it (`ChannelMessagePosted`, `ChannelDeliveryDue` per delivery).

Pushes go to a member for: a line naming them; a question waiting on them; a
line in their own channel they did not write; a contact's line in a channel
assigned to them; and, set to `ALL`, any line written in Lemma in a group
they are in. `MENTIONS` and a note for members keep only the first two;
`NONE` and a mute stop them all. Sending is `agent_surfaces`'
(`services/channel_deliveries.py`); the delivery row is the only record.

Agents answer a member's line written in Lemma when it names them, when they
answer every line in the channel, or -- in a member's own channel -- the pod's
agent; `agent` starts one turn per line and agent.

## Live

Channels provides the pod live socket's `channel` and `inbox` topics and its
`typing` and `read` calls ([live.md](live.md) has the protocol). Every change a
channel's watchers are shown takes the next number of the channel's change
counter (`channels.last_rev`) under its row lock and stamps what changed
(`channel_messages.rev`, `channels.changed_rev` for its own fields and
members) -- `infrastructure/revisions.py`. The counter is taken before the
line or question it covers is touched, in the order a new line takes the
channel lock. A per-process hub per pod (`services/live/hub.py`) hears the
commit's signal and reads `rev > its cursor`, so a missed signal costs only
latency. Lines go out through the REST mapper (`message_response`) for each
reader, so the socket shows exactly what a GET would. An agent answering a
line in Lemma says so (`contracts/live.announce_answering`), and the hub
relays the run's tokens to the member it answers as `line.streaming`.

## Questions

A question is answered once, by the person it waits on
(`POST …/messages/{message_id}/answer`, or the agent's `answer_question`;
an approval only by the person's own hand). The answer is claimed first, so a
second tap finds it taken; the asker registered by ref prefix
(`contracts/questions.answers_for`) takes it -- `tool:` an agent's paused
`ask_user` or approval, `wf:` a workflow's form, `msg:` a question one
member's agent asked another -- and the question closes with what took
effect. `ChannelQuestionClosed` announces every close; a sweeper expires
questions whose time is up.

## API

Under `/pods/{pod_id}` -- everybody named one way, a `PersonRef`
(`MEMBER`, `CONTACT` or `AGENT`, an id, a name). PATCH: absent is unchanged,
`null` clears. Creates take an `Idempotency-Key`; GETs never write.

- `/channels` `GET` (kind, archived, contact, assignee -- `me` or a user
  id --, unassigned, status, `needs_person` -- contacts waiting on a person:
  open, unassigned, the agent off or asking for one --, joined,
  `source_surface`; cursor) · `POST`; `/channels/me`
  `GET` · `POST` (the caller's own channel: made when they join the pod, by
  `POST` for a member from before); `/channels/{c}` `GET` · `PATCH`
  (`assignee` takes `me`) · `DELETE` (groups made in Lemma).
- `/channels/{c}/members` `POST`; `/members/me` `POST` (join -- a private
  group too, for `channel.manage`) · `PATCH` (notify, notify_via,
  muted_until) · `DELETE` (leave);
  `/members/{kind}:{id}` `PATCH` · `DELETE`; `/channels/{c}/read` `POST {seq}`.
- `/channels/{c}/messages` `GET` (`before_seq` / `after_seq`) · `POST`;
  `/messages/{m}` `PATCH` · `DELETE`; `/thread`; `/answer`;
  `/deliveries` `GET` and `/deliveries/{d}/retry`;
  `/files/{file_id}` `GET` (below). A reader's own lines carry `delivered`:
  each place outside Lemma the line went to, with its status and why --
  sent again on the socket when a send moves.
- `/inbox` (counts, waiting, mentions) and `/search` (Postgres full text, in
  the channels the caller sees).
- `/pods/{pod_id}/members/{member}/notices` `POST {text}` (`agent_surfaces`'
  route): tell another member something -- a line from the pod's agent on the
  caller's behalf in the member's own channel, pushed like any of theirs.
  `member` is a pod member id, a user id or an email.

A group source's bot-side settings -- whether people outside the pod are
answered there, who answers for them -- and bringing the bot into a group
are under the channels too, but `agent_surfaces`' routes, since they talk to
the platforms: `/channels/{c}/sources/{s}` `GET` · `PATCH` (`answers_outsiders`,
`owner: "me"`), `/channels/whatsapp-groups` `POST` (the channel is made with
it; `Idempotency-Key`), `/channels/telegram-links` `POST`. They are included
before this module's routers, so the literal segments win over
`/channels/{channel_id}`. The bot's settings page lists its groups with
`GET /channels?source_surface={name}`: each channel carries its
`source_settings`, from the lister `agent_surfaces` registers
(`contracts/source_settings`). A holder of `channel.manage` is listed the
bot's private groups they are not in too -- `joined: false`, with no last line
and nothing unread -- so a pod admin can find a group and join it to take it
over. Every channel row says whether the reader is in it (`joined`).

Creates replay by `Idempotency-Key` -- channels, lines (`client_message_id`
or the key), notices, outreach, a WhatsApp group start -- and a key is the
caller's own: two members who pick one key never name one line.

## Files on a line

A `file` part names a pod file, and whoever writes the line must be able to
read it (the datastore's own rule, with their access) -- else the line is
refused, 422. It is stored by the file's id (`file_id`) and its own path, not
the `/me/…` it was written as. Readers read it through the line,
`GET …/messages/{m}/files/{file_id}`: whoever may see the line (not where it
is withheld from them), as its writer reads the file -- gone once the line
is, or once the writer can no longer read it. So a file shared in a private
group, a member's own channel or a contact's is read by that channel's
readers and nobody else. The app uploads a channel's attachments to the
writer's own `/me/channels/{channel_id}/`, which no other member can open
directly (`api/attachments.py`). Only raster images are served inline;
anything else is a download.

## Contracts

`contracts/` is what other modules may ask: `source_for` (a place's source,
with its channel made the first time), `post` (the write path above),
`background` (the recent lines a reader may see), `contracts/lines` (visibility,
channels, pages, search, a platform thread's root), `contracts/places`
(account-scoped place lookups), `contracts/questions` (the answer registry,
open questions), `contracts/deliveries` (claim, record, retry), and lookups by
id. `contracts/facts` publishes `ChannelMessagePosted`, `ChannelDeliveryDue`
and `ChannelQuestionClosed`.

The module subscribes to `ContactMerged` (a merged contact's channel, members
and lines move to the survivor, renumbered), `PodMemberAdded` (their own
channel is made, or brought back from the archive) and `PodMemberRemoved`
(they are taken out of its channels, unassigned, their questions cancelled
and their own channel archived), and
runs two sweeps a minute: questions that expired, deliveries due again.

## What it does not own

- **Which place a message came from, and whose it is.** `agent_surfaces`
  decides (`services/channel_places.py`): a private chat belongs to the
  member's or the contact's channel, a group to one of its own.
- **The conversation.** `agent` owns it, and its `channel_id` column;
  `agent/contracts/channel_conversations` finds a person's conversation in a
  channel.
- **Who a contact is.** `contacts` does. A stranger in a group is made a
  contact the first time they speak there, whether or not the bot answers
  strangers, so every line has a sender.
- **Sending.** `agent_surfaces` sends and receives: it claims a delivery,
  calls the platform, and records what became of it.

Nothing here is deleted automatically.
