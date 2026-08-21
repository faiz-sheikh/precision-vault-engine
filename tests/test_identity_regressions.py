from __future__ import annotations

import unittest

from pve.identity import provider_fingerprint, semantic_title, sha256_identity


class IdentityRegressionTests(unittest.TestCase):
    def test_long_prefixes_do_not_collide(self) -> None:
        prefix = "x" * 60
        self.assertNotEqual(
            sha256_identity("module", prefix + "a"), sha256_identity("module", prefix + "b")
        )

    def test_ampersand_and_word_are_semantic_duplicates(self) -> None:
        self.assertEqual(
            semantic_title("Sensors & Actuators"), semantic_title("Sensors and Actuators")
        )

    def test_fingerprint_is_order_stable(self) -> None:
        one = sha256_identity("test", {"b": 2, "a": 1})
        two = sha256_identity("test", {"a": 1, "b": 2})
        self.assertEqual(one, two)

    def test_fingerprint_changes_for_semantic_inputs(self) -> None:
        base = dict(
            provider="google",
            model="m",
            request={"x": 1},
            prompt_hash="p",
            parameters={},
            schema={},
            run_config_hash="r",
            upstream_hashes=["a"],
        )
        first = provider_fingerprint(**base)
        for field, value in (
            ("model", "m2"),
            ("prompt_hash", "p2"),
            ("run_config_hash", "r2"),
            ("upstream_hashes", ["b"]),
        ):
            changed = dict(base)
            changed[field] = value
            self.assertNotEqual(first, provider_fingerprint(**changed))
