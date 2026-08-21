from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import config
import curriculum_mapper as mapper
from pve.state import StateCorrupt, load_json_state


class LegacyRegressionTests(unittest.TestCase):
    def test_biology_is_natural_science_not_ai(self) -> None:
        self.assertEqual(
            mapper._correct_domain("Cell Biology", "Biology", "06_AI_ML"), "12_NaturalSciences"
        )

    def test_cs_minor_is_programming_not_defence(self) -> None:
        self.assertEqual(
            mapper._correct_domain("CS Minor", "Computer Science", "09_Defence"), "03_Programming"
        )

    def test_natural_and_mechanical_domains_exist(self) -> None:
        self.assertIn("12_NaturalSciences", config.DOMAIN_KEYWORDS)
        self.assertIn("13_MechanicalEngineering", config.DOMAIN_KEYWORDS)

    def test_queries_are_anchored_not_boilerplate(self) -> None:
        queries = mapper._build_web_queries(
            "Kalman Filtering", ["State estimation", "Covariance update"], "Control"
        )
        self.assertTrue(all("Kalman Filtering" in query for query in queries))

    def test_chapters_are_derived_not_provider_controlled(self) -> None:
        self.assertEqual(mapper._suggested_chapters([]), 1)
        self.assertEqual(mapper._suggested_chapters([str(i) for i in range(7)]), 4)

    def test_visual_route_is_deterministic(self) -> None:
        self.assertEqual(
            mapper._resolve_engine("12_NaturalSciences", "Biology", ["cell structure"]), "remotion"
        )

    def test_module_ids_with_same_long_prefix_do_not_collide(self) -> None:
        a = mapper._make_id("03_Programming", "Sem 1", "x" * 100 + "alpha")
        b = mapper._make_id("03_Programming", "Sem 1", "x" * 100 + "beta")
        self.assertNotEqual(a, b)

    def test_assessment_and_viva_ids_do_not_truncate_at_thirty_characters(self) -> None:
        from pve.identity import sha256_identity

        prefix = "mod_" + "x" * 30
        for phase in ("assessment", "viva"):
            first = sha256_identity("artifact", {"module_id": prefix + "a", "phase": phase})
            second = sha256_identity("artifact", {"module_id": prefix + "b", "phase": phase})
            self.assertNotEqual(first, second)

    def test_pyq_regex_does_not_greedily_merge_questions(self) -> None:
        import ingestion_researcher

        text = (
            "Q1. Explain the complete Kalman prediction step? "
            "Q2. Explain the complete Kalman correction step?"
        )
        questions = ingestion_researcher.extract_pyqs(text)
        self.assertEqual(len(questions), 2)
        self.assertNotIn("Q2", questions[0])

    def test_corrupt_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(StateCorrupt):
                load_json_state(path, default_when_missing={})

    def test_safe_path_rejects_traversal(self) -> None:
        import ingestion_researcher

        self.assertIsNone(ingestion_researcher._safe_path("../../../../etc/passwd"))

    def test_admin_text_is_explicitly_excluded_from_prompt(self) -> None:
        self.assertIn("Administrative", mapper._SYSTEM)
        self.assertIn("SKIP", mapper._SYSTEM)
