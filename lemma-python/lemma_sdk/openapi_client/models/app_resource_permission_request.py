from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.resource_type import ResourceType
from ..types import UNSET, Unset

T = TypeVar("T", bound="AppResourcePermissionRequest")


@_attrs_define
class AppResourcePermissionRequest:
    """One thing the app may do for its viewer, e.g. ``datastore_table``
    ``metrics`` ``[datastore.record.read]``.

        Attributes:
            resource_name (str):
            resource_type (ResourceType):
            permission_ids (list[str] | Unset):
    """

    resource_name: str
    resource_type: ResourceType
    permission_ids: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        resource_name = self.resource_name

        resource_type = self.resource_type.value

        permission_ids: list[str] | Unset = UNSET
        if not isinstance(self.permission_ids, Unset):
            permission_ids = self.permission_ids

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "resource_name": resource_name,
                "resource_type": resource_type,
            }
        )
        if permission_ids is not UNSET:
            field_dict["permission_ids"] = permission_ids

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        resource_name = d.pop("resource_name")

        resource_type = ResourceType(d.pop("resource_type"))

        permission_ids = cast(list[str], d.pop("permission_ids", UNSET))

        app_resource_permission_request = cls(
            resource_name=resource_name,
            resource_type=resource_type,
            permission_ids=permission_ids,
        )

        app_resource_permission_request.additional_properties = d
        return app_resource_permission_request

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
