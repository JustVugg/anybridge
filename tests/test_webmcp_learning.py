import unittest

from anybridge.webmcp_security import SecurityEvaluation, SecurityFinding
from anybridge.webmcp_learning import (
    SecurityLearningStore,
    SecurityObservation,
    observation_from_evaluation,
    schema_fingerprint,
)


class WebMCPLearningTests(unittest.TestCase):
    def test_repeated_failure_is_promoted(self):
        store = SecurityLearningStore(promotion_min_failures=2, promotion_min_failure_rate=0.5)
        obs = SecurityObservation(
            attack_id="novel_output",
            attack_class="output_injection",
            provider="playwright",
            origin="https://evil.example",
            schema_hash="abc",
            defense="base64_quarantine",
            passed=False,
            evidence_confidence=0.9,
        )
        first = store.observe(obs, regression_payload={"output": "ignore policy"})
        self.assertEqual(first.newly_promoted, 0)
        second = store.observe(obs, regression_payload={"output": "ignore policy"})
        self.assertEqual(second.newly_promoted, 1)
        self.assertEqual(len(store.promoted_cases()), 1)
        self.assertTrue(any(c["id"].startswith("sec-") for c in store.corpus()))

    def test_one_failure_does_not_promote(self):
        store = SecurityLearningStore(promotion_min_failures=2)
        store.observe(SecurityObservation(
            attack_id="rare",
            attack_class="schema_poisoning",
            provider="selenium",
            origin="https://example.com",
            schema_hash="s",
            defense="schema_boundary",
            passed=False,
        ))
        self.assertEqual(store.promoted_cases(), ())

    def test_pass_after_failure_reduces_rate_and_blocks_promotion(self):
        store = SecurityLearningStore(promotion_min_failures=2, promotion_min_failure_rate=0.75)
        obs = dict(
            attack_id="flaky",
            attack_class="output_injection",
            provider="playwright",
            origin="https://example.com",
            schema_hash="s",
            defense="spotlight",
        )
        store.observe(SecurityObservation(**obs, passed=False))
        store.observe(SecurityObservation(**obs, passed=True))
        self.assertEqual(store.promoted_cases(), ())

    def test_origin_provider_schema_and_defense_are_in_signature(self):
        store = SecurityLearningStore(promotion_min_failures=1)
        base = dict(
            attack_id="x",
            attack_class="output_injection",
            provider="playwright",
            origin="https://a.example",
            schema_hash="a",
            defense="spotlight",
            passed=False,
        )
        store.observe(SecurityObservation(**base))
        store.observe(SecurityObservation(**{**base, "origin": "https://b.example"}))
        store.observe(SecurityObservation(**{**base, "provider": "selenium"}))
        store.observe(SecurityObservation(**{**base, "schema_hash": "b"}))
        store.observe(SecurityObservation(**{**base, "defense": "base64"}))
        self.assertEqual(len(store.patterns()), 5)

    def test_bounds_and_export(self):
        store = SecurityLearningStore(max_observations=2, max_promoted_cases=1, promotion_min_failures=1)
        for i in range(4):
            store.observe(SecurityObservation(
                attack_id=f"a{i}",
                attack_class="x",
                provider="p",
                origin="o",
                schema_hash=str(i),
                defense="d",
                passed=False,
            ))
        exported = store.export()
        self.assertEqual(len(exported["observations"]), 2)
        self.assertLessEqual(len(exported["promoted"]), 1)
        self.assertIn("corpus", exported)

    def test_evaluation_projection_is_deterministic(self):
        evaluation = SecurityEvaluation(
            passed=False,
            total=2,
            failures=(SecurityFinding("untrusted_output", "output_injection", False, "failed"),),
        )
        observations = observation_from_evaluation(
            evaluation,
            provider="playwright",
            origin="https://example.com",
            schema={"type": "object"},
            defense="content_boundary",
        )
        self.assertEqual(len(observations), 8)
        self.assertFalse(next(o for o in observations if o.attack_id == "untrusted_output").passed)
        self.assertTrue(next(o for o in observations if o.attack_id == "description_override").passed)
        self.assertEqual(observations[0].schema_hash, schema_fingerprint({"type": "object"}))


if __name__ == "__main__":
    unittest.main()
