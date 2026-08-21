"""Release 0 safety controls for legacy paid entry points."""

from __future__ import annotations

import os
from collections.abc import Mapping

LEGACY_PAID_OPT_IN_ENV = "PVE_ALLOW_LEGACY_PAID_EXECUTION"
LEGACY_PAID_OPT_IN_VALUE = "I_HAVE_FRESH_APPROVAL"


class PaidExecutionBlocked(RuntimeError):
    """Raised before a legacy entry point can create a provider client."""


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
