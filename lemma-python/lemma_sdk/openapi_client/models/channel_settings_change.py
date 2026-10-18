from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ChannelSettingsChange")


@_attrs_define
class ChannelSettingsChange:
    """Only what is named changes.

    Attributes:
        agents_answer (bool | None | Unset): Switch the agent on or off; on also hands the contact back now.
        hand_back_after_hours (int | None | Unset):
    """

    agents_answer: bool | None | Unset = UNSET
    hand_back_after_hours: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agents_answer: bool | None | Unset
        if isinstance(self.agents_answer, Unset):
            agents_answer = UNSET
        else:
            agents_answer = self.agents_answer

        hand_back_after_hours: int | None | Unset
        if isinstance(self.hand_back_after_hours, Unset):
            hand_back_after_hours = UNSET
        else:
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

        def _parse_agents_answer(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        agents_answer = _parse_agents_answer(d.pop("agents_answer", UNSET))

        def _parse_hand_back_after_hours(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        hand_back_after_hours = _parse_hand_back_after_hours(
            d.pop("hand_back_after_hours", UNSET)
        )

        channel_settings_change = cls(
            agents_answer=agents_answer,
            hand_back_after_hours=hand_back_after_hours,
        )

        channel_settings_change.additional_properties = d
        return channel_settings_change

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
