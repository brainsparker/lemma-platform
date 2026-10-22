"""Abstract interfaces for workspace sandbox operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Any
from uuid import UUID

from sandbox_runtime.protocol import FileStat

from app.modules.workspace.contracts import (
    SandboxInfo,
    PythonExecutionResult,
    ShellCommandResult,
)
from app.modules.workspace.domain.workspace_identity import workspace_sandbox_id


class ISandbox(ABC):
    """Interface for managing a person's workspace sandboxes.

    Every method but ``resolve_workspace`` takes the workspace's id: one per
    person, per pod, per actor, see ``domain/workspace_identity``. A person's
    workspace with no pod keeps their user id.
    """

    async def resolve_workspace(
        self, user_id: UUID, pod_id: UUID | None, *, actor_id: UUID | None = None
    ) -> UUID:
        """The id of ``user_id``'s workspace in ``pod_id`` as ``actor_id``.

        An implementation that keeps rows makes the row here, because the id
        alone does not say whose workspace it is. No actor is the assistant.
        """
        return workspace_sandbox_id(user_id, pod_id, actor_id)

    @abstractmethod
    async def ensure_sandbox(self, workspace_id: UUID) -> SandboxInfo:
        """Create or resume a workspace sandbox."""

    @abstractmethod
    async def get_sandbox(self, workspace_id: UUID) -> Optional[SandboxInfo]:
        """Get a workspace sandbox's information without starting it."""

    @abstractmethod
    async def delete_sandbox(self, workspace_id: UUID) -> None:
        """Delete a workspace sandbox."""

    async def suspend_sandbox(self, workspace_id: UUID) -> None:
        """Release compute while retaining the logical sandbox identity.

        Providers without a suspend primitive retain historical behavior by
        deleting compute; the next ensure still uses the same logical id.
        """

        await self.delete_sandbox(workspace_id)

    @abstractmethod
    async def is_sandbox_running(self, workspace_id: UUID) -> bool:
        """Check if a workspace sandbox is running."""


class IWorkspaceSession(ABC):
    """
    Pure workspace session interface for operations.

    This interface is independent of the sandbox provider. Implementations may
    talk to the sandbox runtime directly, to a local Docker runtime, or to another adapter.
    """

    @abstractmethod
    async def execute_code(
        self,
        code: str,
        timeout: int = 30,
    ) -> PythonExecutionResult:
        """
        Execute Python code in the session.
        """

    @abstractmethod
    async def execute_terminal_command(
        self,
        command: str,
        timeout: int = 30,
    ) -> ShellCommandResult:
        """Execute a terminal command in the session."""

    @abstractmethod
    async def exec_command(
        self,
        *,
        cmd: str,
        max_output_tokens: Optional[int] = None,
        tty: bool = False,
        workdir: Optional[str] = None,
        yield_time_ms: Optional[int] = None,
        timeout: Optional[int] = 300,
    ) -> dict[str, Any]:
        """Execute a shell command, optionally as a long-running process."""

    @abstractmethod
    async def write_stdin(
        self,
        *,
        process_id: str,
        chars: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        yield_time_ms: Optional[int] = None,
    ) -> dict[str, Any]:
        """Write to, or poll, a long-running process."""

    @abstractmethod
    async def terminate_process(self, process_id: str) -> dict[str, Any]:
        """Terminate a long-running process."""

    @abstractmethod
    async def list_processes(self) -> list[dict[str, Any]]:
        """List tracked shell processes in the session."""

    @abstractmethod
    async def stat_file(self, path: str, *, timeout: int = 30) -> FileStat:
        """Metadata for one path, raising when it is not there."""

    @abstractmethod
    async def list_files(self, path: str, *, timeout: int = 30) -> tuple[FileStat, ...]:
        """One directory's entries."""

    @abstractmethod
    async def read_file(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
        timeout: int = 60,
    ) -> bytes:
        """Raw bytes of one file, from `offset`, at most `length` of them."""

    @abstractmethod
    async def close(self) -> None:
        """Close the session and cleanup resources."""

    @abstractmethod
    async def __aenter__(self) -> "IWorkspaceSession":
        """Enter the async context manager."""

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> Optional[bool]:
        """Exit the async context manager."""
