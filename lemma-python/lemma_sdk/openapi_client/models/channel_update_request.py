from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.channel_settings_change import ChannelSettingsChange


T = TypeVar("T", bound="ChannelUpdateRequest")


@_attrs_define
class ChannelUpdateRequest:
    """
    Attributes:
        archived (bool | None | Unset):
        settings (ChannelSettingsChange | None | Unset): A contact's channel only; takes `contact.message`.
        title (None | str | Unset):
        topic (None | str | Unset):
    """

    archived: bool | None | Unset = UNSET
    settings: ChannelSettingsChange | None | Unset = UNSET
    title: None | str | Unset = UNSET
    topic: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.channel_settings_change import ChannelSettingsChange

        archived: bool | None | Unset
        if isinstance(self.archived, Unset):
            archived = UNSET
        else:
            archived = self.archived

        settings: dict[str, Any] | None | Unset
        if isinstance(self.settings, Unset):
            settings = UNSET
        elif isinstance(self.settings, ChannelSettingsChange):
            settings = self.settings.to_dict()
        else:
            settings = self.settings

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        topic: None | str | Unset
        if isinstance(self.topic, Unset):
            topic = UNSET
        else:
            topic = self.topic

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if archived is not UNSET:
            field_dict["archived"] = archived
        if settings is not UNSET:
            field_dict["settings"] = settings
        if title is not UNSET:
            field_dict["title"] = title
        if topic is not UNSET:
            field_dict["topic"] = topic

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.channel_settings_change import ChannelSettingsChange

        d = dict(src_dict)

        def _parse_archived(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        archived = _parse_archived(d.pop("archived", UNSET))

        def _parse_settings(data: object) -> ChannelSettingsChange | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                settings_type_0 = ChannelSettingsChange.from_dict(data)

                return settings_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ChannelSettingsChange | None | Unset, data)

        settings = _parse_settings(d.pop("settings", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        def _parse_topic(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        topic = _parse_topic(d.pop("topic", UNSET))

        channel_update_request = cls(
            archived=archived,
            settings=settings,
            title=title,
            topic=topic,
        )

        channel_update_request.additional_properties = d
        return channel_update_request

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
