"""The work runtime: durable work items in Postgres, written in the caller's transaction.

Public surface -- import from here, not from the submodules:

* ``work_handler`` / ``WorkMode`` / ``WorkClass`` / ``RetryPolicy`` -- register a kind.
* ``WorkWriter`` -- enqueue on an open unit of work.
* ``facts`` / ``Fact`` -- typed cross-module reactions, fanned out at write time.
* ``periodic`` -- recurring work, armed at worker boot.
* ``Rate`` / ``RateUse`` -- named token buckets a kind's items draw from at claim.
* ``Unavailable`` / ``ItemFailure`` / ``PermanentFailure`` / ``Defer`` /
  ``Released`` / ``LeaseLost`` -- how a handler reports what happened.
* ``FastContext`` / ``JobContext`` / ``DeadContext`` -- what handlers receive.
"""

from app.core.infrastructure.work import maintenance as _maintenance  # noqa: F401  registers work.retention
from app.core.infrastructure.work.context import DeadContext, FastContext
from app.core.infrastructure.work.facts import Fact, FactBus, facts
from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.limits import (
    ConfiguredWorkLimitPort,
    NamespaceDirectory,
    WorkLimitPort,
    WorkLimits,
    install_work_limit_port,
    reset_work_limit_port,
)
from app.core.infrastructure.work.rates import Rate, RateUse
from app.core.infrastructure.work.registry import (
    HandlerSpec,
    UnknownWorkKind,
    WorkRegistry,
    periodic,
    registry,
    work_handler,
)
from app.core.infrastructure.work.retry import DEFAULT_RETRY, Exponential, RetryPolicy
from app.core.infrastructure.work.types import (
    SYSTEM_NAMESPACE,
    ClaimedItem,
    Defer,
    ItemFailure,
    LeaseLost,
    PermanentFailure,
    Released,
    Unavailable,
    UnavailableEvidence,
    WorkClass,
    WorkMode,
    org_namespace,
    pod_namespace,
)
from app.core.infrastructure.work.writer import WorkWriter

__all__ = [
    "DEFAULT_RETRY",
    "SYSTEM_NAMESPACE",
    "ClaimedItem",
    "ConfiguredWorkLimitPort",
    "DeadContext",
    "Defer",
    "Exponential",
    "Fact",
    "FactBus",
    "FastContext",
    "HandlerSpec",
    "ItemFailure",
    "JobContext",
    "LeaseLost",
    "NamespaceDirectory",
    "PermanentFailure",
    "Rate",
    "RateUse",
    "Released",
    "RetryPolicy",
    "Unavailable",
    "UnavailableEvidence",
    "UnknownWorkKind",
    "WorkClass",
    "WorkLimitPort",
    "WorkLimits",
    "WorkMode",
    "WorkRegistry",
    "WorkWriter",
    "facts",
    "install_work_limit_port",
    "org_namespace",
    "periodic",
    "pod_namespace",
    "registry",
    "reset_work_limit_port",
    "work_handler",
]
