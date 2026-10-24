from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.impacted_pod import ImpactedPod
    from ..models.impacted_pod_connector import ImpactedPodConnector


T = TypeVar("T", bound="InstallationImpactResponse")


@_attrs_define
class InstallationImpactResponse:
    """What deleting or disabling an installation stops, before it is done.

    The counts are the whole organization's; `pods` names only the pods the
    caller can open.

        Attributes:
            account_count (int):
            auth_config_id (UUID):
            pod_account_count (int):
            pod_connectors (list[ImpactedPodConnector]):
            complete (bool | Unset): False when the surfaces, schedules and subscriptions could not be counted just now: the
                counts above them are zero for that reason, not because nothing stops. Removing is not blocked by it. Default:
                True.
            event_subscription_count (int | Unset): MCP event subscriptions held on its accounts. Default: 0.
            pods (list[ImpactedPod] | Unset): Per pod the caller can open: what stops there.
            schedule_count (int | Unset): Schedules that listen through one of its pod connectors. Default: 0.
            surface_count (int | Unset): Surfaces that answer through one of its pod connectors. Default: 0.
    """

    account_count: int
    auth_config_id: UUID
    pod_account_count: int
    pod_connectors: list[ImpactedPodConnector]
    complete: bool | Unset = True
    event_subscription_count: int | Unset = 0
    pods: list[ImpactedPod] | Unset = UNSET
    schedule_count: int | Unset = 0
    surface_count: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_count = self.account_count

        auth_config_id = str(self.auth_config_id)

        pod_account_count = self.pod_account_count

        pod_connectors = []
        for pod_connectors_item_data in self.pod_connectors:
            pod_connectors_item = pod_connectors_item_data.to_dict()
            pod_connectors.append(pod_connectors_item)

        complete = self.complete

        event_subscription_count = self.event_subscription_count

        pods: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.pods, Unset):
            pods = []
            for pods_item_data in self.pods:
                pods_item = pods_item_data.to_dict()
                pods.append(pods_item)

        schedule_count = self.schedule_count

        surface_count = self.surface_count

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
        if complete is not UNSET:
            field_dict["complete"] = complete
        if event_subscription_count is not UNSET:
            field_dict["event_subscription_count"] = event_subscription_count
        if pods is not UNSET:
            field_dict["pods"] = pods
        if schedule_count is not UNSET:
            field_dict["schedule_count"] = schedule_count
        if surface_count is not UNSET:
            field_dict["surface_count"] = surface_count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.impacted_pod import ImpactedPod
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

        complete = d.pop("complete", UNSET)

        event_subscription_count = d.pop("event_subscription_count", UNSET)

        _pods = d.pop("pods", UNSET)
        pods: list[ImpactedPod] | Unset = UNSET
        if _pods is not UNSET:
            pods = []
            for pods_item_data in _pods:
                pods_item = ImpactedPod.from_dict(pods_item_data)

                pods.append(pods_item)

        schedule_count = d.pop("schedule_count", UNSET)

        surface_count = d.pop("surface_count", UNSET)

        installation_impact_response = cls(
            account_count=account_count,
            auth_config_id=auth_config_id,
            pod_account_count=pod_account_count,
            pod_connectors=pod_connectors,
            complete=complete,
            event_subscription_count=event_subscription_count,
            pods=pods,
            schedule_count=schedule_count,
            surface_count=surface_count,
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
