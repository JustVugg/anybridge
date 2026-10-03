from unittest import TestCase
from anybridge.self_healing import (
    ExecutionJournal, TargetFingerprint, decide_target_recovery, rank_target_candidates,
)


class SelfHealingTests(TestCase):
    def test_reidentifies_high_confidence_target(self):
        expected = TargetFingerprint(role="button", name="save", text="Save")
        decision = decide_target_recovery(
            expected,
            [
                ("@e7", TargetFingerprint(role="button", name="save", text="Save")),
                ("@e9", TargetFingerprint(role="button", name="cancel", text="Cancel")),
            ],
        )
        self.assertEqual(decision.action, "retry_with_reidentified_target")
        self.assertEqual(decision.target_ref, "@e7")
        self.assertTrue(decision.requires_verification)

    def test_ambiguous_target_never_autoselects(self):
        expected = TargetFingerprint(role="button", text="Submit")
        decision = decide_target_recovery(
            expected,
            [
                ("@a", TargetFingerprint(role="button", text="Submit")),
                ("@b", TargetFingerprint(role="button", text="Submit")),
            ],
        )
        self.assertEqual(decision.action, "refresh_snapshot")
        self.assertIsNone(decision.target_ref)

    def test_candidate_ranking_is_deterministic(self):
        expected = TargetFingerprint(role="button", name="save")
        ranked = rank_target_candidates(
            expected,
            [
                ("@z", TargetFingerprint(role="button", name="save")),
                ("@a", TargetFingerprint(role="button", name="save")),
            ],
        )
        self.assertEqual([x.ref for x in ranked], ["@a", "@z"])

    def test_journal_is_bounded_and_recoverable(self):
        journal = ExecutionJournal(max_events=2)
        journal.append(event_id="1", execution_id="e", kind="action")
        journal.append(event_id="2", execution_id="e", kind="failure")
        journal.append(event_id="3", execution_id="e", kind="remediation", status="recovered")
        self.assertEqual([x["event_id"] for x in journal.export("e")], ["2", "3"])
        self.assertEqual(journal.last("e").event_id, "3")


if __name__ == "__main__":
    import unittest
    unittest.main()
