"""The refusal a GitHub installation gives an organization it does not belong to."""

from __future__ import annotations

from app.modules.connectors.domain.errors import (
    ConnectorConflictError,
    ConnectorDomainError,
)


class GithubInstallationBoundElsewhereError(ConnectorConflictError):
    """Another Lemma organization already holds this installation (audit H4).

    Deliberately says nothing about which one: the installation id is a bare
    number, and confirming whose it is would answer a question the caller has
    no business asking.
    """

    def __init__(self) -> None:
        super().__init__(
            "This GitHub App installation is connected to a different Lemma "
            "organization. Install the Lemma app on a GitHub account or "
            "organization for this one, then connect again."
        )
        self.code = "GITHUB_INSTALLATION_BOUND_ELSEWHERE"


class GithubInstallationNotAdministeredError(ConnectorDomainError):
    """Nobody has bound this installation yet, and the claimant cannot.

    Binding hands the claimant's organization the App's token on every
    repository in the installation, so only its owner on GitHub's side may do
    it: the GitHub user it is installed on, or an admin of the GitHub
    organization it is installed on (audit H4). Seeing a repository in it is
    not enough.
    """

    def __init__(self) -> None:
        super().__init__(
            message=(
                "Only the GitHub account this app is installed on, or an admin "
                "of that GitHub organization, can connect this installation to "
                "Lemma. Ask them to connect it first; after that, anyone in "
                "this organization can use it."
            ),
            code="GITHUB_INSTALLATION_NOT_ADMINISTERED",
            status_code=403,
        )
