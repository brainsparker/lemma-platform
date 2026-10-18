from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="NoticeResponse")


@_attrs_define
class NoticeResponse:
    """
    Attributes:
        channel_id (UUID):
        message_id (UUID):
        delivered_via (None | str | Unset):
        reason (None | str | Unset): Why it went no further than their channel, if it didn't.
    """

    channel_id: UUID
    message_id: UUID
    delivered_via: None | str | Unset = UNSET
    reason: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel_id = str(self.channel_id)

        message_id = str(self.message_id)

        delivered_via: None | str | Unset
        if isinstance(self.delivered_via, Unset):
            delivered_via = UNSET
        else:
            delivered_via = self.delivered_via

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel_id": channel_id,
                "message_id": message_id,
            }
        )
        if delivered_via is not UNSET:
            field_dict["delivered_via"] = delivered_via
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        channel_id = UUID(d.pop("channel_id"))

        message_id = UUID(d.pop("message_id"))

        def _parse_delivered_via(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        delivered_via = _parse_delivered_via(d.pop("delivered_via", UNSET))

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        notice_response = cls(
            channel_id=channel_id,
            message_id=message_id,
            delivered_via=delivered_via,
            reason=reason,
        )

        notice_response.additional_properties = d
        return notice_response

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
