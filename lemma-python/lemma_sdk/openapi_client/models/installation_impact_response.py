from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.impacted_pod_connector import ImpactedPodConnector


T = TypeVar("T", bound="InstallationImpactResponse")


@_attrs_define
class InstallationImpactResponse:
    """What deleting or disabling an installation stops, before it is done.

    Attributes:
        account_count (int):
        auth_config_id (UUID):
        pod_account_count (int):
        pod_connectors (list[ImpactedPodConnector]):
    """

    account_count: int
    auth_config_id: UUID
    pod_account_count: int
    pod_connectors: list[ImpactedPodConnector]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_count = self.account_count

        auth_config_id = str(self.auth_config_id)

        pod_account_count = self.pod_account_count

        pod_connectors = []
        for pod_connectors_item_data in self.pod_connectors:
            pod_connectors_item = pod_connectors_item_data.to_dict()
            pod_connectors.append(pod_connectors_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "account_count": account_count,
                "auth_config_id": auth_config_id,
                "pod_account_count": pod_account_count,
                "pod_connectors": pod_connectors,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.impacted_pod_connector import ImpactedPodConnector

        d = dict(src_dict)
        account_count = d.pop("account_count")

        auth_config_id = UUID(d.pop("auth_config_id"))

        pod_account_count = d.pop("pod_account_count")

        pod_connectors = []
        _pod_connectors = d.pop("pod_connectors")
        for pod_connectors_item_data in _pod_connectors:
            pod_connectors_item = ImpactedPodConnector.from_dict(
                pod_connectors_item_data
            )

            pod_connectors.append(pod_connectors_item)

        installation_impact_response = cls(
            account_count=account_count,
            auth_config_id=auth_config_id,
            pod_account_count=pod_account_count,
            pod_connectors=pod_connectors,
        )

        installation_impact_response.additional_properties = d
        return installation_impact_response

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
