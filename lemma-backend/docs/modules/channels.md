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

- `/channels` `GET` (kind, archived, contact, assignee, status, joined;
  cursor) · `POST`; `/channels/me` `GET` · `POST` (the caller's own channel);
  `/channels/{c}` `GET` · `PATCH` · `DELETE` (groups made in Lemma).
- `/channels/{c}/members` `POST`; `/members/me` `POST` (join) · `PATCH`
  (notify, notify_via, muted_until) · `DELETE` (leave);
  `/members/{kind}:{id}` `PATCH` · `DELETE`; `/channels/{c}/read` `POST {seq}`.
- `/channels/{c}/messages` `GET` (`before_seq` / `after_seq`) · `POST`;
  `/messages/{m}` `PATCH` · `DELETE`; `/thread`; `/answer`;
  `/deliveries` `GET` and `/deliveries/{d}/retry`.
- `/inbox` (counts, waiting, mentions) and `/search` (Postgres full text, in
  the channels the caller sees).

A group source's bot-side settings -- whether people outside the pod are
answered there, who answers for them -- and bringing the bot into a group
(a WhatsApp group, a Telegram link) are `agent_surfaces`':
`/pods/{pod_id}/sources`.

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
and lines move to the survivor, renumbered) and `PodMemberRemoved` (they are
taken out of its channels, unassigned, and their questions cancelled), and
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
