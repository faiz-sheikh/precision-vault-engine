"""Release 0 safety controls for legacy paid entry points."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

LEGACY_PAID_OPT_IN_ENV = "PVE_ALLOW_LEGACY_PAID_EXECUTION"
LEGACY_PAID_OPT_IN_VALUE = "I_HAVE_FRESH_APPROVAL"


class PaidExecutionBlocked(RuntimeError):
    """Raised before a legacy entry point can create a provider client."""


class Provider(StrEnum):
    GOOGLE = "google"
    NVIDIA = "nvidia"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


_PROVIDER_SWITCH = {
    Provider.GOOGLE: "PVE_ALLOW_GOOGLE_LIVE",
    Provider.NVIDIA: "PVE_ALLOW_NVIDIA_LIVE",
    Provider.OPENAI: "PVE_ALLOW_OPENAI_LIVE",
    Provider.ANTHROPIC: "PVE_ALLOW_ANTHROPIC_LIVE",
}


def live_provider_allowed(
    provider: Provider | str, environ: Mapping[str, str] | None = None
) -> bool:
    """Require both the global and provider-specific exact boolean switches."""
    values = os.environ if environ is None else environ
    try:
        selected = Provider(provider)
    except ValueError:
        return False
    return (
        values.get("PVE_ALLOW_LIVE_PROVIDERS", "0") == "1"
        and values.get(_PROVIDER_SWITCH[selected], "0") == "1"
    )


def require_live_provider(
    provider: Provider | str, environ: Mapping[str, str] | None = None
) -> None:
    """Fail before client construction or any network-capable operation."""
    if live_provider_allowed(provider, environ):
        return
    raise PaidExecutionBlocked(
        f"Live {provider} execution is disabled. Both the global and "
        "provider-specific PVE kill switches must be explicitly enabled."
    )


def legacy_paid_execution_allowed(environ: Mapping[str, str] | None = None) -> bool:
    """Return true only for the documented, exact, case-sensitive opt-in."""
    values = os.environ if environ is None else environ
    return values.get(LEGACY_PAID_OPT_IN_ENV) == LEGACY_PAID_OPT_IN_VALUE


def require_legacy_paid_execution(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed unless an operator supplies the explicit rollback opt-in."""
    if legacy_paid_execution_allowed(environ):
        return
    raise PaidExecutionBlocked(
        "Legacy paid execution is blocked by Release 0 safe mode. "
        "Use the controller after its release gates pass. A fresh approval is "
        "still required for every live scope; environment configuration alone "
        "does not constitute approval."
    )
