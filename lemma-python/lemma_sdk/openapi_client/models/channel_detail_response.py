from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.channel_kind import ChannelKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.channel_participant_response import ChannelParticipantResponse
    from ..models.channel_source_response import ChannelSourceResponse
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="ChannelDetailResponse")


@_attrs_define
class ChannelDetailResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        kind (ChannelKind):
        handler (None | PersonRef | Unset): A member answering by hand; agents do not answer meanwhile.
        last_message_at (datetime.datetime | None | Unset):
        participants (list[ChannelParticipantResponse] | Unset):
        person (None | PersonRef | Unset): Whose channel it is: the member's for a DIRECT one, the contact's for a
            CONTACT one; absent for a GROUP.
        sources (list[ChannelSourceResponse] | Unset):
        title (None | str | Unset):
    """

    created_at: datetime.datetime
    id: UUID
    kind: ChannelKind
    handler: None | PersonRef | Unset = UNSET
    last_message_at: datetime.datetime | None | Unset = UNSET
    participants: list[ChannelParticipantResponse] | Unset = UNSET
    person: None | PersonRef | Unset = UNSET
    sources: list[ChannelSourceResponse] | Unset = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.person_ref import PersonRef

        created_at = self.created_at.isoformat()

        id = str(self.id)

        kind = self.kind.value

        handler: dict[str, Any] | None | Unset
        if isinstance(self.handler, Unset):
            handler = UNSET
        elif isinstance(self.handler, PersonRef):
            handler = self.handler.to_dict()
        else:
            handler = self.handler

        last_message_at: None | str | Unset
        if isinstance(self.last_message_at, Unset):
            last_message_at = UNSET
        elif isinstance(self.last_message_at, datetime.datetime):
            last_message_at = self.last_message_at.isoformat()
        else:
            last_message_at = self.last_message_at

        participants: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.participants, Unset):
            participants = []
            for participants_item_data in self.participants:
                participants_item = participants_item_data.to_dict()
                participants.append(participants_item)

        person: dict[str, Any] | None | Unset
        if isinstance(self.person, Unset):
            person = UNSET
        elif isinstance(self.person, PersonRef):
            person = self.person.to_dict()
        else:
            person = self.person

        sources: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.sources, Unset):
            sources = []
            for sources_item_data in self.sources:
                sources_item = sources_item_data.to_dict()
                sources.append(sources_item)

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "kind": kind,
            }
        )
        if handler is not UNSET:
            field_dict["handler"] = handler
        if last_message_at is not UNSET:
            field_dict["last_message_at"] = last_message_at
        if participants is not UNSET:
            field_dict["participants"] = participants
        if person is not UNSET:
            field_dict["person"] = person
        if sources is not UNSET:
            field_dict["sources"] = sources
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.channel_participant_response import ChannelParticipantResponse
        from ..models.channel_source_response import ChannelSourceResponse
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        kind = ChannelKind(d.pop("kind"))

        def _parse_handler(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                handler_type_0 = PersonRef.from_dict(data)

                return handler_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        handler = _parse_handler(d.pop("handler", UNSET))

        def _parse_last_message_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_message_at_type_0 = isoparse(data)

                return last_message_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_message_at = _parse_last_message_at(d.pop("last_message_at", UNSET))

        _participants = d.pop("participants", UNSET)
        participants: list[ChannelParticipantResponse] | Unset = UNSET
        if _participants is not UNSET:
            participants = []
            for participants_item_data in _participants:
                participants_item = ChannelParticipantResponse.from_dict(
                    participants_item_data
                )

                participants.append(participants_item)

        def _parse_person(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                person_type_0 = PersonRef.from_dict(data)

                return person_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        person = _parse_person(d.pop("person", UNSET))

        _sources = d.pop("sources", UNSET)
        sources: list[ChannelSourceResponse] | Unset = UNSET
        if _sources is not UNSET:
            sources = []
            for sources_item_data in _sources:
                sources_item = ChannelSourceResponse.from_dict(sources_item_data)

                sources.append(sources_item)

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        channel_detail_response = cls(
            created_at=created_at,
            id=id,
            kind=kind,
            handler=handler,
            last_message_at=last_message_at,
            participants=participants,
            person=person,
            sources=sources,
            title=title,
        )

        channel_detail_response.additional_properties = d
        return channel_detail_response

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
