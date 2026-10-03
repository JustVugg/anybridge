from unittest import TestCase

from anybridge.decomposition import (
    DecompositionObservation,
    decomposition_telemetry,
    recommend_decomposition,
)


class DecompositionTests(TestCase):
    def test_independent_work_stays_parallel(self):
        decision = recommend_decomposition(independent_work=4, max_parallelism=3)
        self.assertEqual(decision.strategy, "parallel_independent")
        self.assertEqual(decision.suggested_parallelism, 3)

    def test_repeated_failures_split_and_verify(self):
        decision = recommend_decomposition(
            independent_work=6,
            history=(
                DecompositionObservation(verification_failures=2, retries=1),
                DecompositionObservation(evidence_confidence=0.4),
            ),
            max_parallelism=6,
        )
        self.assertEqual(decision.strategy, "split_and_verify")
        self.assertLess(decision.suggested_parallelism, 6)
        self.assertEqual(decision.verification_depth, "deep")
        self.assertIn("verify_each_partition", decision.preconditions)

    def test_network_failures_isolate_dependencies(self):
        decision = recommend_decomposition(
            independent_work=4,
            history=(DecompositionObservation(network_failures=1),),
        )
        self.assertEqual(decision.strategy, "isolate_network_dependencies")
        self.assertIn("validate_network_policy", decision.preconditions)

    def test_high_risk_is_staged(self):
        decision = recommend_decomposition(
            independent_work=5,
            risk="high",
            max_parallelism=5,
        )
        self.assertEqual(decision.strategy, "stage_with_preconditions")
        self.assertEqual(decision.suggested_parallelism, 1)
        self.assertEqual(decision.escalation, "human_review")

    def test_malformed_history_is_ignored(self):
        decision = recommend_decomposition(
            independent_work=2,
            history=({"verification_failures": "bad"},),
        )
        self.assertEqual(decision.strategy, "parallel_independent")

    def test_telemetry_is_stable(self):
        decision = recommend_decomposition(independent_work=2)
        telemetry = decomposition_telemetry(decision, task_id="task-1", execution_id="exec-1")
        self.assertEqual(telemetry["task_id"], "task-1")
        self.assertEqual(telemetry["execution_id"], "exec-1")
        self.assertIn("preconditions", telemetry)


if __name__ == "__main__":
    import unittest
    unittest.main()
