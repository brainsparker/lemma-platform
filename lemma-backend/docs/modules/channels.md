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
| `channels` | One place per pod: `kind`, a `title`, whose it is (`member_user_id` for `DIRECT`, `contact_id` for `CONTACT`, each unique per pod), `handler_user_id` -- a member who stepped in, while agents stay quiet -- and `last_message_at` |
| `channel_participants` | The members and contacts in a channel, each with `last_read_at`. Anybody who says something there joins it |
| `channel_sources` | The outside places a channel is fed by, keyed by surface, platform, chat and -- for a private chat -- the person's platform id. `surface_id` is kept `SET NULL` so history survives a removed bot. `last_inbound_at` is when somebody last wrote there |
| `channel_messages` | The timeline. `sender_kind` (`MEMBER`, `CONTACT`, `AGENT`) with the sender's id; for an agent, `on_behalf_user_id` or `on_behalf_contact_id` -- whom it answered, and so whose access the answer was made with. `source_id` is where it was said (absent for a line written in Lemma); `members_only` marks a note that is never delivered and never shown outside. A platform message is one line however often it is delivered (unique on source and external message id) |
| `channel_mentions` | Who a line names -- a member, a contact or an agent -- and when they read it: the unread mentions a member is shown |
| `channel_message_deliveries` | Each place a line was sent out to (a source, or a member's own platform for something the pod told them), its status -- `SENT`, `FAILED`, `SKIPPED` with why -- and the platform's id for it, so a reply there finds the line |

A line can be a reply in a thread (`thread_root_id`; a Slack or Teams thread
is one here too), carry files (`parts`), be edited or deleted (it keeps its
place, empty), and name people and agents (`channel_mentions`). A line can put
a question to one person -- `interaction_kind` `ASK`, `APPROVAL` or `FORM`,
`awaits_user_id` / `awaits_contact_id`, `interaction_state` `OPEN` until it is
`ANSWERED`, `CANCELLED` or `EXPIRED` -- which is what the app shows as waiting
on them. A participant hears of a channel outside the app as their `notify`
says (`ALL`, `MENTIONS`, `NONE`). A channel can be archived, and can ask for a
person (`attention_at`, `attention_reason`) until a member steps in. Its `settings` are a JSON
object of the channel's own switches -- whether its agents answer a contact,
how long a member who stepped in holds it -- each key absent until it is set.

An agent's working -- tool calls, reasoning -- is never a channel message. It
stays in the conversation (`agent_messages`).

## Who reads what

One rule, in `domain/projection.py`, which every reader asks:

- **A member** reads everything but one thing: an answer an agent made with
  another member's own access shows only that it was given, and for whom
  (`withheld_from`) -- except in the reader's own channel, and a question put
  to the reader.
- **A contact** -- a run answering them -- reads only the lines of their own
  source, never a note, and never an answer an agent made with a member's own
  access. An agent line whose principal is no longer known counts as a
  member's, so it fails closed.

## Who answers, and where a member's line goes

- **In a member's own channel** the pod's agent answers every line they write
  in Lemma; **in a group or a contact's channel** an agent answers when a line
  names it. The answer is a line in the same thread
  (`agent_surfaces/services/in_app_turns.py`).
- **A member's line** goes to one outside place: the one they pick, or the one
  the channel last heard from (`services/messages.py`); a note to members goes
  nowhere. Writing in a contact's channel steps in: its agents stay quiet until
  the member hands it back. Each place a line was carried to is a
  `channel_message_deliveries` row -- sent, failed, or skipped and why (a
  contact who said stop, WhatsApp's 24 hours, the daily limit).
- **What the pod tells a member** -- a teammate's agent, a notice, an approval,
  a workflow's form -- is a line in their own channel, carried on to where they
  last spoke unless their channel says `NONE`
  (`agent_surfaces/services/direct_messages.py`).

## Questions

A question is answered once, on its line, by the person it waits on
(`POST /{id}/messages/{message_id}/answer`, or the agent's `answer_question`
in their conversation). What answering does depends on who asked:
`contracts/questions.answers_for(kind)` registers a handler per ref prefix --
`tool:` an agent's paused `ask_user` or approval, `wf:` a workflow's form,
`msg:` a question one member's agent asked another -- and the line closes when
the handler has done its part. `ChannelQuestionClosed` announces it, so an
asker waiting on several answers is woken once the last closes.

## API

`/pods/{pod_id}/channels` -- everybody named one way, a `PersonRef`
(`MEMBER`, `CONTACT` or `AGENT`, an id, a name):

- `GET` lists the channels the caller may see, most recently active first, by
  `kind`, `archived` or `contact_id`, each with its unread lines, mentions and
  questions waiting on the caller, and its last line; pages with an opaque
  `before` / `next_before`. Another member's `DIRECT` channel is never listed,
  and reads as not found; nor is a `CONTACT` channel, to a member without
  `contact.read` (editors and admins hold it). The caller's own channel is
  made the first time they list.
- `POST` makes a channel in Lemma; `GET`/`PATCH /{id}` reads it with its
  sources, participants and the caller's settings, or renames, re-describes or
  archives it (who made it, or an admin); `POST /{id}/participants` and
  `DELETE /{id}/participants/{member_id}`; `PUT /{id}/me` sets how the caller
  is notified; `POST /{id}/read` marks it read.
- `GET /{id}/messages` (oldest first, paged backwards, a thread's replies
  counted on its line), `GET /{id}/messages/{message_id}/thread`,
  `POST /{id}/messages` (text, a note to members, a source, a thread,
  mentions), `PATCH`/`DELETE` one's own line, and `POST …/answer`.
- A contact's channel is answered by hand two ways, both taking
  `contact.message`: writing there steps in, and its agents wait while the
  member is in the conversation (`handler`, let go after the channel's
  `hand_back_after_hours` of quiet); `PATCH /{id}` with
  `{"settings": {"agents_answer": false}}` switches its agents off until a
  member switches them on, which hands the contact back at once.
- `GET /pods/{pod_id}/inbox` -- what is new across the pod and what waits on
  the caller -- and `GET /pods/{pod_id}/channels/search?q=` (Postgres full
  text, in the channels the caller sees).

A group source's settings -- whether people outside the pod are answered
there, and which member answers for them -- and bringing the bot into a group
(a WhatsApp group, a Telegram link) are the bot's side: `/pods/{pod_id}/sources`
in `agent_surfaces`.

## Contracts

`contracts/` is what other modules may ask: `source_for` (a place's source,
with its channel made the first time), `post` (a line, once per platform
message, announced as `ChannelMessagePosted`), `background` (the recent lines
a reader may see), `contracts/lines` (a member's channels, recent lines,
search, a platform thread's root), `contracts/questions` (the answer registry,
open questions), `contracts/deliveries`, `may_read`, and lookups by id.
`agent_surfaces` writes every message it hears and every answer it delivers
through them; `agent`'s channel tools read and post through them.

`contracts/facts` publishes `ChannelMessagePosted` and
`ChannelQuestionClosed`. The module subscribes to `ContactMerged`
(`events/contact_merged_work.py`): a merged contact's channel, participation
and lines move to the contact that survived.

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
- **Delivery.** `agent_surfaces` sends and receives; a line is written once the
  platform took it.

Nothing here is deleted automatically.
