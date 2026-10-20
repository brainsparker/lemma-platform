from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.file_part import FilePart
    from ..models.line_delivery import LineDelivery
    from ..models.link_part import LinkPart
    from ..models.mention_part import MentionPart
    from ..models.person_ref import PersonRef
    from ..models.question_response import QuestionResponse


T = TypeVar("T", bound="ChannelMessageResponse")


@_attrs_define
class ChannelMessageResponse:
    """
    Attributes:
        channel_id (UUID):
        created_at (datetime.datetime):
        id (UUID):
        sender (PersonRef): Somebody in a channel, said one way everywhere: a member of the pod, one
            of its contacts, or one of its agents. There is nobody else -- everybody
            outside the pod is a contact.
        seq (int): Its place in the channel's timeline.
        client_message_id (None | str | Unset):
        conversation_id (None | Unset | UUID):
        deleted (bool | Unset):  Default: False.
        delivered (list[LineDelivery] | None | Unset): Only on the reader's own line: each place outside Lemma it went
            to, and what became of it. Pushes to members are not listed here (`…/deliveries` has every send).
        edited_at (datetime.datetime | None | Unset):
        last_reply_at (datetime.datetime | None | Unset):
        members_only (bool | Unset):  Default: False.
        on_behalf_of (None | PersonRef | Unset): For an agent's line: the member or contact it acted for, whose access
            the answer was made with.
        parts (list[FilePart | LinkPart | MentionPart] | Unset):
        question (None | QuestionResponse | Unset):
        reply_count (int | Unset): On a thread's first line. Default: 0.
        reply_to_id (None | Unset | UUID):
        source_id (None | Unset | UUID): Where it was said or sent; absent for a line only in Lemma.
        text (None | str | Unset): Absent where the line is withheld from the reader, or deleted.
        thread_root_id (None | Unset | UUID): The line this one is a reply in the thread of.
        withheld (bool | Unset): An answer an agent made with another member's own access: the reader sees that it was
            given, and for whom, not what it said. Default: False.
    """

    channel_id: UUID
    created_at: datetime.datetime
    id: UUID
    sender: PersonRef
    seq: int
    client_message_id: None | str | Unset = UNSET
    conversation_id: None | Unset | UUID = UNSET
    deleted: bool | Unset = False
    delivered: list[LineDelivery] | None | Unset = UNSET
    edited_at: datetime.datetime | None | Unset = UNSET
    last_reply_at: datetime.datetime | None | Unset = UNSET
    members_only: bool | Unset = False
    on_behalf_of: None | PersonRef | Unset = UNSET
    parts: list[FilePart | LinkPart | MentionPart] | Unset = UNSET
    question: None | QuestionResponse | Unset = UNSET
    reply_count: int | Unset = 0
    reply_to_id: None | Unset | UUID = UNSET
    source_id: None | Unset | UUID = UNSET
    text: None | str | Unset = UNSET
    thread_root_id: None | Unset | UUID = UNSET
    withheld: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.file_part import FilePart
        from ..models.mention_part import MentionPart
        from ..models.person_ref import PersonRef
        from ..models.question_response import QuestionResponse

        channel_id = str(self.channel_id)

        created_at = self.created_at.isoformat()

        id = str(self.id)

        sender = self.sender.to_dict()

        seq = self.seq

        client_message_id: None | str | Unset
        if isinstance(self.client_message_id, Unset):
            client_message_id = UNSET
        else:
            client_message_id = self.client_message_id

        conversation_id: None | str | Unset
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        elif isinstance(self.conversation_id, UUID):
            conversation_id = str(self.conversation_id)
        else:
            conversation_id = self.conversation_id

        deleted = self.deleted

        delivered: list[dict[str, Any]] | None | Unset
        if isinstance(self.delivered, Unset):
            delivered = UNSET
        elif isinstance(self.delivered, list):
            delivered = []
            for delivered_type_0_item_data in self.delivered:
                delivered_type_0_item = delivered_type_0_item_data.to_dict()
                delivered.append(delivered_type_0_item)

        else:
            delivered = self.delivered

        edited_at: None | str | Unset
        if isinstance(self.edited_at, Unset):
            edited_at = UNSET
        elif isinstance(self.edited_at, datetime.datetime):
            edited_at = self.edited_at.isoformat()
        else:
            edited_at = self.edited_at

        last_reply_at: None | str | Unset
        if isinstance(self.last_reply_at, Unset):
            last_reply_at = UNSET
        elif isinstance(self.last_reply_at, datetime.datetime):
            last_reply_at = self.last_reply_at.isoformat()
        else:
            last_reply_at = self.last_reply_at

        members_only = self.members_only

        on_behalf_of: dict[str, Any] | None | Unset
        if isinstance(self.on_behalf_of, Unset):
            on_behalf_of = UNSET
        elif isinstance(self.on_behalf_of, PersonRef):
            on_behalf_of = self.on_behalf_of.to_dict()
        else:
            on_behalf_of = self.on_behalf_of

        parts: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.parts, Unset):
            parts = []
            for parts_item_data in self.parts:
                parts_item: dict[str, Any]
                if isinstance(parts_item_data, MentionPart):
                    parts_item = parts_item_data.to_dict()
                elif isinstance(parts_item_data, FilePart):
                    parts_item = parts_item_data.to_dict()
                else:
                    parts_item = parts_item_data.to_dict()

                parts.append(parts_item)

        question: dict[str, Any] | None | Unset
        if isinstance(self.question, Unset):
            question = UNSET
        elif isinstance(self.question, QuestionResponse):
            question = self.question.to_dict()
        else:
            question = self.question

        reply_count = self.reply_count

        reply_to_id: None | str | Unset
        if isinstance(self.reply_to_id, Unset):
            reply_to_id = UNSET
        elif isinstance(self.reply_to_id, UUID):
            reply_to_id = str(self.reply_to_id)
        else:
            reply_to_id = self.reply_to_id

        source_id: None | str | Unset
        if isinstance(self.source_id, Unset):
            source_id = UNSET
        elif isinstance(self.source_id, UUID):
            source_id = str(self.source_id)
        else:
            source_id = self.source_id

        text: None | str | Unset
        if isinstance(self.text, Unset):
            text = UNSET
        else:
            text = self.text

        thread_root_id: None | str | Unset
        if isinstance(self.thread_root_id, Unset):
            thread_root_id = UNSET
        elif isinstance(self.thread_root_id, UUID):
            thread_root_id = str(self.thread_root_id)
        else:
            thread_root_id = self.thread_root_id

        withheld = self.withheld

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel_id": channel_id,
                "created_at": created_at,
                "id": id,
                "sender": sender,
                "seq": seq,
            }
        )
        if client_message_id is not UNSET:
            field_dict["client_message_id"] = client_message_id
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id
        if deleted is not UNSET:
            field_dict["deleted"] = deleted
        if delivered is not UNSET:
            field_dict["delivered"] = delivered
        if edited_at is not UNSET:
            field_dict["edited_at"] = edited_at
        if last_reply_at is not UNSET:
            field_dict["last_reply_at"] = last_reply_at
        if members_only is not UNSET:
            field_dict["members_only"] = members_only
        if on_behalf_of is not UNSET:
            field_dict["on_behalf_of"] = on_behalf_of
        if parts is not UNSET:
            field_dict["parts"] = parts
        if question is not UNSET:
            field_dict["question"] = question
        if reply_count is not UNSET:
            field_dict["reply_count"] = reply_count
        if reply_to_id is not UNSET:
            field_dict["reply_to_id"] = reply_to_id
        if source_id is not UNSET:
            field_dict["source_id"] = source_id
        if text is not UNSET:
            field_dict["text"] = text
        if thread_root_id is not UNSET:
            field_dict["thread_root_id"] = thread_root_id
        if withheld is not UNSET:
            field_dict["withheld"] = withheld

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.file_part import FilePart
        from ..models.line_delivery import LineDelivery
        from ..models.link_part import LinkPart
        from ..models.mention_part import MentionPart
        from ..models.person_ref import PersonRef
        from ..models.question_response import QuestionResponse

        d = dict(src_dict)
        channel_id = UUID(d.pop("channel_id"))

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        sender = PersonRef.from_dict(d.pop("sender"))

        seq = d.pop("seq")

        def _parse_client_message_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        client_message_id = _parse_client_message_id(d.pop("client_message_id", UNSET))

        def _parse_conversation_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                conversation_id_type_0 = UUID(data)

                return conversation_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        conversation_id = _parse_conversation_id(d.pop("conversation_id", UNSET))

        deleted = d.pop("deleted", UNSET)

        def _parse_delivered(data: object) -> list[LineDelivery] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                delivered_type_0 = []
                _delivered_type_0 = data
                for delivered_type_0_item_data in _delivered_type_0:
                    delivered_type_0_item = LineDelivery.from_dict(
                        delivered_type_0_item_data
                    )

                    delivered_type_0.append(delivered_type_0_item)

                return delivered_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[LineDelivery] | None | Unset, data)

        delivered = _parse_delivered(d.pop("delivered", UNSET))

        def _parse_edited_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                edited_at_type_0 = isoparse(data)

                return edited_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        edited_at = _parse_edited_at(d.pop("edited_at", UNSET))

        def _parse_last_reply_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_reply_at_type_0 = isoparse(data)

                return last_reply_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_reply_at = _parse_last_reply_at(d.pop("last_reply_at", UNSET))

        members_only = d.pop("members_only", UNSET)

        def _parse_on_behalf_of(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                on_behalf_of_type_0 = PersonRef.from_dict(data)

                return on_behalf_of_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        on_behalf_of = _parse_on_behalf_of(d.pop("on_behalf_of", UNSET))

        _parts = d.pop("parts", UNSET)
        parts: list[FilePart | LinkPart | MentionPart] | Unset = UNSET
        if _parts is not UNSET:
            parts = []
            for parts_item_data in _parts:

                def _parse_parts_item(
                    data: object,
                ) -> FilePart | LinkPart | MentionPart:
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        parts_item_type_0 = MentionPart.from_dict(data)

                        return parts_item_type_0
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        parts_item_type_1 = FilePart.from_dict(data)

                        return parts_item_type_1
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    if not isinstance(data, dict):
                        raise TypeError()
                    parts_item_type_2 = LinkPart.from_dict(data)

                    return parts_item_type_2

                parts_item = _parse_parts_item(parts_item_data)

                parts.append(parts_item)

        def _parse_question(data: object) -> None | QuestionResponse | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                question_type_0 = QuestionResponse.from_dict(data)

                return question_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | QuestionResponse | Unset, data)

        question = _parse_question(d.pop("question", UNSET))

        reply_count = d.pop("reply_count", UNSET)

        def _parse_reply_to_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                reply_to_id_type_0 = UUID(data)

                return reply_to_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        reply_to_id = _parse_reply_to_id(d.pop("reply_to_id", UNSET))

        def _parse_source_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                source_id_type_0 = UUID(data)

                return source_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        source_id = _parse_source_id(d.pop("source_id", UNSET))

        def _parse_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        text = _parse_text(d.pop("text", UNSET))

        def _parse_thread_root_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                thread_root_id_type_0 = UUID(data)

                return thread_root_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        thread_root_id = _parse_thread_root_id(d.pop("thread_root_id", UNSET))

        withheld = d.pop("withheld", UNSET)

        channel_message_response = cls(
            channel_id=channel_id,
            created_at=created_at,
            id=id,
            sender=sender,
            seq=seq,
            client_message_id=client_message_id,
            conversation_id=conversation_id,
            deleted=deleted,
            delivered=delivered,
            edited_at=edited_at,
            last_reply_at=last_reply_at,
            members_only=members_only,
            on_behalf_of=on_behalf_of,
            parts=parts,
            question=question,
            reply_count=reply_count,
            reply_to_id=reply_to_id,
            source_id=source_id,
            text=text,
            thread_root_id=thread_root_id,
            withheld=withheld,
        )

        channel_message_response.additional_properties = d
        return channel_message_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
