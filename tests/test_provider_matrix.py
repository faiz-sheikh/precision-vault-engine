from __future__ import annotations

import unittest

from pve.providers import FailureClass, classify_status


class ProviderFailureMatrixTests(unittest.TestCase):
    def test_status_matrix(self):
        cases = {400:FailureClass.PERMANENT,401:FailureClass.PERMANENT,403:FailureClass.PERMANENT,
                 404:FailureClass.PERMANENT,408:FailureClass.TRANSIENT,409:FailureClass.TRANSIENT,
                 425:FailureClass.TRANSIENT,429:FailureClass.TRANSIENT,500:FailureClass.TRANSIENT,
                 502:FailureClass.TRANSIENT,503:FailureClass.TRANSIENT,504:FailureClass.TRANSIENT}
        for status, expected in cases.items():
            with self.subTest(status=status): self.assertEqual(classify_status(status), expected)

    def test_unknown_acceptance_is_always_ambiguous(self):
        for status in (None, 200, 400, 429, 500):
            self.assertEqual(classify_status(status, accepted_unknown=True), FailureClass.AMBIGUOUS)
