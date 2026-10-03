from unittest import TestCase

from anybridge.adaptive_fabric import (
    ResourceObservation,
    decide_execution_adaptive,
    decision_telemetry,
)
from anybridge.execution_fabric import ExecutionObservation


class AdaptiveFabricTests(TestCase):
    def test_resource_pressure_forces_serial(self):
        decision, alternatives = decide_execution_adaptive(
            available=("playwright",),
            independent_work=3,
            resources=ResourceObservation(
                cpu_available=0.10,
                memory_available_mb=4096,
                concurrency_capacity=4,
            ),
        )
        self.assertEqual(decision.execution_mode, "serial")
        self.assertTrue(any(p.name == "parallel_execution" for p in alternatives))

    def test_capacity_allows_parallel(self):
        decision, _ = decide_execution_adaptive(
            available=("playwright",),
            independent_work=3,
            resources=ResourceObservation(
                cpu_available=0.80,
                memory_available_mb=4096,
                queue_depth=0,
                concurrency_capacity=4,
            ),
        )
        self.assertEqual(decision.execution_mode, "parallel")

    def test_high_risk_remains_serial(self):
        decision, _ = decide_execution_adaptive(
            available=("playwright",),
            independent_work=3,
            risk="high",
            resources=ResourceObservation(cpu_available=1.0, concurrency_capacity=8),
        )
        self.assertEqual(decision.execution_mode, "serial")
        self.assertIn("safety gate", " ".join(decision.reasons))

    def test_counterfactuals_include_verification_and_provider_fallback(self):
        decision, alternatives = decide_execution_adaptive(
            available=("playwright", "selenium"),
            independent_work=1,
            history=(
                ExecutionObservation(
                    provider="playwright",
                    success=False,
                    evidence_confidence=0.4,
                    verification_failures=2,
                    retries=1,
                ),
            ),
        )
        names = {p.name for p in alternatives}
        self.assertEqual(decision.verification_depth, "deep")
        self.assertIn("alternate_provider", names)
        alternate = next(p for p in alternatives if p.name == "alternate_provider")
        self.assertNotEqual(alternate.decision.provider, decision.provider)

    def test_telemetry_is_hws_friendly(self):
        decision, _ = decide_execution_adaptive(available=("playwright",))
        telemetry = decision_telemetry(
            decision,
            execution_id="exec-1",
            test_id="test-1",
            resources=ResourceObservation(context_budget=5000, evidence_value=0.9),
        )
        self.assertEqual(telemetry["execution_id"], "exec-1")
        self.assertEqual(telemetry["test_id"], "test-1")
        self.assertEqual(telemetry["provider"], "playwright")
        self.assertIn("resources", telemetry)
        self.assertEqual(telemetry["resources"]["context_budget"], 5000)


if __name__ == "__main__":
    import unittest

    unittest.main()
