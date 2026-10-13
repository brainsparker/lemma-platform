"""Per-namespace limits: the port, the settings-backed default, and the seam.

Modelled on usage's ``ConfiguredUsageLimitPort``: a deployment states its limits
in settings, and a plan-backed provider (lemma-cloud) replaces the whole port at
startup through ``install_work_limit_port``. Nothing in the runtime knows about
plans.

The runtime reads limits in two places only -- when an enqueue creates a
namespace's queue row, and when a dispatcher refreshes caps for namespaces with
queued work -- never per claimed item. A provider is expected to cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.work.config import default_max_running, work_settings
from app.core.infrastructure.work.types import WorkClass
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Rate:
    limit: int
    per_seconds: float


@dataclass(frozen=True, slots=True)
class WorkLimits:
    max_running: dict[WorkClass, int]
    #: Named token buckets ('llm_filter', 'email_out'). Parsed and carried so a
    #: provider can state them today; enforcement at claim lands with the first
    #: handler that declares a rate key.
    rates: dict[str, Rate]

    def cap(self, cls: WorkClass) -> int:
        return self.max_running.get(cls, default_max_running(cls))


class WorkLimitPort(Protocol):
    async def limits_for(self, ns: str) -> WorkLimits: ...


class NamespaceDirectory(Protocol):
    """Who owns a namespace, for overrides keyed by organization.

    Core cannot read the pods or organizations tables, so a module that can
    installs this; without one, org rules match only ``org:`` namespaces and
    slug rules match nothing.
    """

    async def organization_of(self, ns: str) -> tuple[UUID, str] | None: ...


@dataclass(frozen=True, slots=True)
class OverrideRule:
    specificity: int
    key: str
    value: str
    max_running: tuple[tuple[WorkClass, int], ...]
    rates: tuple[tuple[str, Rate], ...]


_EXACT_KEYS = {"ns": 40, "pod_id": 40, "org_id": 30, "slug": 20, "slug_prefix": 10}


def _class_caps(raw: JsonValue) -> tuple[tuple[WorkClass, int], ...]:
    if not isinstance(raw, dict):
        return ()
    valid = {c.value for c in WorkClass}
    return tuple(
        (WorkClass(name), value)
        for name, value in raw.items()
        if name in valid and isinstance(value, int) and value >= 0
    )


def _rates(raw: JsonValue) -> tuple[tuple[str, Rate], ...]:
    if not isinstance(raw, dict):
        return ()
    rates: list[tuple[str, Rate]] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        limit, per = spec.get("limit"), spec.get("per_seconds")
        if isinstance(limit, int) and isinstance(per, (int, float)) and per > 0:
            rates.append((name, Rate(limit, float(per))))
    return tuple(rates)


def _rule(entry: JsonValue) -> OverrideRule | None:
    if not isinstance(entry, dict):
        return None
    for key, specificity in _EXACT_KEYS.items():
        value = entry.get(key)
        if isinstance(value, str) and value:
            caps = _class_caps(entry.get("max_running"))
            rates = _rates(entry.get("rates"))
            if not caps and not rates:
                return None
            if key == "slug_prefix":
                specificity += min(len(value), 9)
            return OverrideRule(specificity, key, value, caps, rates)
    return None


@lru_cache(maxsize=8)
def parse_overrides(raw: str) -> tuple[OverrideRule, ...]:
    """Override rules from ``WORK_LIMIT_OVERRIDES_JSON``.

    Malformed input yields no rules and warns once per distinct value: a cap
    that silently does not apply is worse than one that refuses, but taking the
    whole runtime down over a typo is worse still.
    """
    if not raw.strip():
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("work.limit_overrides.unparseable")
        return ()
    if not isinstance(parsed, list):
        logger.warning("work.limit_overrides.not_a_list")
        return ()
    rules: list[OverrideRule] = []
    for position, entry in enumerate(parsed):
        rule = _rule(entry)
        if rule is None:
            logger.warning("work.limit_overrides.entry_ignored", position=position)
            continue
        rules.append(rule)
    return tuple(rules)


def _matches(rule: OverrideRule, ns: str, owner: tuple[UUID, str] | None) -> bool:
    key, value = rule.key, rule.value
    if key == "ns":
        return ns == value
    if key == "pod_id":
        return ns == f"pod:{value}"
    if key == "org_id":
        return ns == f"org:{value}" or (owner is not None and str(owner[0]) == value)
    if owner is None:
        return False
    if key == "slug":
        return owner[1] == value
    return owner[1].startswith(value)


def select_override(
    rules: tuple[OverrideRule, ...], ns: str, owner: tuple[UUID, str] | None
) -> OverrideRule | None:
    """The most specific matching rule; a tie goes to the one written last."""
    best: tuple[int, int] | None = None
    chosen: OverrideRule | None = None
    for position, rule in enumerate(rules):
        if not _matches(rule, ns, owner):
            continue
        rank = (rule.specificity, position)
        if best is None or rank > best:
            best, chosen = rank, rule
    return chosen


class ConfiguredWorkLimitPort:
    """Settings defaults plus ``WORK_LIMIT_OVERRIDES_JSON``."""

    def __init__(
        self,
        *,
        raw_overrides: str | None = None,
        directory: NamespaceDirectory | None = None,
    ) -> None:
        self._raw = raw_overrides
        self._directory = directory

    async def limits_for(self, ns: str) -> WorkLimits:
        raw = (
            work_settings.work_limit_overrides_json if self._raw is None else self._raw
        )
        rules = parse_overrides(raw)
        caps = {cls: default_max_running(cls) for cls in WorkClass}
        if not rules:
            return WorkLimits(max_running=caps, rates={})
        owner = None
        if self._directory is not None and ns.startswith("pod:"):
            owner = await self._directory.organization_of(ns)
        rule = select_override(rules, ns, owner)
        if rule is None:
            return WorkLimits(max_running=caps, rates={})
        caps.update(dict(rule.max_running))
        return WorkLimits(max_running=caps, rates=dict(rule.rates))


@dataclass(slots=True)
class _InstalledPort:
    port: WorkLimitPort | None = None


_installed = _InstalledPort()


def install_work_limit_port(port: WorkLimitPort) -> None:
    """Replace the settings-backed port for this process (lemma-cloud's plan provider)."""
    _installed.port = port


def reset_work_limit_port() -> None:
    _installed.port = None


def current_work_limit_port() -> WorkLimitPort:
    return _installed.port or ConfiguredWorkLimitPort()
