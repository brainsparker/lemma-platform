from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_seat import AgentSeat


T = TypeVar("T", bound="MembersAddRequest")


@_attrs_define
class MembersAddRequest:
    """
    Attributes:
        agents (list[AgentSeat] | Unset):
        people (list[UUID] | Unset): Members, by user id.
    """

    agents: list[AgentSeat] | Unset = UNSET
    people: list[UUID] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agents: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = []
            for agents_item_data in self.agents:
                agents_item = agents_item_data.to_dict()
                agents.append(agents_item)

        people: list[str] | Unset = UNSET
        if not isinstance(self.people, Unset):
            people = []
            for people_item_data in self.people:
                people_item = str(people_item_data)
                people.append(people_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agents is not UNSET:
            field_dict["agents"] = agents
        if people is not UNSET:
            field_dict["people"] = people

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_seat import AgentSeat

        d = dict(src_dict)
        _agents = d.pop("agents", UNSET)
        agents: list[AgentSeat] | Unset = UNSET
        if _agents is not UNSET:
            agents = []
            for agents_item_data in _agents:
                agents_item = AgentSeat.from_dict(agents_item_data)

                agents.append(agents_item)

        _people = d.pop("people", UNSET)
        people: list[UUID] | Unset = UNSET
        if _people is not UNSET:
            people = []
            for people_item_data in _people:
                people_item = UUID(people_item_data)

                people.append(people_item)

        members_add_request = cls(
            agents=agents,
            people=people,
        )

        members_add_request.additional_properties = d
        return members_add_request

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
