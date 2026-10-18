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
        members_only (bool | Unset):  Default: False.
        on_behalf_of (None | PersonRef | Unset): For an agent's line: the member or contact it acted for, whose access
            the answer was made with.
        source_id (None | Unset | UUID): Where it was said; absent for a line written in Lemma.
        text (None | str | Unset): Absent where the line is withheld from the reader.
        withheld (bool | Unset): An answer an agent made with another member's own access: the reader sees that it was
            given, and for whom, not what it said. Default: False.
    """

    created_at: datetime.datetime
    id: UUID
    sender: PersonRef
    members_only: bool | Unset = False
    on_behalf_of: None | PersonRef | Unset = UNSET
    source_id: None | Unset | UUID = UNSET
    text: None | str | Unset = UNSET
    withheld: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.person_ref import PersonRef

        created_at = self.created_at.isoformat()

        id = str(self.id)

        sender = self.sender.to_dict()

        members_only = self.members_only

        on_behalf_of: dict[str, Any] | None | Unset
        if isinstance(self.on_behalf_of, Unset):
            on_behalf_of = UNSET
        elif isinstance(self.on_behalf_of, PersonRef):
            on_behalf_of = self.on_behalf_of.to_dict()
        else:
            on_behalf_of = self.on_behalf_of

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
        if members_only is not UNSET:
            field_dict["members_only"] = members_only
        if on_behalf_of is not UNSET:
            field_dict["on_behalf_of"] = on_behalf_of
        if source_id is not UNSET:
            field_dict["source_id"] = source_id
        if text is not UNSET:
            field_dict["text"] = text
        if withheld is not UNSET:
            field_dict["withheld"] = withheld

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        sender = PersonRef.from_dict(d.pop("sender"))

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

        withheld = d.pop("withheld", UNSET)

        channel_message_response = cls(
            created_at=created_at,
            id=id,
            sender=sender,
            members_only=members_only,
            on_behalf_of=on_behalf_of,
            source_id=source_id,
            text=text,
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
