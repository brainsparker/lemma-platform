from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.app_permissions_replace_request import AppPermissionsReplaceRequest


T = TypeVar("T", bound="UpdateAppRequest")


@_attrs_define
class UpdateAppRequest:
    """
    Attributes:
        description (None | str | Unset):
        permissions (AppPermissionsReplaceRequest | None | Unset): Replaces the app's declared grants; absent leaves
            them.
        public_slug (None | str | Unset):
        visibility (None | str | Unset):
    """

    description: None | str | Unset = UNSET
    permissions: AppPermissionsReplaceRequest | None | Unset = UNSET
    public_slug: None | str | Unset = UNSET
    visibility: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.app_permissions_replace_request import (
            AppPermissionsReplaceRequest,
        )

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        permissions: dict[str, Any] | None | Unset
        if isinstance(self.permissions, Unset):
            permissions = UNSET
        elif isinstance(self.permissions, AppPermissionsReplaceRequest):
            permissions = self.permissions.to_dict()
        else:
            permissions = self.permissions

        public_slug: None | str | Unset
        if isinstance(self.public_slug, Unset):
            public_slug = UNSET
        else:
            public_slug = self.public_slug

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if description is not UNSET:
            field_dict["description"] = description
        if permissions is not UNSET:
            field_dict["permissions"] = permissions
        if public_slug is not UNSET:
            field_dict["public_slug"] = public_slug
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.app_permissions_replace_request import (
            AppPermissionsReplaceRequest,
        )

        d = dict(src_dict)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_permissions(
            data: object,
        ) -> AppPermissionsReplaceRequest | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                permissions_type_0 = AppPermissionsReplaceRequest.from_dict(data)

                return permissions_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AppPermissionsReplaceRequest | None | Unset, data)

        permissions = _parse_permissions(d.pop("permissions", UNSET))

        def _parse_public_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        public_slug = _parse_public_slug(d.pop("public_slug", UNSET))

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        update_app_request = cls(
            description=description,
            permissions=permissions,
            public_slug=public_slug,
            visibility=visibility,
        )

        update_app_request.additional_properties = d
        return update_app_request

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
