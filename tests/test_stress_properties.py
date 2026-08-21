from __future__ import annotations

import random
import string
import unittest

from pve.identity import semantic_title, sha256_identity
from pve.pipeline import PHASES, plan


class StressPropertyTests(unittest.TestCase):
    def test_thousand_module_plan_is_complete_and_deterministic(self):
        modules = [f"mod_{i:04d}" for i in range(1000)]
        one = plan(modules)
        two = plan(modules)
        self.assertEqual(one, two)
        self.assertEqual(len(one), 1000 * len(PHASES))
        self.assertEqual(len({(job.module_id, job.phase) for job in one}), len(one))

    def test_ten_thousand_random_identities_do_not_collide(self):
        rng = random.Random(4132)
        values = []
        alphabet = string.ascii_letters + string.digits + " &-_/"
        for index in range(10_000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 180))) + str(index)
            values.append(sha256_identity("fuzz", text))
        self.assertEqual(len(values), len(set(values)))

    def test_semantic_normalization_is_idempotent(self):
        rng = random.Random(54)
        alphabet = string.ascii_letters + string.digits + " &-_/"
        for _ in range(2000):
            value = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 100)))
            self.assertEqual(semantic_title(value), semantic_title(semantic_title(value)))
