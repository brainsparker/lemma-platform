from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.channel_post_request_parts_item import ChannelPostRequestPartsItem
    from ..models.mention import Mention


T = TypeVar("T", bound="ChannelPostRequest")


@_attrs_define
class ChannelPostRequest:
    """
    Attributes:
        members_only (bool | Unset): A note to the pod's members: never sent outside. Default: False.
        mentions (list[Mention] | Unset):
        parts (list[ChannelPostRequestPartsItem] | Unset):
        reply_to_id (None | Unset | UUID):
        source_id (None | Unset | UUID): Which outside place it goes to; by default the one the channel last heard from.
        text (str | Unset):  Default: ''.
        thread_root_id (None | Unset | UUID): Reply in the thread of this line.
    """

    members_only: bool | Unset = False
    mentions: list[Mention] | Unset = UNSET
    parts: list[ChannelPostRequestPartsItem] | Unset = UNSET
    reply_to_id: None | Unset | UUID = UNSET
    source_id: None | Unset | UUID = UNSET
    text: str | Unset = ""
    thread_root_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        members_only = self.members_only

        mentions: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.mentions, Unset):
            mentions = []
            for mentions_item_data in self.mentions:
                mentions_item = mentions_item_data.to_dict()
                mentions.append(mentions_item)

        parts: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.parts, Unset):
            parts = []
            for parts_item_data in self.parts:
                parts_item = parts_item_data.to_dict()
                parts.append(parts_item)

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

        text = self.text

        thread_root_id: None | str | Unset
        if isinstance(self.thread_root_id, Unset):
            thread_root_id = UNSET
        elif isinstance(self.thread_root_id, UUID):
            thread_root_id = str(self.thread_root_id)
        else:
            thread_root_id = self.thread_root_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if members_only is not UNSET:
            field_dict["members_only"] = members_only
        if mentions is not UNSET:
            field_dict["mentions"] = mentions
        if parts is not UNSET:
            field_dict["parts"] = parts
        if reply_to_id is not UNSET:
            field_dict["reply_to_id"] = reply_to_id
        if source_id is not UNSET:
            field_dict["source_id"] = source_id
        if text is not UNSET:
            field_dict["text"] = text
        if thread_root_id is not UNSET:
            field_dict["thread_root_id"] = thread_root_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.channel_post_request_parts_item import ChannelPostRequestPartsItem
        from ..models.mention import Mention

        d = dict(src_dict)
        members_only = d.pop("members_only", UNSET)

        _mentions = d.pop("mentions", UNSET)
        mentions: list[Mention] | Unset = UNSET
        if _mentions is not UNSET:
            mentions = []
            for mentions_item_data in _mentions:
                mentions_item = Mention.from_dict(mentions_item_data)

                mentions.append(mentions_item)

        _parts = d.pop("parts", UNSET)
        parts: list[ChannelPostRequestPartsItem] | Unset = UNSET
        if _parts is not UNSET:
            parts = []
            for parts_item_data in _parts:
                parts_item = ChannelPostRequestPartsItem.from_dict(parts_item_data)

                parts.append(parts_item)

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

        text = d.pop("text", UNSET)

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

        channel_post_request = cls(
            members_only=members_only,
            mentions=mentions,
            parts=parts,
            reply_to_id=reply_to_id,
            source_id=source_id,
            text=text,
            thread_root_id=thread_root_id,
        )

        channel_post_request.additional_properties = d
        return channel_post_request

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
