from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.notify_level import NotifyLevel
from ..types import UNSET, Unset

T = TypeVar("T", bound="NotifyRequest")


@_attrs_define
class NotifyRequest:
    """
    Attributes:
        notify (NotifyLevel): Which lines of a channel reach a participant outside the app.
        notify_via (list[str] | None | Unset):
    """

    notify: NotifyLevel
    notify_via: list[str] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        notify = self.notify.value

        notify_via: list[str] | None | Unset
        if isinstance(self.notify_via, Unset):
            notify_via = UNSET
        elif isinstance(self.notify_via, list):
            notify_via = self.notify_via

        else:
            notify_via = self.notify_via

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "notify": notify,
            }
        )
        if notify_via is not UNSET:
            field_dict["notify_via"] = notify_via

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        notify = NotifyLevel(d.pop("notify"))

        def _parse_notify_via(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                notify_via_type_0 = cast(list[str], data)

                return notify_via_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        notify_via = _parse_notify_via(d.pop("notify_via", UNSET))

        notify_request = cls(
            notify=notify,
            notify_via=notify_via,
        )

        notify_request.additional_properties = d
        return notify_request

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
