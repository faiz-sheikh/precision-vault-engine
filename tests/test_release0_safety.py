from __future__ import annotations

import unittest

from pve.safety import (
    LEGACY_PAID_OPT_IN_ENV,
    LEGACY_PAID_OPT_IN_VALUE,
    PaidExecutionBlocked,
    legacy_paid_execution_allowed,
    require_legacy_paid_execution,
)


class LegacySafeModeTests(unittest.TestCase):
    def test_missing_opt_in_is_blocked(self) -> None:
        with self.assertRaises(PaidExecutionBlocked):
            require_legacy_paid_execution({})

    def test_near_match_is_blocked(self) -> None:
        self.assertFalse(legacy_paid_execution_allowed({LEGACY_PAID_OPT_IN_ENV: "true"}))

    def test_exact_rollback_opt_in_is_recognized(self) -> None:
        self.assertTrue(legacy_paid_execution_allowed({LEGACY_PAID_OPT_IN_ENV: LEGACY_PAID_OPT_IN_VALUE}))


if __name__ == "__main__":
    unittest.main()
