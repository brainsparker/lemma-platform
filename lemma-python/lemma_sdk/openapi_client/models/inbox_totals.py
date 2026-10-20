from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="InboxTotals")


@_attrs_define
class InboxTotals:
    """
    Attributes:
        channels (int): Channels with something new.
        mentions (int): Unread lines naming the reader.
        waiting (int): Questions waiting on the reader's answer.
    """

    channels: int
    mentions: int
    waiting: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channels = self.channels

        mentions = self.mentions

        waiting = self.waiting

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channels": channels,
                "mentions": mentions,
                "waiting": waiting,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        channels = d.pop("channels")

        mentions = d.pop("mentions")

        waiting = d.pop("waiting")

        inbox_totals = cls(
            channels=channels,
            mentions=mentions,
            waiting=waiting,
        )

        inbox_totals.additional_properties = d
        return inbox_totals

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
