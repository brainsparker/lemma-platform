from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.app_resource_permission_request import AppResourcePermissionRequest


T = TypeVar("T", bound="AppPermissionsReplaceRequest")


@_attrs_define
class AppPermissionsReplaceRequest:
    """The app's declared grants: what its page may do, at most, for whoever
    views it. Replaces every grant it held; an empty list revokes them all.

        Attributes:
            grants (list[AppResourcePermissionRequest] | Unset):
    """

    grants: list[AppResourcePermissionRequest] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        grants: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.grants, Unset):
            grants = []
            for grants_item_data in self.grants:
                grants_item = grants_item_data.to_dict()
                grants.append(grants_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if grants is not UNSET:
            field_dict["grants"] = grants

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.app_resource_permission_request import (
            AppResourcePermissionRequest,
        )

        d = dict(src_dict)
        _grants = d.pop("grants", UNSET)
        grants: list[AppResourcePermissionRequest] | Unset = UNSET
        if _grants is not UNSET:
            grants = []
            for grants_item_data in _grants:
                grants_item = AppResourcePermissionRequest.from_dict(grants_item_data)

                grants.append(grants_item)

        app_permissions_replace_request = cls(
            grants=grants,
        )

        app_permissions_replace_request.additional_properties = d
        return app_permissions_replace_request

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
