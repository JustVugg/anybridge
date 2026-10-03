from unittest import TestCase

from anybridge.evidence import EvidenceEnvelope
from anybridge.remediation import (
    RemediationOutcome,
    correlate_failure,
    correlate_step_failure,
    propose_remediations,
    record_remediation_outcome,
    remediation_telemetry,
)


class RemediationTests(TestCase):
    def test_correlates_failure_to_step_and_network_dependency(self):
        evidence = EvidenceEnvelope(
            execution_id="exec-1",
            action="click",
            expected="Dashboard",
            observed=None,
            status="failed",
            network_errors=[{"hostname": "api.internal.example", "reason": "blocked"}],
            confidence=0.8,
        )
        failure = correlate_failure(
            evidence,
            failure_id="fail-1",
            step_index=3,
            step="When I click on Dashboard",
        )
        self.assertEqual(failure.failure_class, "network_dependency")
        self.assertEqual(failure.dependency, "api.internal.example")
        self.assertEqual(failure.step_index, 3)

    def test_step_failure_can_be_correlated_without_hws_or_provider(self):
        failure = correlate_step_failure(
            failure_id="fail-2",
            execution_id="exec-2",
            step_index=1,
            step="Then I should see Welcome",
            status="failed",
            action="assert",
            expected="Welcome",
            observed="Login",
        )
        self.assertEqual(failure.failure_class, "assertion")
        self.assertEqual(failure.step, "Then I should see Welcome")

    def test_proposes_safe_network_remediation(self):
        failure = correlate_step_failure(
            failure_id="fail-3",
            execution_id="exec-3",
            step_index=2,
            step="When I open the page",
            status="failed",
            action="navigate",
            dependency="api.internal.example",
        )
        actions = propose_remediations(failure)
        self.assertEqual(actions[0].action, "inspect_and_approve_dependency")
        self.assertIn("explicit_approval", actions[0].preconditions)

    def test_realized_success_feeds_next_cycle(self):
        outcome = RemediationOutcome(
            remediation_id="rem-fail-4",
            failure_id="fail-4",
            action="refresh_snapshot_and_reidentify",
            result="retry_passed",
            recovered=True,
            execution_id="exec-4",
            latency_ms=120,
            evidence_confidence=0.92,
            attempt=2,
        )
        feedback = record_remediation_outcome(
            outcome,
            provider="playwright",
            browser="chromium",
            task_class="checkout",
        )
        self.assertTrue(feedback.execution_observation["success"])
        self.assertEqual(feedback.execution_observation["provider"], "playwright")
        self.assertEqual(feedback.decomposition_observation["task_class"], "checkout")
        self.assertEqual(feedback.execution_observation["remediation_id"], "rem-fail-4")
        self.assertEqual(feedback.decomposition_observation["recovered"], True)

    def test_realized_failure_increases_next_cycle_signal(self):
        outcome = RemediationOutcome(
            remediation_id="rem-fail-5",
            failure_id="fail-5",
            action="retry_with_deeper_verification",
            result="timeout",
            recovered=False,
            attempt=2,
            retries=1,
            verification_failures=1,
            evidence_confidence=0.3,
        )
        feedback = record_remediation_outcome(outcome)
        self.assertFalse(feedback.execution_observation["success"])
        self.assertGreaterEqual(feedback.execution_observation["verification_failures"], 1)
        self.assertGreaterEqual(feedback.decomposition_observation["retries"], 1)
        self.assertEqual(feedback.decomposition_observation["evidence_confidence"], 0.3)

    def test_full_chain_is_serializable(self):
        failure = correlate_step_failure(
            failure_id="fail-6",
            execution_id="exec-6",
            step_index=4,
            step="Then I should see Order",
            status="failed",
            action="assert",
        )
        actions = propose_remediations(failure)
        payload = remediation_telemetry(failure, actions)
        self.assertEqual(payload["failure"]["failure_id"], "fail-6")
        self.assertEqual(payload["actions"][0]["failure_id"], "fail-6")


if __name__ == "__main__":
    import unittest
    unittest.main()
