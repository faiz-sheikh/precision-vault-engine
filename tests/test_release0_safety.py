from __future__ import annotations

import unittest

from pve.safety import (
    LEGACY_PAID_OPT_IN_ENV,
    LEGACY_PAID_OPT_IN_VALUE,
    PaidExecutionBlocked,
    legacy_paid_execution_allowed,
    require_legacy_paid_execution,
    live_provider_allowed,
    require_live_provider,
    Provider,
)


class LegacySafeModeTests(unittest.TestCase):
    def test_missing_opt_in_is_blocked(self) -> None:
        with self.assertRaises(PaidExecutionBlocked):
            require_legacy_paid_execution({})

    def test_near_match_is_blocked(self) -> None:
        self.assertFalse(legacy_paid_execution_allowed({LEGACY_PAID_OPT_IN_ENV: "true"}))

    def test_exact_rollback_opt_in_is_recognized(self) -> None:
        self.assertTrue(legacy_paid_execution_allowed({LEGACY_PAID_OPT_IN_ENV: LEGACY_PAID_OPT_IN_VALUE}))

    def test_live_provider_requires_both_switches(self) -> None:
        for provider in Provider:
            self.assertFalse(live_provider_allowed(provider, {}))
            self.assertFalse(live_provider_allowed(provider, {"PVE_ALLOW_LIVE_PROVIDERS": "1"}))

    def test_provider_specific_switch_does_not_bypass_global(self) -> None:
        self.assertFalse(live_provider_allowed(Provider.GOOGLE, {"PVE_ALLOW_GOOGLE_LIVE": "1"}))

    def test_both_exact_switches_allow_provider(self) -> None:
        env = {"PVE_ALLOW_LIVE_PROVIDERS": "1", "PVE_ALLOW_NVIDIA_LIVE": "1"}
        self.assertTrue(live_provider_allowed(Provider.NVIDIA, env))
        with self.assertRaises(PaidExecutionBlocked):
            require_live_provider(Provider.OPENAI, env)


if __name__ == "__main__":
    unittest.main()
