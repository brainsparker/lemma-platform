from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.bundle_source_kind import BundleSourceKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="ImportStartRequest")


@_attrs_define
class ImportStartRequest:
    """Body for starting a URL-based import.

    Attributes:
        kind (BundleSourceKind): Where an imported bundle comes from — a CAPS wire enum.

            ``URL`` covers any lemma-origin signed download URL (an export or an
            uploaded ``.zip`` staged into our object storage); ``GITHUB`` is a public
            repo fetched via the connector path.
        owner (None | str | Unset): GITHUB repo owner.
        ref (None | str | Unset): GITHUB branch/tag/sha (optional).
        repo (None | str | Unset): GITHUB repo name.
        url (None | str | Unset): For URL: a lemma bundle download URL (from an export or an upload). For GITHUB: the
            repo URL (alternative to owner+repo).
    """

    kind: BundleSourceKind
    owner: None | str | Unset = UNSET
    ref: None | str | Unset = UNSET
    repo: None | str | Unset = UNSET
    url: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        owner: None | str | Unset
        if isinstance(self.owner, Unset):
            owner = UNSET
        else:
            owner = self.owner

        ref: None | str | Unset
        if isinstance(self.ref, Unset):
            ref = UNSET
        else:
            ref = self.ref

        repo: None | str | Unset
        if isinstance(self.repo, Unset):
            repo = UNSET
        else:
            repo = self.repo

        url: None | str | Unset
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "kind": kind,
            }
        )
        if owner is not UNSET:
            field_dict["owner"] = owner
        if ref is not UNSET:
            field_dict["ref"] = ref
        if repo is not UNSET:
            field_dict["repo"] = repo
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = BundleSourceKind(d.pop("kind"))

        def _parse_owner(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        owner = _parse_owner(d.pop("owner", UNSET))

        def _parse_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ref = _parse_ref(d.pop("ref", UNSET))

        def _parse_repo(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        repo = _parse_repo(d.pop("repo", UNSET))

        def _parse_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        url = _parse_url(d.pop("url", UNSET))

        import_start_request = cls(
            kind=kind,
            owner=owner,
            ref=ref,
            repo=repo,
            url=url,
        )

        return import_start_request
