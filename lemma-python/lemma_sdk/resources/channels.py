from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from ..openapi_client.api.channels import (
    channel_create,
    channel_get,
    channel_handler_release,
    channel_handler_take,
    channel_inbox_get,
    channel_list,
    channel_mark_read,
    channel_message_answer,
    channel_message_delete,
    channel_message_list,
    channel_message_post,
    channel_message_search,
    channel_message_thread,
    channel_message_update,
    channel_notify_update,
    channel_participant_add,
    channel_participant_remove,
    channel_update,
)
from ..openapi_client.models.channel_answer_request import ChannelAnswerRequest
from ..openapi_client.models.channel_create_request import ChannelCreateRequest
from ..openapi_client.models.channel_detail_response import ChannelDetailResponse
from ..openapi_client.models.channel_edit_request import ChannelEditRequest
from ..openapi_client.models.channel_kind import ChannelKind
from ..openapi_client.models.channel_list_response import ChannelListResponse
from ..openapi_client.models.channel_message_response import ChannelMessageResponse
from ..openapi_client.models.channel_messages_response import ChannelMessagesResponse
from ..openapi_client.models.channel_post_request import ChannelPostRequest
from ..openapi_client.models.channel_response import ChannelResponse
from ..openapi_client.models.channel_update_request import ChannelUpdateRequest
from ..openapi_client.models.inbox_response import InboxResponse
from ..openapi_client.models.notify_request import NotifyRequest
from ..openapi_client.models.participants_add_request import ParticipantsAddRequest
from ..openapi_client.models.search_response import SearchResponse
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid


def _unset(value: Any) -> Any:
    return UNSET if value is None else value


class PodChannels(BoundResource):
    """The places a pod's members, contacts and agents talk.

    A channel is one shared timeline fed by any number of outside sources -- a
    WhatsApp chat, a Telegram group, a Slack channel, an email thread, a web
    chat -- or by none: a channel made in Lemma. ``DIRECT`` is one member and
    the pod, ``CONTACT`` one contact and the pod (editors and admins), ``GROUP``
    a chat several people read. Another member's direct channel is never
    listed.
    """

    def list(
        self,
        *,
        kind: Literal["DIRECT", "CONTACT", "GROUP"] | None = None,
        archived: bool = False,
        limit: int = 50,
        before: str | None = None,
    ) -> ChannelListResponse:
        """The pod's channels, most recently active first, each with what is new
        in it for the caller and its last line.

        Page by passing the last page's ``next_before`` as ``before``.
        """
        return self._call(
            channel_list,
            self._pod_uuid(),
            kind=ChannelKind(kind) if kind is not None else UNSET,
            archived=archived,
            limit=limit,
            before=_unset(before),
        )

    def create(
        self,
        title: str,
        *,
        topic: str | None = None,
        members: list[str | UUID] | None = None,
    ) -> ChannelDetailResponse:
        """Make a channel in Lemma, with the caller and the members named in it."""
        return self._call(
            channel_create,
            self._pod_uuid(),
            body={
                "title": title,
                "topic": topic,
                "members": [str(as_uuid(member)) for member in members or []],
            },
            body_model=ChannelCreateRequest,
        )

    def get(self, channel_id: str | UUID) -> ChannelDetailResponse:
        """One channel, with its sources, participants, and the caller's settings."""
        return self._call(channel_get, self._pod_uuid(), as_uuid(channel_id))

    def update(
        self,
        channel_id: str | UUID,
        *,
        title: str | None = None,
        topic: str | None = None,
        archived: bool | None = None,
    ) -> ChannelDetailResponse:
        """Rename, re-describe or archive a group. Who made it, or an admin."""
        body = {
            key: value
            for key, value in {
                "title": title,
                "topic": topic,
                "archived": archived,
            }.items()
            if value is not None
        }
        return self._call(
            channel_update,
            self._pod_uuid(),
            as_uuid(channel_id),
            body=body,
            body_model=ChannelUpdateRequest,
        )

    def add_members(
        self, channel_id: str | UUID, members: list[str | UUID]
    ) -> ChannelDetailResponse:
        """Add members of the pod to a group."""
        return self._call(
            channel_participant_add,
            self._pod_uuid(),
            as_uuid(channel_id),
            body={"members": [str(as_uuid(member)) for member in members]},
            body_model=ParticipantsAddRequest,
        )

    def remove_member(self, channel_id: str | UUID, member_id: str | UUID) -> None:
        """Leave a channel (the caller's own id), or take somebody out of it."""
        self._call(
            channel_participant_remove,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(member_id),
        )

    def set_notify(
        self,
        channel_id: str | UUID,
        notify: Literal["ALL", "MENTIONS", "NONE"],
        *,
        via: list[str] | None = None,
    ) -> ChannelDetailResponse:
        """How the caller hears of this channel's lines outside the app."""
        return self._call(
            channel_notify_update,
            self._pod_uuid(),
            as_uuid(channel_id),
            body={"notify": notify, "notify_via": via},
            body_model=NotifyRequest,
        )

    def messages(
        self, channel_id: str | UUID, *, limit: int = 50, before: str | None = None
    ) -> ChannelMessagesResponse:
        """The channel's timeline, oldest first: who said what, when and where.

        A thread's replies are left out and counted on its first line. The
        newest page unless ``before`` is the previous page's ``next_before``.
        """
        return self._call(
            channel_message_list,
            self._pod_uuid(),
            as_uuid(channel_id),
            limit=limit,
            before=_unset(before),
        )

    def thread(
        self,
        channel_id: str | UUID,
        message_id: str | UUID,
        *,
        limit: int = 50,
        before: str | None = None,
    ) -> ChannelMessagesResponse:
        """A thread's replies, oldest first."""
        return self._call(
            channel_message_thread,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
            limit=limit,
            before=_unset(before),
        )

    def post(
        self,
        channel_id: str | UUID,
        text: str,
        *,
        members_only: bool = False,
        source_id: str | UUID | None = None,
        thread_root_id: str | UUID | None = None,
        mentions: list[dict[str, str]] | None = None,
    ) -> ChannelMessageResponse:
        """Write in a channel.

        It goes to one outside place -- ``source_id``, or the one the channel
        last heard from -- unless ``members_only`` makes it a note to members.
        ``mentions`` names people and agents (``{"kind", "id"}``); a named
        agent answers.
        """
        return self._call(
            channel_message_post,
            self._pod_uuid(),
            as_uuid(channel_id),
            body={
                "text": text,
                "members_only": members_only,
                "source_id": str(as_uuid(source_id)) if source_id else None,
                "thread_root_id": (
                    str(as_uuid(thread_root_id)) if thread_root_id else None
                ),
                "mentions": mentions or [],
            },
            body_model=ChannelPostRequest,
        )

    def edit(
        self, channel_id: str | UUID, message_id: str | UUID, text: str
    ) -> ChannelMessageResponse:
        """Change what one of the caller's own lines says."""
        return self._call(
            channel_message_update,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
            body={"text": text},
            body_model=ChannelEditRequest,
        )

    def delete(self, channel_id: str | UUID, message_id: str | UUID) -> None:
        """Delete one of the caller's own lines; it keeps its place, empty."""
        self._call(
            channel_message_delete,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
        )

    def answer(
        self,
        channel_id: str | UUID,
        message_id: str | UUID,
        answer: dict[str, Any],
    ) -> ChannelMessageResponse:
        """Answer what a line asks the caller: ``{"choice"}`` or ``{"text"}``
        for a question, ``{"decision"}`` for an approval, a form's fields."""
        return self._call(
            channel_message_answer,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
            body={"answer": answer},
            body_model=ChannelAnswerRequest,
        )

    def inbox(self) -> InboxResponse:
        """What is new for the caller across the pod, and what waits on them."""
        return self._call(channel_inbox_get, self._pod_uuid())

    def search(self, q: str, *, limit: int = 30) -> SearchResponse:
        """Lines matching every word, newest first, in the channels the caller sees."""
        return self._call(channel_message_search, self._pod_uuid(), q=q, limit=limit)

    def mark_read(self, channel_id: str | UUID) -> None:
        """Note that the caller has read the channel up to now."""
        self._call(channel_mark_read, self._pod_uuid(), as_uuid(channel_id))

    def take(self, channel_id: str | UUID) -> ChannelResponse:
        """Answer a contact's channel by hand: its agents stop answering there.

        Takes ``contact.message``.
        """
        return self._call(channel_handler_take, self._pod_uuid(), as_uuid(channel_id))

    def release(self, channel_id: str | UUID) -> ChannelResponse:
        """Hand a contact's channel back to its agents."""
        return self._call(
            channel_handler_release, self._pod_uuid(), as_uuid(channel_id)
        )
