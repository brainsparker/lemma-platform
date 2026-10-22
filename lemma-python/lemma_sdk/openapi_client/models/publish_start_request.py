from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.publish_mode import PublishMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="PublishStartRequest")


@_attrs_define
class PublishStartRequest:
    """Body for publishing a pod to GitHub.

    Attributes:
        repo_name (str): GitHub repository to publish into, as `name` or `owner/name`. It must already exist and be
            covered by the Lemma app's installation -- publishing does not create repositories, because a GitHub App cannot.
        ai_readme (bool | Unset): Polish the generated README with the system model. Default: False.
        mode (PublishMode | Unset):
        private (bool | Unset): Recorded on the job and reported back, but not acted on: the repository already exists,
            so its visibility is whatever it was created with. Default: False.
    """

    repo_name: str
    ai_readme: bool | Unset = False
    mode: PublishMode | Unset = UNSET
    private: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        repo_name = self.repo_name

        ai_readme = self.ai_readme

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        private = self.private

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "repo_name": repo_name,
            }
        )
        if ai_readme is not UNSET:
            field_dict["ai_readme"] = ai_readme
        if mode is not UNSET:
            field_dict["mode"] = mode
        if private is not UNSET:
            field_dict["private"] = private

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        repo_name = d.pop("repo_name")

        ai_readme = d.pop("ai_readme", UNSET)

        _mode = d.pop("mode", UNSET)
        mode: PublishMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = PublishMode(_mode)

        private = d.pop("private", UNSET)

        publish_start_request = cls(
            repo_name=repo_name,
            ai_readme=ai_readme,
            mode=mode,
            private=private,
        )

        return publish_start_request
