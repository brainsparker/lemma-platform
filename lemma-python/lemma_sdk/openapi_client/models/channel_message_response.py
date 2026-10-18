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
    from ..models.channel_message_response_parts_item import (
        ChannelMessageResponsePartsItem,
    )
    from ..models.interaction_response import InteractionResponse
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="ChannelMessageResponse")


@_attrs_define
class ChannelMessageResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        sender (PersonRef): Somebody in a channel, said one way everywhere: a member of the pod, one
            of its contacts, or one of its agents. There is nobody else -- everybody
            outside the pod is a contact.
        conversation_id (None | Unset | UUID): For an agent's line: the conversation it came from.
        deleted (bool | Unset):  Default: False.
        edited_at (datetime.datetime | None | Unset):
        interaction (InteractionResponse | None | Unset):
        last_reply_at (datetime.datetime | None | Unset):
        members_only (bool | Unset):  Default: False.
        on_behalf_of (None | PersonRef | Unset): For an agent's line: the member or contact it acted for, whose access
            the answer was made with.
        parts (list[ChannelMessageResponsePartsItem] | Unset):
        reply_count (int | Unset): On a thread's first line. Default: 0.
        reply_to_id (None | Unset | UUID):
        source_id (None | Unset | UUID): Where it was said; absent for a line written in Lemma.
        text (None | str | Unset): Absent where the line is withheld from the reader, or deleted.
        thread_root_id (None | Unset | UUID): The line this one is a reply in the thread of.
        withheld (bool | Unset): An answer an agent made with another member's own access: the reader sees that it was
            given, and for whom, not what it said. Default: False.
    """

    created_at: datetime.datetime
    id: UUID
    sender: PersonRef
    conversation_id: None | Unset | UUID = UNSET
    deleted: bool | Unset = False
    edited_at: datetime.datetime | None | Unset = UNSET
    interaction: InteractionResponse | None | Unset = UNSET
    last_reply_at: datetime.datetime | None | Unset = UNSET
    members_only: bool | Unset = False
    on_behalf_of: None | PersonRef | Unset = UNSET
    parts: list[ChannelMessageResponsePartsItem] | Unset = UNSET
    reply_count: int | Unset = 0
    reply_to_id: None | Unset | UUID = UNSET
    source_id: None | Unset | UUID = UNSET
    text: None | str | Unset = UNSET
    thread_root_id: None | Unset | UUID = UNSET
    withheld: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.interaction_response import InteractionResponse
        from ..models.person_ref import PersonRef

        created_at = self.created_at.isoformat()

        id = str(self.id)

        sender = self.sender.to_dict()

        conversation_id: None | str | Unset
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        elif isinstance(self.conversation_id, UUID):
            conversation_id = str(self.conversation_id)
        else:
            conversation_id = self.conversation_id

        deleted = self.deleted

        edited_at: None | str | Unset
        if isinstance(self.edited_at, Unset):
            edited_at = UNSET
        elif isinstance(self.edited_at, datetime.datetime):
            edited_at = self.edited_at.isoformat()
        else:
            edited_at = self.edited_at

        interaction: dict[str, Any] | None | Unset
        if isinstance(self.interaction, Unset):
            interaction = UNSET
        elif isinstance(self.interaction, InteractionResponse):
            interaction = self.interaction.to_dict()
        else:
            interaction = self.interaction

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
                parts_item = parts_item_data.to_dict()
                parts.append(parts_item)

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
                "created_at": created_at,
                "id": id,
                "sender": sender,
            }
        )
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id
        if deleted is not UNSET:
            field_dict["deleted"] = deleted
        if edited_at is not UNSET:
            field_dict["edited_at"] = edited_at
        if interaction is not UNSET:
            field_dict["interaction"] = interaction
        if last_reply_at is not UNSET:
            field_dict["last_reply_at"] = last_reply_at
        if members_only is not UNSET:
            field_dict["members_only"] = members_only
        if on_behalf_of is not UNSET:
            field_dict["on_behalf_of"] = on_behalf_of
        if parts is not UNSET:
            field_dict["parts"] = parts
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
        from ..models.channel_message_response_parts_item import (
            ChannelMessageResponsePartsItem,
        )
        from ..models.interaction_response import InteractionResponse
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        sender = PersonRef.from_dict(d.pop("sender"))

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

        def _parse_interaction(data: object) -> InteractionResponse | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                interaction_type_0 = InteractionResponse.from_dict(data)

                return interaction_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(InteractionResponse | None | Unset, data)

        interaction = _parse_interaction(d.pop("interaction", UNSET))

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
        parts: list[ChannelMessageResponsePartsItem] | Unset = UNSET
        if _parts is not UNSET:
            parts = []
            for parts_item_data in _parts:
                parts_item = ChannelMessageResponsePartsItem.from_dict(parts_item_data)

                parts.append(parts_item)

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
            created_at=created_at,
            id=id,
            sender=sender,
            conversation_id=conversation_id,
            deleted=deleted,
            edited_at=edited_at,
            interaction=interaction,
            last_reply_at=last_reply_at,
            members_only=members_only,
            on_behalf_of=on_behalf_of,
            parts=parts,
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
