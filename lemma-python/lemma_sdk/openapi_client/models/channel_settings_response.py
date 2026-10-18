from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ChannelSettingsResponse")


@_attrs_define
class ChannelSettingsResponse:
    """
    Attributes:
        agents_answer (bool | Unset): A contact's channel: whether its agents answer the contact. Off, only members do.
            Default: True.
        hand_back_after_hours (int | Unset): How long a member who stepped in holds the contact after their last line,
            before the agent answers again. Default: 12.
    """

    agents_answer: bool | Unset = True
    hand_back_after_hours: int | Unset = 12
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agents_answer = self.agents_answer

        hand_back_after_hours = self.hand_back_after_hours

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agents_answer is not UNSET:
            field_dict["agents_answer"] = agents_answer
        if hand_back_after_hours is not UNSET:
            field_dict["hand_back_after_hours"] = hand_back_after_hours

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agents_answer = d.pop("agents_answer", UNSET)

        hand_back_after_hours = d.pop("hand_back_after_hours", UNSET)

        channel_settings_response = cls(
            agents_answer=agents_answer,
            hand_back_after_hours=hand_back_after_hours,
        )

        channel_settings_response.additional_properties = d
        return channel_settings_response

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
