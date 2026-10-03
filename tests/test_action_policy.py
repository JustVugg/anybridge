from unittest import TestCase
from anybridge.action_policy import ActionRisk, RetryPolicy, assess_action, assess_sequence


class ActionPolicyTests(TestCase):
    def test_reads_are_automatically_retryable(self):
        result = assess_action("snapshot")
        self.assertEqual(result.risk, ActionRisk.READ)
        self.assertEqual(result.policy, RetryPolicy.AUTOMATIC)
        self.assertFalse(result.requires_confirmation)

    def test_navigation_is_bounded(self):
        result = assess_action("navigate")
        self.assertEqual(result.risk, ActionRisk.NAVIGATE)
        self.assertEqual(result.max_retries, 2)

    def test_mutation_requires_revalidation(self):
        result = assess_action("fill_ref")
        self.assertEqual(result.risk, ActionRisk.MUTATE)
        self.assertEqual(result.policy, RetryPolicy.REVALIDATE)

    def test_consequential_target_requires_confirmation(self):
        result = assess_action("click", target="Delete account")
        self.assertEqual(result.risk, ActionRisk.CONSEQUENTIAL)
        self.assertEqual(result.policy, RetryPolicy.CONFIRM)
        self.assertTrue(result.requires_confirmation)
        self.assertEqual(result.max_retries, 0)

    def test_unknown_defaults_to_revalidation(self):
        result = assess_action("future_tool")
        self.assertEqual(result.risk, ActionRisk.UNKNOWN)
        self.assertEqual(result.policy, RetryPolicy.REVALIDATE)

    def test_sequence_is_deterministic(self):
        result = assess_sequence(["snapshot", "navigate", "fill_ref"])
        self.assertEqual([x.risk for x in result], [ActionRisk.READ, ActionRisk.NAVIGATE, ActionRisk.MUTATE])


if (1):
    import unittest
    unittest.main()
